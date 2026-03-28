"""Code to handle the Plenticore API."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Mapping
from datetime import timedelta
import logging
import random
import time
from typing import Any, Final, TYPE_CHECKING, TypeVar, cast
import asyncio

from aiohttp.client_exceptions import ClientError
from pykoplenti import (
    ApiClient,
    ApiException,
    AuthenticationException,
    EventData,
    ExtendedApiClient,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import ADVANCED_WRITE_ARM_TTL_SECONDS, CONF_SERVICE_CODE, DOMAIN
from .repairs import (
    clear_issue,
    create_api_unreachable_issue,
    create_auth_failed_issue,
    create_inverter_busy_issue,
)
from .helper import (
    ModbusIllegalDataAddressError,
    ModbusIllegalDataValueError,
    ModbusServerDeviceBusyError,
    ModbusServerDeviceFailureError,
    ModbusMemoryParityError,
    ModbusException,
    get_hostname_id,
    is_allowed_write_target,
    is_rest_write_supported_target,
    parse_modbus_exception,
    requires_advanced_write_arm,
    validate_cross_field_write_rules,
)

_LOGGER = logging.getLogger(__name__)

EVENT_HISTORY_MAX: int = 50
EVENT_DEDUP_COOLDOWN_SECONDS: int = 300
EVENT_UPDATE_INTERVAL_SECONDS: int = 30
SETUP_FETCH_TIMEOUT_SECONDS: float = 5.0
SETUP_PREWARM_TIMEOUT_SECONDS: float = 5.0
DEFAULT_AVAILABLE_MODULES: Final[list[str]] = [
    "devices:local",
    "scb:statistic:EnergyFlow",
    "scb:event",
    "scb:system",
    "scb:network",
]

# Type variables for generic classes
T = TypeVar("T")



# Type alias for config entry with runtime data
# Forward reference: Plenticore class is defined below
if TYPE_CHECKING:  # pragma: no cover
    PlenticoreConfigEntry = ConfigEntry["Plenticore"]
else:
    PlenticoreConfigEntry = ConfigEntry


class Plenticore:
    """Manages the Plenticore API."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Create a new plenticore manager instance."""
        self.hass = hass
        self.config_entry = config_entry
        self._request_scheduler: Any = None

        self._client: ApiClient | None = None
        self._shutdown_remove_listener: CALLBACK_TYPE | None = None

        self.device_info = DeviceInfo(
            configuration_url=f"http://{self.host}",
            identifiers={(DOMAIN, "unknown")},
            manufacturer="Kostal",
            name="Kostal Plenticore",
            model="Unknown",
            sw_version="Unknown",
        )
        self.available_modules: list[str] = list(DEFAULT_AVAILABLE_MODULES)
        self._settings_cache: Mapping[str, Any] | None = None
        self._settings_cache_ts: float = 0.0
        self._process_data_cache: Mapping[str, Any] | None = None
        self._process_data_cache_ts: float = 0.0
        self._advanced_write_armed_until: float = 0.0

    @property
    def host(self) -> str:
        """Return the host of the Plenticore inverter."""
        return cast(str, self.config_entry.data[CONF_HOST])

    @property
    def client(self) -> ApiClient:
        """Return the Plenticore API client."""
        return cast(ApiClient, self._client)

    def arm_advanced_writes(
        self, ttl_seconds: int = ADVANCED_WRITE_ARM_TTL_SECONDS
    ) -> None:
        """Arm high-impact writes for a short time window."""
        ttl = max(10, int(ttl_seconds))
        self._advanced_write_armed_until = time.monotonic() + ttl

    def disarm_advanced_writes(self) -> None:
        """Immediately disable high-impact writes."""
        self._advanced_write_armed_until = 0.0

    @property
    def is_advanced_write_armed(self) -> bool:
        """Return whether high-impact writes are currently armed."""
        return time.monotonic() < self._advanced_write_armed_until

    @property
    def advanced_write_arm_seconds_left(self) -> int:
        """Return remaining arm window in seconds."""
        return max(0, int(self._advanced_write_armed_until - time.monotonic()))

    async def async_get_settings_cached(
        self, ttl_seconds: float = 120.0
    ) -> Mapping[str, Any]:
        """Return cached settings metadata with short TTL."""
        if self._client is None:
            return {}
        now = time.monotonic()
        if self._settings_cache is not None and (now - self._settings_cache_ts) < ttl_seconds:
            return self._settings_cache
        data = cast(Mapping[str, Any], await self._client.get_settings())
        self._settings_cache = data
        self._settings_cache_ts = now
        return data

    async def async_get_process_data_cached(
        self, ttl_seconds: float = 60.0
    ) -> Mapping[str, Any]:
        """Return cached process-data-id map with short TTL."""
        if self._client is None:
            return {}
        now = time.monotonic()
        if self._process_data_cache is not None and (now - self._process_data_cache_ts) < ttl_seconds:
            return self._process_data_cache
        data = cast(Mapping[str, Any], await self._client.get_process_data())
        self._process_data_cache = data
        self._process_data_cache_ts = now
        return data

    def invalidate_capability_cache(self) -> None:
        """Clear cached process/settings capability maps."""
        self._settings_cache = None
        self._settings_cache_ts = 0.0
        self._process_data_cache = None
        self._process_data_cache_ts = 0.0

    async def async_setup(self) -> bool:
        """Set up Plenticore API client."""
        session = async_get_clientsession(self.hass)
        if self._request_scheduler is not None:
            from .scheduled_session import ScheduledSession
            session = ScheduledSession(session, self._request_scheduler)  # type: ignore[assignment]
        self._client = ExtendedApiClient(session, host=self.host)  # pyright: ignore[reportArgumentType]
        
        try:
            await self._client.login(
                self.config_entry.data[CONF_PASSWORD],
                service_code=self.config_entry.data.get(CONF_SERVICE_CODE),
            )
        except AuthenticationException as err:
            _LOGGER.error(
                "Authentication exception connecting to %s: %s", self.host, err
            )
            create_auth_failed_issue(self.hass)
            return False
        except (ClientError, TimeoutError) as err:
            _LOGGER.error("Error connecting to %s", self.host)
            create_api_unreachable_issue(self.hass)
            raise ConfigEntryNotReady from err
        except ApiException as err:
            modbus_err = parse_modbus_exception(err)
            _LOGGER.error("API error during login to %s: %s", self.host, modbus_err.message)
            create_api_unreachable_issue(self.hass)
            raise ConfigEntryNotReady from err
        
        _LOGGER.debug("Log-in successfully to %s", self.host)

        self._shutdown_remove_listener = self.hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, self._async_shutdown
        )

        # Concurrently fetch modules and device metadata for better performance
        try:
            modules_task = self._fetch_modules()
            metadata_task = self._fetch_device_metadata()

            # Run both operations concurrently
            modules_result, metadata_result = await asyncio.gather(
                asyncio.wait_for(modules_task, timeout=SETUP_FETCH_TIMEOUT_SECONDS),
                asyncio.wait_for(metadata_task, timeout=SETUP_FETCH_TIMEOUT_SECONDS),
                return_exceptions=True,
            )

            # Handle results
            if isinstance(modules_result, asyncio.TimeoutError):
                self.available_modules = list(DEFAULT_AVAILABLE_MODULES)
                _LOGGER.warning(
                    "Module discovery timed out after %.1fs, continuing with defaults",
                    SETUP_FETCH_TIMEOUT_SECONDS,
                )
            elif isinstance(modules_result, Exception):
                raise modules_result
            if isinstance(metadata_result, asyncio.TimeoutError):
                self._set_default_device_info()
                _LOGGER.warning(
                    "Device metadata fetch timed out after %.1fs, continuing with defaults",
                    SETUP_FETCH_TIMEOUT_SECONDS,
                )
            elif isinstance(metadata_result, Exception):
                raise metadata_result

            # Prime capability caches once to reduce duplicate startup probes
            # across sensor/number/select platforms.
            prewarm_results = await asyncio.gather(
                asyncio.wait_for(
                    self.async_get_process_data_cached(ttl_seconds=0.0),
                    timeout=SETUP_PREWARM_TIMEOUT_SECONDS,
                ),
                asyncio.wait_for(
                    self.async_get_settings_cached(ttl_seconds=0.0),
                    timeout=SETUP_PREWARM_TIMEOUT_SECONDS,
                ),
                return_exceptions=True,
            )
            for prewarm_result in prewarm_results:
                if isinstance(prewarm_result, Exception):
                    _LOGGER.debug(
                        "Capability cache prewarm failed (non-fatal): %s",
                        prewarm_result,
                    )

            return True

        except (ApiException, KeyError, ValueError) as err:
            if isinstance(err, ApiException):
                modbus_err = parse_modbus_exception(err)
                _LOGGER.error("Could not get device metadata: %s", modbus_err.message)
            else:
                _LOGGER.error("Error processing device metadata: %s", err)
            return False

    def _set_default_device_info(self) -> None:
        """Set conservative fallback device metadata."""
        self.device_info = DeviceInfo(
            configuration_url=f"http://{self.host}",
            identifiers={(DOMAIN, "unknown")},
            manufacturer="Kostal",
            model="Unknown",
            name=self.host,
        )

    async def _fetch_modules(self) -> None:
        """Fetch available modules concurrently."""
        if self._client is None:
            return
        try:
            modules = await self._client.get_modules()
            self.available_modules = [m.id for m in modules]
            _LOGGER.debug("Available modules: %s", self.available_modules)
        except (ApiException, ClientError, TimeoutError) as err:
            _LOGGER.warning("Could not get available modules: %s. Using default assumption.", err)
            # Fallback to defaults if modules can't be fetched (older firmware?)
            self.available_modules = list(DEFAULT_AVAILABLE_MODULES)

    async def _fetch_device_metadata(self) -> None:
        """Fetch device metadata concurrently."""
        if self._client is None:
            return
        try:
            hostname_id = await get_hostname_id(self._client)
            settings = await self._client.get_setting_values(
                {
                    "devices:local": [
                        "Properties:SerialNo",
                        "Branding:ProductName1",
                        "Branding:ProductName2",
                        "Properties:VersionIOC",
                        "Properties:VersionMC",
                    ],
                    "scb:network": [hostname_id],
                }
            )
        except (ApiException, ClientError, TimeoutError, KeyError) as err:
            _LOGGER.error("Could not fetch device metadata: %s", err)
            # Set default device info to prevent setup failure
            self._set_default_device_info()
            return

        # Safe dictionary access with defaults
        device_local = settings.get("devices:local", {})
        prod1 = device_local.get("Branding:ProductName1", "Unknown")
        prod2 = device_local.get("Branding:ProductName2", "")
        serial_no = device_local.get("Properties:SerialNo", "unknown")
        version_ioc = device_local.get("Properties:VersionIOC", "unknown")
        version_mc = device_local.get("Properties:VersionMC", "unknown")

        network_settings = settings.get("scb:network", {})
        hostname = network_settings.get(hostname_id, self.host)

        self.device_info = DeviceInfo(
            configuration_url=f"http://{self.host}",
            identifiers={(DOMAIN, serial_no)},
            manufacturer="Kostal",
            model=f"{prod1} {prod2}".strip() or "Unknown",
            name=hostname,
            sw_version=(
                f"IOC: {version_ioc} MC: {version_mc}"
            ),
        )

    async def _async_shutdown(self, event: Any) -> None:
        """Call from Homeassistant shutdown event."""
        # unset remove listener otherwise calling it would raise an exception
        self._shutdown_remove_listener = None
        await self.async_unload()

    async def async_unload(self) -> None:
        """Unload the Plenticore API client."""
        remove_listener = self._shutdown_remove_listener
        self._shutdown_remove_listener = None
        if remove_listener:
            remove_listener()

        # CHANGELOG (Codex, 2026-02-05):
        # Fix review finding #3 by removing the fragile hass.state string check.
        # If this unload call originates from HA shutdown, _async_shutdown()
        # already cleared the listener before calling async_unload(), so
        # remove_listener is None and logout is skipped.
        try:
            if self._client and remove_listener is not None:
                await asyncio.wait_for(
                    self._client.logout(),  # type: ignore[no-untyped-call]
                    timeout=5.0  # Add timeout to prevent hanging
                )
                _LOGGER.debug("Logged out from %s", self.host)
            else:
                _LOGGER.debug("Skipping logout during shutdown")
        except (ApiException, ClientError, TimeoutError, asyncio.TimeoutError) as err:
            _LOGGER.debug("Error during logout from %s: %s", self.host, err)
        finally:
            self._client = None


class DataUpdateCoordinatorMixin:
    """Base implementation for read and write data."""

    _plenticore: Plenticore
    name: str

    async def async_read_data(
        self, module_id: str, data_id: str
    ) -> Mapping[str, Mapping[str, str]] | None:
        """Read data from Plenticore."""
        if (client := self._plenticore.client) is None:
            return None

        try:
            data = await client.get_setting_values(module_id, data_id)
            # CHANGELOG (Codex, 2026-02-05):
            # Auto-clear transient inverter_busy issue on successful recovery.
            if (hass := getattr(self._plenticore, "hass", None)) is not None:
                clear_issue(hass, "inverter_busy")
            return data
        except (ApiException, ClientError, TimeoutError) as err:
            # Parse into specific MODBUS exceptions for better error handling
            error_msg = str(err)
            
            # Handle 404 errors (module/setting not found) gracefully
            if "module or setting not found" in error_msg.lower() or "[404]" in error_msg:
                 _LOGGER.warning(
                     "Setting %s:%s not found on this inverter (404), skipping",
                     module_id,
                     data_id,
                 )
                 return None

            # Handle 503 errors (internal communication error)
            if "internal communication error" in error_msg.lower() or "[503]" in error_msg:
                 create_inverter_busy_issue(self._plenticore.hass)
                 _LOGGER.warning("Inverter internal communication error (503) reading %s:%s - retrying later", module_id, data_id)
                 return None

            if isinstance(err, ApiException):
                modbus_err = parse_modbus_exception(err)
                _LOGGER.error("MODBUS error reading %s:%s - %s", module_id, data_id, modbus_err.message)
            elif "Unknown API response [500]" in error_msg:
                # Downgrade 500 errors to warning as they often indicate unsupported features
                _LOGGER.warning("Inverter API returned 500 error reading %s:%s - feature likely not supported", module_id, data_id)
            elif "Missing data_id" in error_msg:
                _LOGGER.debug("Missing data_id %s in module %s", data_id, module_id)
            else:
                _LOGGER.error("Error reading %s:%s - %s", module_id, data_id, err)
            return None

    async def async_write_data(self, module_id: str, value: dict[str, str]) -> bool:
        """Write settings back to Plenticore."""
        if (client := self._plenticore.client) is None:
            return False

        _LOGGER.debug(
            "Setting value for %s in module %s to %s", self.name, module_id, value
        )

        # Security: explicit write allowlist
        for data_id in value:
            if not is_rest_write_supported_target(data_id):
                raise HomeAssistantError(
                    f"REST write disabled for {data_id}. Use Modbus battery controls instead."
                )
            if not is_allowed_write_target(module_id, data_id):
                raise HomeAssistantError(
                    f"Write to unsupported target blocked: {module_id}/{data_id}"
                )

        # Security: temporary arming for high-impact controls
        strict_verify = any(requires_advanced_write_arm(data_id) for data_id in value)
        if strict_verify:
            if not self._plenticore.is_advanced_write_armed:
                raise HomeAssistantError(
                    "High-impact control is locked. Arm advanced writes first."
                )

        get_setting_values = getattr(client, "get_setting_values", None)
        supports_readback = callable(get_setting_values)
        if strict_verify and not supports_readback:
            raise HomeAssistantError(
                "High-impact write requires readback verification, but API readback is unavailable."
            )

        # Pre-read for cross-field validation and change baseline.
        current_module_values: dict[str, str] = {}
        read_ids = set(value.keys())
        for data_id in value:
            if data_id.endswith("OnPowerThreshold"):
                read_ids.add(data_id.replace("OnPowerThreshold", "OffPowerThreshold"))
            elif data_id.endswith("OffPowerThreshold"):
                read_ids.add(data_id.replace("OffPowerThreshold", "OnPowerThreshold"))

        if get_setting_values is not None and supports_readback:
            try:
                current_response = await get_setting_values({module_id: list(read_ids)})
                current_module_values = dict(current_response.get(module_id, {}))
            except Exception as pre_read_err:
                _LOGGER.debug(
                    "Pre-read before write failed for %s (%s): %s",
                    module_id,
                    list(read_ids),
                    pre_read_err,
                )

        for data_id, raw_new_value in value.items():
            cross_field_error = validate_cross_field_write_rules(
                data_id,
                str(raw_new_value),
                current_module_values,
            )
            if cross_field_error:
                raise HomeAssistantError(cross_field_error)

        try:
            await client.set_setting_values(module_id, value)
        except (ApiException, ClientError, TimeoutError) as err:
            # Parse into specific MODBUS exceptions for better error handling
            error_msg = str(err)
            if isinstance(err, ApiException):
                modbus_err = parse_modbus_exception(err)
                _LOGGER.error("MODBUS error writing %s:%s - %s", module_id, value, modbus_err.message)
                
                # For certain errors, we might want to retry
                if isinstance(modbus_err, ModbusServerDeviceBusyError):
                    _LOGGER.warning("Inverter busy, consider retrying operation")
                elif isinstance(modbus_err, ModbusIllegalDataValueError):
                    _LOGGER.error("Invalid value provided, check value ranges")
                elif isinstance(modbus_err, ModbusIllegalDataAddressError):
                    _LOGGER.error("Invalid register address, check inverter model compatibility")
            elif "Unknown API response [500]" in error_msg:
                _LOGGER.error("Inverter API returned 500 error when writing %s:%s - feature not supported", module_id, value)
            else:
                _LOGGER.error("Error writing %s:%s - %s", module_id, value, err)
            
            # Raise translated exception to ensure UI feedback
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="write_setting_failed",
                translation_placeholders={"error": error_msg},
            ) from err

        mismatches: list[str] = []
        if get_setting_values is not None and supports_readback:
            verify_response: dict[str, Any] | None
            try:
                verify_response = await get_setting_values({module_id: list(value.keys())})
            except (ApiException, ClientError, TimeoutError) as verify_err:
                verify_msg = str(verify_err)
                if strict_verify:
                    raise HomeAssistantError(
                        "Write verification read failed after successful write; "
                        f"setting may already be applied ({verify_msg})"
                    ) from verify_err
                _LOGGER.warning(
                    "Verification read failed after write to %s/%s: %s",
                    module_id,
                    list(value.keys()),
                    verify_msg,
                )
                verify_response = None
            except Exception as verify_err:
                if strict_verify:
                    raise HomeAssistantError(
                        "Write verification failed after successful write; "
                        f"setting may already be applied ({verify_err})"
                    ) from verify_err
                _LOGGER.warning(
                    "Unexpected verification error after write to %s/%s: %s",
                    module_id,
                    list(value.keys()),
                    verify_err,
                )
                verify_response = None

            if verify_response is not None:
                verify_values = dict(verify_response.get(module_id, {}))
                for data_id, expected in value.items():
                    if data_id not in verify_values:
                        mismatches.append(f"{data_id}: missing in readback")
                        continue
                    actual = str(verify_values[data_id])
                    expected_s = str(expected)
                    try:
                        expected_f = float(expected_s)
                        actual_f = float(actual)
                        if abs(expected_f - actual_f) > 1e-3:
                            mismatches.append(
                                f"{data_id}: expected {expected_s}, got {actual}"
                            )
                    except (TypeError, ValueError):
                        if actual != expected_s:
                            mismatches.append(
                                f"{data_id}: expected {expected_s}, got {actual}"
                            )

                if mismatches and strict_verify:
                    raise HomeAssistantError(
                        "Write verification failed: " + "; ".join(mismatches)
                    )
                if mismatches and not strict_verify:
                    _LOGGER.debug(
                        "Non-strict write verification mismatch for %s/%s: %s",
                        module_id,
                        list(value.keys()),
                        mismatches,
                    )

        # CHANGELOG (Codex, 2026-02-05):
        # Successful write confirms recovery from prior inverter_busy state.
        if (hass := getattr(self._plenticore, "hass", None)) is not None:
            clear_issue(hass, "inverter_busy")

        return True


_DataT = TypeVar("_DataT")


class PlenticoreUpdateCoordinator(DataUpdateCoordinator[_DataT]):
    """Base coordinator for Plenticore data."""

    config_entry: PlenticoreConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: PlenticoreConfigEntry,
        logger: logging.Logger,
        name: str,
        update_interval: timedelta,
        plenticore: Plenticore,
    ) -> None:
        """Create a new update coordinator for plenticore data."""
        super().__init__(
            hass=hass,
            logger=logger,
            config_entry=config_entry,
            name=name,
            update_interval=update_interval,
        )
        # data ids to poll
        self._fetch: dict[str, list[str]] = defaultdict(list)
        self._plenticore = plenticore

    def start_fetch_data(self, module_id: str, data_id: str) -> CALLBACK_TYPE:
        """Start fetching the given data (module-id and data-id).

        Args:
            module_id: Plenticore module identifier (e.g., "devices:local")
            data_id: Data identifier within the module (e.g., "P")

        Returns:
            Callback function to cancel the scheduled refresh.
        """
        if module_id in self._fetch and data_id in self._fetch[module_id]:
            _LOGGER.debug("Data %s/%s already being fetched, skipping duplicate", module_id, data_id)
            # Return a cleanup callback that removes the data_id when the
            # entity is removed -- even for duplicates.
            def _stop() -> None:
                self.stop_fetch_data(module_id, data_id)
            return _stop

        self._fetch[module_id].append(data_id)
        _LOGGER.debug(
            "Coordinator %s: Registered %s/%s for fetching",
            self.name, module_id, data_id,
        )

        # Force an update of all data. Multiple refresh calls
        # are ignored by the debouncer.
        async def force_refresh(event_time: Any) -> None:
            try:
                await self.async_request_refresh()
            except Exception as err:
                _LOGGER.debug("Deferred refresh failed for %s: %s", self.name, err)

        return async_call_later(self.hass, 0.5, force_refresh)

    def stop_fetch_data(self, module_id: str, data_id: str) -> None:
        """Stop fetching the given data (module-id and data-id)."""
        if module_id in self._fetch and data_id in self._fetch[module_id]:
            try:
                self._fetch[module_id].remove(data_id)
            except ValueError:
                # Data ID already removed, ignore error
                pass


class AdaptivePollingCoordinatorMixin:
    """Shared adaptive interval behavior for API coordinators."""

    update_interval: timedelta | None
    _base_update_interval: timedelta
    _max_update_interval: timedelta
    _consecutive_failures: int
    _failure_multiplier_cap: int

    def _init_adaptive_polling(
        self,
        *,
        default_base_seconds: int,
        max_interval_floor_seconds: int,
        max_interval_multiplier: int,
        failure_multiplier_cap: int,
    ) -> None:
        base_interval = self.update_interval or timedelta(seconds=default_base_seconds)
        self._base_update_interval = base_interval
        self._max_update_interval = timedelta(
            seconds=max(
                max_interval_floor_seconds,
                int(base_interval.total_seconds() * max_interval_multiplier),
            )
        )
        self._consecutive_failures = 0
        self._failure_multiplier_cap = max(1, int(failure_multiplier_cap))

    def _apply_adaptive_interval(self, multiplier: float) -> None:
        """Apply bounded adaptive interval with light jitter."""
        base_seconds = max(1.0, self._base_update_interval.total_seconds())
        next_seconds = min(
            self._max_update_interval.total_seconds(), base_seconds * multiplier
        )
        # Jitter prevents synchronized poll bursts across instances.
        next_seconds = max(1.0, next_seconds * random.uniform(0.9, 1.1))
        self.update_interval = timedelta(seconds=next_seconds)

    def _record_success(self) -> None:
        self._consecutive_failures = 0
        self.update_interval = self._base_update_interval

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        self._apply_adaptive_interval(
            1 + min(self._failure_multiplier_cap, self._consecutive_failures)
        )


class ProcessDataUpdateCoordinator(
    AdaptivePollingCoordinatorMixin,
    PlenticoreUpdateCoordinator[Mapping[str, Mapping[str, str]]],
):
    """Implementation of PlenticoreUpdateCoordinator for process data."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._last_result: dict[str, dict[str, str]] = {}
        self._init_adaptive_polling(
            default_base_seconds=10,
            max_interval_floor_seconds=120,
            max_interval_multiplier=6,
            failure_multiplier_cap=3,
        )

    async def _async_update_data(self) -> dict[str, dict[str, str]]:
        client = self._plenticore.client

        if not self._fetch or client is None:
            return {}

        _LOGGER.debug("Fetching %s for %s", self.name, self._fetch)

        try:
            fetched_data = await asyncio.wait_for(
                client.get_process_data_values(self._fetch),
                timeout=30.0
            )
            # CHANGELOG (Codex, 2026-02-05):
            # Auto-clear inverter_busy after a successful process-data roundtrip.
            if (hass := getattr(self._plenticore, "hass", None)) is not None:
                clear_issue(hass, "inverter_busy")
            self._record_success()
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout fetching process data for %s", self.name)
            if self._last_result:
                _LOGGER.warning(
                    "Timeout fetching process data for %s - using last known values",
                    self.name,
                )
                # Soft backoff while keeping entities responsive.
                self._apply_adaptive_interval(1.5)
                return self._last_result
            self._record_failure()
            raise UpdateFailed("Timeout fetching process data") from None
        except (ApiException, ClientError, TimeoutError) as err:
            error_msg = str(err)
            if self._last_result:
                if "internal communication error" in error_msg.lower() or "[503]" in error_msg:
                    _LOGGER.warning(
                        "Inverter internal communication error (503) fetching process data - "
                        "using last known values"
                    )
                else:
                    _LOGGER.warning(
                        "Process data fetch failed for %s - using last known values: %s",
                        self.name,
                        error_msg,
                    )
                # Soft backoff while keeping entities responsive.
                self._apply_adaptive_interval(1.5)
                return self._last_result
            self._record_failure()

            # Handle 503 errors (internal communication error)
            if "internal communication error" in error_msg.lower() or "[503]" in error_msg:
                _LOGGER.warning("Inverter internal communication error (503) fetching process data - retrying later")
                raise UpdateFailed(f"Inverter busy/internal error: {error_msg}") from err

            if isinstance(err, ApiException):
                modbus_err = parse_modbus_exception(err)
                _LOGGER.error("Error fetching process data for %s: %s", self.name, modbus_err.message)
            elif "Unknown API response [500]" in error_msg:
                _LOGGER.warning("Inverter API returned 500 error fetching process data for %s - feature likely not supported", self.name)
            else:
                _LOGGER.error("Error fetching process data for %s: %s", self.name, err)
            raise UpdateFailed(f"Error communicating with API: {error_msg}") from err

        result: dict[str, dict[str, str]] = {}
        for module_id in fetched_data:
            try:
                module_data = fetched_data[module_id]
                if hasattr(module_data, 'items') and callable(getattr(module_data, 'items')):
                    result[module_id] = {
                        process_data_id: str(module_data[process_data_id].value)
                        for process_data_id in module_data.keys()
                    }
                elif hasattr(module_data, '__iter__') and hasattr(module_data, '__getitem__'):
                    result[module_id] = {
                        process_data_id: str(module_data[process_data_id].value)
                        for process_data_id in module_data
                    }
                else:
                    _LOGGER.warning("Unsupported data type for module %s: %s", module_id, type(module_data))
                    result[module_id] = {}
            except (AttributeError, TypeError, KeyError, ValueError) as err:
                _LOGGER.warning("Error processing module %s: %s", module_id, err)
                result[module_id] = {}

        if result:
            self._last_result = result
        return result


class SettingDataUpdateCoordinator(
    AdaptivePollingCoordinatorMixin,
    PlenticoreUpdateCoordinator[Mapping[str, Mapping[str, str]]],
    DataUpdateCoordinatorMixin,
):
    """Implementation of PlenticoreUpdateCoordinator for settings data."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize with last-result fallback for 503 errors."""
        super().__init__(*args, **kwargs)
        self._last_result: Mapping[str, Mapping[str, str]] = {}
        self._init_adaptive_polling(
            default_base_seconds=30,
            max_interval_floor_seconds=300,
            max_interval_multiplier=8,
            failure_multiplier_cap=8,
        )

    async def _async_update_data(self) -> Mapping[str, Mapping[str, str]]:
        if (client := self._plenticore.client) is None:
            return {}

        fetch = defaultdict(set)

        for module_id, data_ids in self._fetch.items():
            fetch[module_id].update(data_ids)

        for module_id, data_id in self.async_contexts():
            fetch[module_id].add(data_id)

        if not fetch:
            return {}

        _LOGGER.debug("Fetching %s for %s", self.name, fetch)

        try:
            result = await client.get_setting_values(fetch)
            self._last_result = result
            # CHANGELOG (Codex, 2026-02-05):
            # Auto-clear inverter_busy once settings communication recovers.
            if (hass := getattr(self._plenticore, "hass", None)) is not None:
                clear_issue(hass, "inverter_busy")
            self._record_success()
            return result
        except (ApiException, ClientError, TimeoutError) as err:
            error_msg = str(err)
            self._record_failure()

            # Handle 503 errors (internal communication error)
            if "internal communication error" in error_msg.lower() or "[503]" in error_msg:
                 # Fallback to last known data to avoid entity unavailability
                 if self._last_result:
                     _LOGGER.warning(
                         "Inverter internal communication error (503) - using last known data for settings"
                     )
                     return self._last_result

                 create_inverter_busy_issue(self._plenticore.hass)
                 _LOGGER.warning(
                     "Inverter internal communication error (503) fetching settings - retrying later"
                 )
                 return {}

            # Handle 404 errors (missing setting) - warn but continue
            if "[404]" in error_msg or "not found" in error_msg.lower():
                 _LOGGER.warning(
                     "Some settings are not available on this device (404) - feature unsupported: %s",
                     self.name,
                 )
                 return {}

            if "Missing data_id" in error_msg:
                 _LOGGER.warning("Missing data_id during settings fetch: %s", error_msg)
                 return {}

            if isinstance(err, ApiException):
                modbus_err = parse_modbus_exception(err)
                _LOGGER.error("Error fetching setting data for %s: %s", self.name, modbus_err.message)
            elif "Unknown API response [500]" in error_msg:
                _LOGGER.warning("Inverter API returned 500 error fetching setting data for %s - feature likely not supported", self.name)
            else:
                _LOGGER.error("Error fetching setting data for %s: %s", self.name, err)
            raise UpdateFailed(f"Error communicating with API: {error_msg}") from err


class EventDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for inverter event snapshots and bounded event history."""

    config_entry: PlenticoreConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: PlenticoreConfigEntry,
        logger: logging.Logger,
        name: str,
        update_interval: timedelta,
        plenticore: Plenticore,
    ) -> None:
        super().__init__(
            hass=hass,
            logger=logger,
            config_entry=config_entry,
            name=name,
            update_interval=update_interval,
        )
        self._plenticore = plenticore
        self._history: deque[dict[str, Any]] = deque(maxlen=EVENT_HISTORY_MAX)
        self._last_signature_ts: dict[str, float] = {}
        self._last_result: dict[str, Any] = {}

    @property
    def history(self) -> list[dict[str, Any]]:
        """Return bounded event history (newest first)."""
        return list(self._history)

    def _event_signature(self, event: EventData) -> str:
        return f"{event.code}:{event.category}:{event.is_active}"

    def _event_to_payload(self, event: EventData) -> dict[str, Any]:
        return {
            "code": int(event.code),
            "category": str(event.category),
            "is_active": bool(event.is_active),
            "description": str(event.description),
            "group": str(event.group),
            "start_time": event.start_time.isoformat(),
            "end_time": event.end_time.isoformat(),
        }

    def _append_history_if_new(self, event: EventData) -> None:
        now = time.monotonic()
        signature = self._event_signature(event)
        last_seen = self._last_signature_ts.get(signature)
        if (
            last_seen is not None
            and (now - last_seen) < EVENT_DEDUP_COOLDOWN_SECONDS
        ):
            return
        self._last_signature_ts[signature] = now
        self._history.appendleft(self._event_to_payload(event))

    async def _async_update_data(self) -> dict[str, Any]:
        client = self._plenticore.client
        if client is None:
            return self._last_result

        try:
            events = list(await client.get_events(max_count=25))
            events.sort(key=lambda e: e.start_time, reverse=True)

            for event in events:
                self._append_history_if_new(event)

            latest = events[0] if events else None
            active_error_count = sum(
                1 for event in events if event.category.lower() == "error" and event.is_active
            )
            now_epoch = time.time()
            result: dict[str, Any]
            if latest is None:
                result = {
                    "last_event_code": None,
                    "last_event_category": None,
                    "last_event_age_s": None,
                    "active_error_events_count": 0,
                    "last_event_description": None,
                    "history_size": len(self._history),
                    "history_dropped": False,
                    "fetched_count": 0,
                }
            else:
                latest_age = max(0, int(now_epoch - latest.start_time.timestamp()))
                result = {
                    "last_event_code": int(latest.code),
                    "last_event_category": str(latest.category),
                    "last_event_age_s": latest_age,
                    "active_error_events_count": int(active_error_count),
                    "last_event_description": str(latest.description),
                    "history_size": len(self._history),
                    "history_dropped": len(self._history) >= EVENT_HISTORY_MAX,
                    "fetched_count": len(events),
                }

            self._last_result = result
            return result

        except (ApiException, ClientError, TimeoutError) as err:
            error_msg = str(err)
            if "internal communication error" in error_msg.lower() or "[503]" in error_msg:
                _LOGGER.debug("Event fetch busy (503), using last event snapshot")
                return self._last_result
            if isinstance(err, ApiException):
                modbus_err = parse_modbus_exception(err)
                _LOGGER.debug("Event fetch failed: %s", modbus_err.message)
            else:
                _LOGGER.debug("Event fetch failed: %s", err)
            return self._last_result
        except Exception as err:
            _LOGGER.debug("Event fetch failed unexpectedly: %s", err)
            return self._last_result


class PlenticoreSelectUpdateCoordinator(DataUpdateCoordinator[_DataT]):
    """Base implementation of DataUpdateCoordinator for Plenticore data."""

    config_entry: PlenticoreConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: PlenticoreConfigEntry,
        logger: logging.Logger,
        name: str,
        update_interval: timedelta,
        plenticore: Plenticore,
    ) -> None:
        """Create a new update coordinator for plenticore data."""
        super().__init__(
            hass=hass,
            logger=logger,
            config_entry=config_entry,
            name=name,
            update_interval=update_interval,
        )
        # CHANGELOG (Codex, 2026-02-05):
        # Fix review finding #1 (select fetch overwrite). Store multiple select
        # entities per module to avoid overwriting previously registered entries.
        # Map: module_id -> data_id -> list[str] (all options)
        self._fetch: dict[str, dict[str, list[str]]] = {}
        self._plenticore = plenticore

    def start_fetch_data(
        self, module_id: str, data_id: str, all_options: list[str]
    ) -> CALLBACK_TYPE:
        """Start fetching the given data (module-id and entry-id)."""
        module_fetch = self._fetch.setdefault(module_id, {})
        module_fetch[data_id] = list(all_options)

        # Force an update of all data. Multiple refresh calls
        # are ignored by the debouncer.
        async def force_refresh(event_time: Any) -> None:
            try:
                await self.async_request_refresh()
            except Exception as err:
                _LOGGER.debug("Deferred select refresh failed for %s: %s", self.name, err)

        return async_call_later(self.hass, 2, force_refresh)

    def stop_fetch_data(
        self, module_id: str, data_id: str, all_options: list[str]
    ) -> None:
        """Stop fetching the given data (module-id and entry-id)."""
        if module_id not in self._fetch:
            return
        self._fetch[module_id].pop(data_id, None)
        if not self._fetch[module_id]:
            self._fetch.pop(module_id, None)


class SelectDataUpdateCoordinator(
    PlenticoreSelectUpdateCoordinator[dict[str, dict[str, str]]],
    DataUpdateCoordinatorMixin,
):
    """Implementation of PlenticoreUpdateCoordinator for select data."""

    async def _async_update_data(self) -> dict[str, dict[str, str]]:
        if self._plenticore.client is None:
            return {}

        _LOGGER.debug("Fetching select %s for %s", self.name, self._fetch)

        # Snapshot fetch map to avoid RuntimeError when entities are added/removed
        # while an update round is iterating with await points.
        fetch_snapshot = {
            module: {
                data_id: list(options)
                for data_id, options in data_map.items()
            }
            for module, data_map in self._fetch.items()
        }
        return await self._async_get_current_option(fetch_snapshot)

    async def _async_get_current_option(
        self,
        module_id: dict[str, dict[str, list[str]]],
    ) -> dict[str, dict[str, str]]:
        """Get current option."""
        # CHANGELOG (Codex, 2026-02-05):
        # Fix review finding #1: evaluate all options for all tracked select
        # entities instead of returning after the first entry.
        result: dict[str, dict[str, str]] = {}
        for mid, data_map in list(module_id.items()):
            module_result: dict[str, str] = {}
            for data_id, all_options in list(data_map.items()):
                selected_option = "None"
                for all_option in list(all_options):
                    if all_option == "None":
                        continue
                    val = await self.async_read_data(mid, all_option)
                    if not val:
                        continue
                    for option in val.values():
                        # Safe dictionary access - use .get() to prevent KeyError
                        if option.get(all_option) == "1":
                            selected_option = all_option
                            break
                    if selected_option != "None":
                        break
                module_result[data_id] = selected_option
            if module_result:
                result[mid] = module_result
        return result
