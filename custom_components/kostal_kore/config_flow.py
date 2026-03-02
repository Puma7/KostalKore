"""Config flow for KOSTAL KORE integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import ipaddress
import logging
from collections.abc import Mapping
from typing import Any, Final, TypeAlias, TYPE_CHECKING

from aiohttp.client_exceptions import ClientError, ContentTypeError
from pykoplenti import ApiClient, AuthenticationException, ApiException
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_BASE, CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_ACCESS_ROLE,
    CONF_INSTALLER_ACCESS,
    CONF_KSEM_ENABLED,
    CONF_KSEM_HOST,
    CONF_KSEM_PORT,
    CONF_KSEM_UNIT_ID,
    CONF_MODBUS_ENABLED,
    CONF_MODBUS_ENDIANNESS,
    CONF_MODBUS_PORT,
    CONF_MODBUS_PROXY_BIND,
    CONF_MODBUS_PROXY_ENABLED,
    CONF_MODBUS_PROXY_PORT,
    CONF_MODBUS_UNIT_ID,
    CONF_MQTT_BRIDGE_ENABLED,
    CONF_SERVICE_CODE,
    DEFAULT_MODBUS_PORT,
    DEFAULT_MODBUS_PROXY_BIND,
    DEFAULT_MODBUS_UNIT_ID,
    DEFAULT_KSEM_PORT,
    DEFAULT_KSEM_UNIT_ID,
    DOMAIN,
)
from .helper import get_hostname_id

if TYPE_CHECKING:  # pragma: no cover
    from homeassistant.config_entries import ConfigFlowResult
else:
    ConfigFlowResult: TypeAlias = dict[str, Any]

_LOGGER = logging.getLogger(__name__)

# Network and connection constants
NETWORK_MODULE: Final[str] = "scb:network"
DEFAULT_ERROR_MESSAGE: Final[str] = "unknown"
CONNECTION_TEST_TIMEOUT_SECONDS: Final[float] = 30.0

# Discovery constants (best-effort scan for local inverters)
DISCOVERY_HTTP_PORT: Final[int] = 80
DISCOVERY_PROBE_TIMEOUT_SECONDS: Final[float] = 0.35
DISCOVERY_PARALLEL_PROBES: Final[int] = 24
DISCOVERY_MAX_HOSTS_PER_ADAPTER: Final[int] = 64
DISCOVERY_MAX_AUTH_ATTEMPTS: Final[int] = 8

# Modbus options constants
DEFAULT_MODBUS_PROXY_PORT: Final[int] = 5502


class NoDiscoveredInverterError(Exception):
    """Raised when no inverter can be discovered in the local network."""


class DiscoveryAuthFailedError(Exception):
    """Raised when discovery found hosts but none accepted credentials."""


@dataclass(slots=True)
class ConnectionCheckResult:
    """Result of a successful inverter connection check."""

    host: str
    hostname: str
    access_role: str
    installer_access: bool


DATA_SCHEMA: Final[vol.Schema] = vol.Schema(
    {
        vol.Optional(CONF_HOST, default=""): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_SERVICE_CODE): str,
    }
)


def _installer_access_from_role(access_role: str, service_code: str | None) -> bool:
    """Return whether the current authenticated role may write installer settings."""
    normalized = access_role.strip().upper()
    if normalized in {"INSTALLER", "SERVICE", "TECHNICIAN", "ADMIN"}:
        return True
    if normalized in {"USER", "GUEST", "ANONYMOUS"}:
        return False
    # Fallback for unknown role names on older firmware generations.
    return bool(service_code)


def _sanitize_auth_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Normalize config flow auth input values."""
    sanitized: dict[str, Any] = {
        CONF_PASSWORD: str(user_input.get(CONF_PASSWORD, "")),
    }

    host = str(user_input.get(CONF_HOST, "")).strip()
    if host:
        sanitized[CONF_HOST] = host

    service_code_raw = str(user_input.get(CONF_SERVICE_CODE, "")).strip()
    if service_code_raw:
        sanitized[CONF_SERVICE_CODE] = service_code_raw

    return sanitized


def _build_entry_data(
    auth_input: dict[str, Any],
    connection_result: ConnectionCheckResult,
) -> dict[str, Any]:
    """Create persisted config entry data from auth input + connection result."""
    data: dict[str, Any] = {
        CONF_HOST: connection_result.host,
        CONF_PASSWORD: str(auth_input[CONF_PASSWORD]),
        CONF_ACCESS_ROLE: connection_result.access_role,
        CONF_INSTALLER_ACCESS: connection_result.installer_access,
    }
    service_code = auth_input.get(CONF_SERVICE_CODE)
    if service_code:
        data[CONF_SERVICE_CODE] = str(service_code)
    return data


def _options_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Build the shared schema for setup wizard and options panel."""
    return vol.Schema(
        {
            vol.Optional(
                CONF_MODBUS_ENABLED,
                default=defaults.get(CONF_MODBUS_ENABLED, False),
            ): bool,
            vol.Optional(
                CONF_MODBUS_PORT,
                default=defaults.get(CONF_MODBUS_PORT, DEFAULT_MODBUS_PORT),
            ): int,
            vol.Optional(
                CONF_MODBUS_UNIT_ID,
                default=defaults.get(CONF_MODBUS_UNIT_ID, DEFAULT_MODBUS_UNIT_ID),
            ): int,
            vol.Optional(
                CONF_MODBUS_ENDIANNESS,
                default=defaults.get(CONF_MODBUS_ENDIANNESS, "auto"),
            ): vol.In(["auto", "little", "big"]),
            vol.Optional(
                CONF_MQTT_BRIDGE_ENABLED,
                default=defaults.get(CONF_MQTT_BRIDGE_ENABLED, False),
            ): bool,
            vol.Optional(
                CONF_MODBUS_PROXY_ENABLED,
                default=defaults.get(CONF_MODBUS_PROXY_ENABLED, False),
            ): bool,
            vol.Optional(
                CONF_MODBUS_PROXY_PORT,
                default=defaults.get(CONF_MODBUS_PROXY_PORT, DEFAULT_MODBUS_PROXY_PORT),
            ): int,
            vol.Optional(
                CONF_MODBUS_PROXY_BIND,
                default=defaults.get(CONF_MODBUS_PROXY_BIND, DEFAULT_MODBUS_PROXY_BIND),
            ): str,
            vol.Optional(
                CONF_KSEM_ENABLED,
                default=defaults.get(CONF_KSEM_ENABLED, False),
            ): bool,
            vol.Optional(
                CONF_KSEM_HOST,
                default=defaults.get(CONF_KSEM_HOST, ""),
            ): str,
            vol.Optional(
                CONF_KSEM_PORT,
                default=defaults.get(CONF_KSEM_PORT, DEFAULT_KSEM_PORT),
            ): int,
            vol.Optional(
                CONF_KSEM_UNIT_ID,
                default=defaults.get(CONF_KSEM_UNIT_ID, DEFAULT_KSEM_UNIT_ID),
            ): int,
        }
    )


def _normalize_options(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize options and enforce valid dependencies."""
    modbus_enabled = bool(user_input.get(CONF_MODBUS_ENABLED, False))
    mqtt_enabled = bool(user_input.get(CONF_MQTT_BRIDGE_ENABLED, False))
    proxy_enabled = bool(user_input.get(CONF_MODBUS_PROXY_ENABLED, False))
    ksem_enabled = bool(user_input.get(CONF_KSEM_ENABLED, False))

    # MQTT bridge and proxy both depend on Modbus being enabled.
    if mqtt_enabled or proxy_enabled:
        modbus_enabled = True

    return {
        CONF_MODBUS_ENABLED: modbus_enabled,
        CONF_MODBUS_PORT: int(user_input.get(CONF_MODBUS_PORT, DEFAULT_MODBUS_PORT)),
        CONF_MODBUS_UNIT_ID: int(
            user_input.get(CONF_MODBUS_UNIT_ID, DEFAULT_MODBUS_UNIT_ID)
        ),
        CONF_MODBUS_ENDIANNESS: str(user_input.get(CONF_MODBUS_ENDIANNESS, "auto")),
        CONF_MQTT_BRIDGE_ENABLED: mqtt_enabled,
        CONF_MODBUS_PROXY_ENABLED: proxy_enabled,
        CONF_MODBUS_PROXY_PORT: int(
            user_input.get(CONF_MODBUS_PROXY_PORT, DEFAULT_MODBUS_PROXY_PORT)
        ),
        CONF_MODBUS_PROXY_BIND: str(
            user_input.get(CONF_MODBUS_PROXY_BIND, DEFAULT_MODBUS_PROXY_BIND)
        ),
        CONF_KSEM_ENABLED: ksem_enabled,
        CONF_KSEM_HOST: str(user_input.get(CONF_KSEM_HOST, "")).strip(),
        CONF_KSEM_PORT: int(user_input.get(CONF_KSEM_PORT, DEFAULT_KSEM_PORT)),
        CONF_KSEM_UNIT_ID: int(
            user_input.get(CONF_KSEM_UNIT_ID, DEFAULT_KSEM_UNIT_ID)
        ),
    }


async def _probe_tcp_port(host: str, port: int) -> bool:
    """Return True if host:port is reachable."""
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=DISCOVERY_PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, TimeoutError, asyncio.TimeoutError):
        return False

    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return True


async def _build_discovery_candidates(hass: HomeAssistant) -> list[str]:
    """Build a bounded list of candidate hosts from active IPv4 adapters."""
    try:
        from homeassistant.components.network import async_get_adapters

        adapters = await async_get_adapters(hass)
    except Exception as err:  # pragma: no cover - depends on host env
        _LOGGER.debug("Could not enumerate network adapters for discovery: %s", err)
        return []

    candidates: list[str] = []
    for adapter in adapters:
        for ipv4 in adapter.get("ipv4", []):
            address = ipv4.get("address")
            prefix_raw = ipv4.get("network_prefix")
            try:
                prefix = int(prefix_raw)
            except (TypeError, ValueError):
                continue
            if not address or prefix < 16 or prefix > 30:
                continue

            try:
                iface = ipaddress.ip_interface(f"{address}/{prefix}")
            except ValueError:
                continue

            own_ip = iface.ip
            host_ips = [ip for ip in iface.network.hosts() if ip != own_ip]
            if not host_ips:
                continue

            if len(host_ips) > DISCOVERY_MAX_HOSTS_PER_ADAPTER:
                own_as_int = int(own_ip)
                host_ips.sort(key=lambda ip: abs(int(ip) - own_as_int))
                host_ips = host_ips[:DISCOVERY_MAX_HOSTS_PER_ADAPTER]

            candidates.extend(str(ip) for ip in host_ips)

    deduped: list[str] = []
    seen: set[str] = set()
    for host in candidates:
        if host in seen:
            continue
        seen.add(host)
        deduped.append(host)

    return deduped


async def discover_inverter_hosts(hass: HomeAssistant) -> list[str]:
    """Best-effort discovery by probing likely local IPv4 hosts."""
    candidates = await _build_discovery_candidates(hass)
    if not candidates:
        return []

    semaphore = asyncio.Semaphore(DISCOVERY_PARALLEL_PROBES)

    async def _probe_candidate(host: str) -> str | None:
        async with semaphore:
            if await _probe_tcp_port(host, DISCOVERY_HTTP_PORT):
                return host
            return None

    results = await asyncio.gather(*(_probe_candidate(host) for host in candidates))
    return [host for host in results if host is not None]


def _handle_config_flow_error(err: Exception, operation: str) -> dict[str, str]:
    """Map setup exceptions to config flow error keys."""
    errors: dict[str, str] = {}

    if isinstance(err, (AuthenticationException, DiscoveryAuthFailedError)):
        errors[CONF_PASSWORD] = "invalid_auth"
        _LOGGER.error("Authentication error during %s: %s", operation, err)
    elif isinstance(err, NoDiscoveredInverterError):
        errors[CONF_HOST] = "no_discovered_inverter"
        _LOGGER.warning("No inverter discovered during %s", operation)
    elif isinstance(err, asyncio.TimeoutError):
        errors[CONF_HOST] = "timeout"
        _LOGGER.warning("Timeout during %s", operation)
    elif isinstance(err, ContentTypeError):
        errors[CONF_HOST] = "cannot_connect"
        _LOGGER.warning(
            "Non-JSON response during %s. Host may not be a Kostal inverter "
            "or traffic is intercepted by a proxy/captive portal: %s",
            operation,
            err,
        )
    elif isinstance(err, (ClientError, TimeoutError)):
        errors[CONF_HOST] = "cannot_connect"
        _LOGGER.error("Network error during %s: %s", operation, err)
    elif isinstance(err, ApiException):
        errors[CONF_HOST] = "cannot_connect"
        _LOGGER.error("API error during %s: %s", operation, err)
    else:
        errors[CONF_BASE] = DEFAULT_ERROR_MESSAGE
        _LOGGER.exception("Unexpected error during %s: %s", operation, err)

    return errors


async def test_connection_safe(
    hass: HomeAssistant,
    data: dict[str, Any],
) -> ConnectionCheckResult:
    """Validate inverter credentials and return hostname + access profile."""
    host = str(data.get(CONF_HOST, "")).strip()
    if not host:
        raise NoDiscoveredInverterError("Host is empty and discovery did not run.")

    password = str(data[CONF_PASSWORD])
    service_code = data.get(CONF_SERVICE_CODE)

    session = async_get_clientsession(hass)
    async with ApiClient(session, host) as client:
        await asyncio.wait_for(
            client.login(password, service_code=service_code),
            timeout=CONNECTION_TEST_TIMEOUT_SECONDS,
        )

        hostname_id = await get_hostname_id(client)
        values = await client.get_setting_values(NETWORK_MODULE, hostname_id)
        network_settings = values.get(NETWORK_MODULE, {})
        hostname = str(network_settings.get(hostname_id, host))

        access_role = "UNKNOWN"
        try:
            me_data = await client.get_me()
            role = getattr(me_data, "role", "UNKNOWN")
            access_role = str(role).strip().upper() or "UNKNOWN"
        except Exception as err:  # pragma: no cover - role endpoint varies by firmware
            _LOGGER.debug("Role detection failed for %s: %s", host, err)

        return ConnectionCheckResult(
            host=host,
            hostname=hostname,
            access_role=access_role,
            installer_access=_installer_access_from_role(
                access_role, str(service_code) if service_code else None
            ),
        )


async def resolve_connection_safe(
    hass: HomeAssistant,
    auth_input: dict[str, Any],
) -> ConnectionCheckResult:
    """Resolve host (manual or discovery) and validate credentials."""
    host = str(auth_input.get(CONF_HOST, "")).strip()
    if host:
        return await test_connection_safe(
            hass,
            {**auth_input, CONF_HOST: host},
        )

    discovered_hosts = await discover_inverter_hosts(hass)
    if not discovered_hosts:
        raise NoDiscoveredInverterError

    auth_failed = False
    last_error: Exception | None = None
    for candidate in discovered_hosts[:DISCOVERY_MAX_AUTH_ATTEMPTS]:
        try:
            return await test_connection_safe(
                hass,
                {**auth_input, CONF_HOST: candidate},
            )
        except AuthenticationException:
            auth_failed = True
        except Exception as err:  # pragma: no cover - depends on network profile
            last_error = err

    if auth_failed:
        raise DiscoveryAuthFailedError(
            "Discovered hosts found, but credentials were rejected."
        )
    if last_error is not None:
        raise last_error
    raise NoDiscoveredInverterError


async def run_modbus_connection_test(
    host: str,
    options: dict[str, Any],
) -> tuple[bool, str]:
    """Run a short Modbus smoke test and return (passed, log)."""
    port = int(options.get(CONF_MODBUS_PORT, DEFAULT_MODBUS_PORT))
    unit_id = int(options.get(CONF_MODBUS_UNIT_ID, DEFAULT_MODBUS_UNIT_ID))
    endianness_setting = str(options.get(CONF_MODBUS_ENDIANNESS, "auto"))

    test_log: list[str] = []
    test_passed = True

    try:
        from .modbus_client import KostalModbusClient
        from .modbus_registers import (
            BATTERY_MGMT_MODES,
            INVERTER_STATES,
            REG_BATTERY_MGMT_MODE,
            REG_INVERTER_MAX_POWER,
            REG_INVERTER_STATE,
            REG_PRODUCT_NAME,
            REG_SERIAL_NUMBER,
            REG_TOTAL_DC_POWER,
        )

        client = KostalModbusClient(
            host=str(host),
            port=port,
            unit_id=unit_id,
            endianness="little" if endianness_setting == "auto" else endianness_setting,
        )

        test_log.append(f"Connecting to {host}:{port} (Unit-ID {unit_id})...")
        await client.connect()
        test_log.append("TCP connection: OK")

        endianness = await client.detect_endianness()
        order = "CDAB" if endianness == "little" else "ABCD"
        test_log.append(f"Byte order: {endianness} ({order})")

        test_regs = [
            (REG_PRODUCT_NAME, "Product"),
            (REG_SERIAL_NUMBER, "Serial"),
            (REG_INVERTER_MAX_POWER, "Max Power"),
            (REG_INVERTER_STATE, "State"),
            (REG_TOTAL_DC_POWER, "DC Power"),
            (REG_BATTERY_MGMT_MODE, "Battery Mgmt"),
        ]
        for reg, label in test_regs:
            try:
                val = await client.read_register(reg)
                if reg == REG_INVERTER_STATE:
                    val = INVERTER_STATES.get(int(val), str(val))
                elif reg == REG_BATTERY_MGMT_MODE:  # pragma: no cover
                    mode_int = int(val)
                    val = BATTERY_MGMT_MODES.get(mode_int, str(val))
                elif reg == REG_INVERTER_MAX_POWER:
                    val = f"{val} W"
                test_log.append(f"{label}: {val}")
            except Exception as reg_err:
                test_log.append(f"{label}: ERROR - {reg_err}")

        await client.disconnect()
        test_log.append("")
        test_log.append("Modbus test passed. Click submit to save settings.")

    except Exception as err:
        test_passed = False
        test_log.append(f"ERROR: {err}")
        test_log.append("")
        test_log.append("Modbus test failed. Check network and inverter settings.")
        test_log.append(f"  Host: {host}:{port}")
        test_log.append(f"  Unit-ID: {unit_id}")
        test_log.append(f"  Endianness: {endianness_setting}")
        test_log.append(f"  Error: {type(err).__name__}: {err}")
        _LOGGER.error("Modbus connection test failed: %s", err)

    return test_passed, "\n".join(test_log)


class KostalPlenticoreConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for KOSTAL KORE."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize state for the setup wizard."""
        self._pending_entry_data: dict[str, Any] = {}
        self._pending_entry_title: str = ""
        self._pending_options: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> KostalPlenticoreOptionsFlow:
        """Return the options flow handler."""
        return KostalPlenticoreOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: Connect manually or via auto-discovery."""
        errors: dict[str, str] = {}

        if user_input is not None:
            auth_input = _sanitize_auth_input(user_input)
            try:
                connection_result = await resolve_connection_safe(self.hass, auth_input)
            except Exception as err:
                errors = _handle_config_flow_error(err, "user setup")
            else:
                self._async_abort_entries_match({CONF_HOST: connection_result.host})
                self._pending_entry_data = _build_entry_data(auth_input, connection_result)
                self._pending_entry_title = connection_result.hostname
                return await self.async_step_setup_options()

        return self.async_show_form(
            step_id="user",
            data_schema=DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_setup_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: Choose Modbus/MQTT/proxy options in the first-run wizard."""
        if not self._pending_entry_data:
            return await self.async_step_user()

        if user_input is not None:
            options = _normalize_options(user_input)
            self._pending_options = options

            if options.get(CONF_MODBUS_ENABLED, False):
                return await self.async_step_setup_modbus_test()

            return self.async_create_entry(
                title=self._pending_entry_title,
                data=self._pending_entry_data,
                options=options,
            )

        defaults = _normalize_options({})
        access_role = str(self._pending_entry_data.get(CONF_ACCESS_ROLE, "UNKNOWN"))
        write_access = (
            "enabled"
            if bool(self._pending_entry_data.get(CONF_INSTALLER_ACCESS, False))
            else "restricted"
        )
        return self.async_show_form(
            step_id="setup_options",
            data_schema=_options_schema(defaults),
            description_placeholders={
                "detected_host": str(self._pending_entry_data.get(CONF_HOST, "")),
                "access_role": access_role,
                "write_access": write_access,
            },
        )

    async def async_step_setup_modbus_test(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Optional Modbus test before creating first config entry."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._pending_entry_title,
                data=self._pending_entry_data,
                options=self._pending_options,
            )

        host = str(self._pending_entry_data.get(CONF_HOST, ""))
        test_passed, description = await run_modbus_connection_test(
            host=host,
            options=self._pending_options,
        )
        return self.async_show_form(
            step_id="setup_modbus_test",
            data_schema=vol.Schema({}),
            description_placeholders={"test_result": description},
            errors={"base": "modbus_test_failed"} if not test_passed else {},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow reconfigure with manual host or auto-discovery."""
        errors: dict[str, str] = {}

        if user_input is not None:
            auth_input = _sanitize_auth_input(user_input)
            try:
                connection_result = await resolve_connection_safe(self.hass, auth_input)
            except Exception as err:
                errors = _handle_config_flow_error(err, "reconfigure")
            else:
                self._async_abort_entries_match({CONF_HOST: connection_result.host})
                return self.async_update_reload_and_abort(
                    entry=self._get_reconfigure_entry(),
                    title=connection_result.hostname,
                    data=_build_entry_data(auth_input, connection_result),
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle re-authentication requests."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm re-authentication and update stored credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            auth_input = _sanitize_auth_input(user_input)
            try:
                connection_result = await resolve_connection_safe(self.hass, auth_input)
            except Exception as err:
                errors = _handle_config_flow_error(err, "reauth")
            else:
                entry_id = self.context.get("entry_id")
                if entry_id:
                    entry = self.hass.config_entries.async_get_entry(entry_id)
                    if entry is not None:
                        self.hass.config_entries.async_update_entry(
                            entry,
                            title=connection_result.hostname,
                            data=_build_entry_data(auth_input, connection_result),
                        )
                        await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=DATA_SCHEMA,
            errors=errors,
        )


class KostalPlenticoreOptionsFlow(OptionsFlow):
    """Handle Modbus/MQTT options in the post-setup configuration panel."""

    def __init__(self) -> None:
        """Initialize options flow."""
        self._user_input: dict[str, Any] = {}

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Configure options and optionally validate Modbus connectivity."""
        if user_input is not None:
            normalized = _normalize_options(user_input)
            if normalized.get(CONF_MODBUS_ENABLED, False):
                self._user_input = normalized
                return await self.async_step_modbus_test()
            return self.async_create_entry(title="", data=normalized)

        defaults = _normalize_options(self.config_entry.options)
        access_role = str(self.config_entry.data.get(CONF_ACCESS_ROLE, "UNKNOWN"))
        write_access = (
            "enabled"
            if bool(
                self.config_entry.data.get(
                    CONF_INSTALLER_ACCESS,
                    bool(self.config_entry.data.get(CONF_SERVICE_CODE)),
                )
            )
            else "restricted"
        )
        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(defaults),
            description_placeholders={
                "access_role": access_role,
                "write_access": write_access,
            },
        )

    async def async_step_modbus_test(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Run Modbus test before saving options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=self._user_input)

        host = str(self.config_entry.data.get(CONF_HOST, ""))
        test_passed, description = await run_modbus_connection_test(
            host=host,
            options=self._user_input,
        )
        return self.async_show_form(
            step_id="modbus_test",
            data_schema=vol.Schema({}),
            description_placeholders={"test_result": description},
            errors={"base": "modbus_test_failed"} if not test_passed else {},
        )
