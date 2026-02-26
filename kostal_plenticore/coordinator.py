"""Code to handle the Plenticore API."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import timedelta
import logging
from typing import Any, TYPE_CHECKING, TypeVar, cast
import asyncio

from aiohttp.client_exceptions import ClientError
from pykoplenti import (
    ApiClient,
    ApiException,
    AuthenticationException,
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

from .const import CONF_SERVICE_CODE, DOMAIN
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
    parse_modbus_exception,
)

_LOGGER = logging.getLogger(__name__)

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
        self.available_modules: list[str] = []

    @property
    def host(self) -> str:
        """Return the host of the Plenticore inverter."""
        return cast(str, self.config_entry.data[CONF_HOST])

    @property
    def client(self) -> ApiClient:
        """Return the Plenticore API client."""
        return cast(ApiClient, self._client)

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
                modules_task, metadata_task, return_exceptions=True
            )
            
            # Handle results
            if isinstance(modules_result, Exception):
                raise modules_result
            if isinstance(metadata_result, Exception):
                raise metadata_result
                
            return True
            
        except (ApiException, KeyError, ValueError, asyncio.CancelledError) as err:
            if isinstance(err, ApiException):
                modbus_err = parse_modbus_exception(err)
                _LOGGER.error("Could not get device metadata: %s", modbus_err.message)
            else:
                _LOGGER.error("Error processing device metadata: %s", err)
            return False

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
            self.available_modules = ["devices:local", "scb:statistic:EnergyFlow", "scb:event", "scb:system", "scb:network"]

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
            self.device_info = DeviceInfo(
                configuration_url=f"http://{self.host}",
                identifiers={(DOMAIN, "unknown")},
                manufacturer="Kostal",
                model="Unknown",
                name=self.host,
            )
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

        try:
            await client.set_setting_values(module_id, value)
            # CHANGELOG (Codex, 2026-02-05):
            # Successful write confirms recovery from prior inverter_busy state.
            if (hass := getattr(self._plenticore, "hass", None)) is not None:
                clear_issue(hass, "inverter_busy")
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
            await self.async_request_refresh()

        return async_call_later(self.hass, 0.5, force_refresh)

    def stop_fetch_data(self, module_id: str, data_id: str) -> None:
        """Stop fetching the given data (module-id and data-id)."""
        if module_id in self._fetch and data_id in self._fetch[module_id]:
            try:
                self._fetch[module_id].remove(data_id)
            except ValueError:
                # Data ID already removed, ignore error
                pass


class ProcessDataUpdateCoordinator(
    PlenticoreUpdateCoordinator[Mapping[str, Mapping[str, str]]]
):
    """Implementation of PlenticoreUpdateCoordinator for process data."""

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
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout fetching process data for %s", self.name)
            raise UpdateFailed("Timeout fetching process data") from None
        except (ApiException, ClientError, TimeoutError) as err:
            error_msg = str(err)

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

        return result


class SettingDataUpdateCoordinator(
    PlenticoreUpdateCoordinator[Mapping[str, Mapping[str, str]]],
    DataUpdateCoordinatorMixin,
):
    """Implementation of PlenticoreUpdateCoordinator for settings data."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize with last-result fallback for 503 errors."""
        super().__init__(*args, **kwargs)
        self._last_result: Mapping[str, Mapping[str, str]] = {}

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
            return result
        except (ApiException, ClientError, TimeoutError) as err:
            error_msg = str(err)

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
            await self.async_request_refresh()

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

        return await self._async_get_current_option(self._fetch)

    async def _async_get_current_option(
        self,
        module_id: dict[str, dict[str, list[str]]],
    ) -> dict[str, dict[str, str]]:
        """Get current option."""
        # CHANGELOG (Codex, 2026-02-05):
        # Fix review finding #1: evaluate all options for all tracked select
        # entities instead of returning after the first entry.
        result: dict[str, dict[str, str]] = {}
        for mid, data_map in module_id.items():
            module_result: dict[str, str] = {}
            for data_id, all_options in data_map.items():
                selected_option = "None"
                for all_option in all_options:
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
