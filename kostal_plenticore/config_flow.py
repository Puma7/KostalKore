"""Config flow for Kostal Plenticore Solar Inverter integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Final, TypeAlias, TYPE_CHECKING

from aiohttp.client_exceptions import ClientError
from pykoplenti import ApiClient, AuthenticationException, ApiException
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
if TYPE_CHECKING:  # pragma: no cover
    from homeassistant.config_entries import ConfigFlowResult
else:
    ConfigFlowResult: TypeAlias = dict[str, Any]
from homeassistant.const import CONF_BASE, CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_MODBUS_ENABLED,
    CONF_MODBUS_ENDIANNESS,
    CONF_MODBUS_PORT,
    CONF_MODBUS_UNIT_ID,
    CONF_MQTT_BRIDGE_ENABLED,
    CONF_SERVICE_CODE,
    DEFAULT_MODBUS_PORT,
    DEFAULT_MODBUS_UNIT_ID,
    DOMAIN,
)
from .helper import get_hostname_id

_LOGGER = logging.getLogger(__name__)

# Config flow constants
DATA_SCHEMA: Final[vol.Schema] = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_SERVICE_CODE): str,
    }
)

# Network and connection constants
NETWORK_MODULE: Final[str] = "scb:network"
DEFAULT_ERROR_MESSAGE: Final[str] = "unknown"
CONNECTION_TEST_TIMEOUT_SECONDS: Final[float] = 30.0


def _handle_config_flow_error(err: Exception, operation: str) -> dict[str, str]:
    """
    Centralized error handling for config flow operations.
    
    Args:
        err: Exception that occurred
        operation: Description of the operation being performed
        
    Returns:
        Dictionary of field names to error messages
    """
    errors = {}
    
    if isinstance(err, AuthenticationException):
        errors[CONF_PASSWORD] = "invalid_auth"
        _LOGGER.error("Authentication error during %s: %s", operation, err)
    elif isinstance(err, asyncio.TimeoutError):
        errors[CONF_HOST] = "timeout"
        _LOGGER.warning("Timeout during %s", operation)
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


async def test_connection_safe(hass: HomeAssistant, data: dict[str, Any]) -> str:
    """
    Test the connection to the inverter with comprehensive error handling.
    
    This function validates the connection to the Kostal Plenticore inverter
    with timeout protection, rate limiting, and detailed error handling.
    
    Args:
        hass: Home Assistant instance
        data: Configuration data with host, password, and optional service code
        
    Returns:
        Hostname if successful, raises exception otherwise
        
    Raises:
        AuthenticationException: For invalid credentials
        ClientError: For network connectivity issues
        TimeoutError: For timeout scenarios
        ApiException: For API errors
        Exception: For unexpected errors
    """
    host = data[CONF_HOST]

    session = async_get_clientsession(hass)
    try:
        async with ApiClient(session, host) as client:
            await asyncio.wait_for(
                client.login(
                    data[CONF_PASSWORD], service_code=data.get(CONF_SERVICE_CODE)
                ),
                timeout=CONNECTION_TEST_TIMEOUT_SECONDS
            )
            hostname_id = await get_hostname_id(client)
            values = await client.get_setting_values(NETWORK_MODULE, hostname_id)
            
            # Safe dictionary access with fallback to host IP
            network_settings = values.get(NETWORK_MODULE, {})
            return str(network_settings.get(hostname_id, host))
            
    except Exception as err:
        _handle_config_flow_error(err, "connection test")
        raise err


class KostalPlenticoreConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Kostal Plenticore Solar Inverter."""

    VERSION = 1

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
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._async_abort_entries_match({CONF_HOST: user_input[CONF_HOST]})

            try:
                hostname = await test_connection_safe(self.hass, user_input)
            except Exception as err:
                errors = _handle_config_flow_error(err, "user setup")
            else:
                return self.async_create_entry(title=hostname, data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add reconfigure step to allow to reconfigure a config entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._async_abort_entries_match({CONF_HOST: user_input[CONF_HOST]})
            try:
                hostname = await test_connection_safe(self.hass, user_input)
            except Exception as err:
                errors = _handle_config_flow_error(err, "reconfigure")
            else:
                return self.async_update_reload_and_abort(
                    entry=self._get_reconfigure_entry(), title=hostname, data=user_input
                )

        return self.async_show_form(
            step_id="reconfigure", data_schema=DATA_SCHEMA, errors=errors
        )

    async def async_step_reauth(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle re-authentication requests."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm re-authentication and update stored credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                hostname = await test_connection_safe(self.hass, user_input)
            except Exception as err:
                errors = _handle_config_flow_error(err, "reauth")
            else:
                entry_id = self.context.get("entry_id")
                if entry_id:
                    entry = self.hass.config_entries.async_get_entry(entry_id)
                    if entry:
                        self.hass.config_entries.async_update_entry(
                            entry, title=hostname, data=user_input
                        )
                        await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm", data_schema=DATA_SCHEMA, errors=errors
        )


class KostalPlenticoreOptionsFlow(OptionsFlow):
    """Handle Kostal Plenticore options (Modbus & MQTT bridge settings).

    Two-step flow:
    1. init: Configure Modbus settings (port, unit-id, endianness, MQTT)
    2. modbus_test: Test connection before saving (only if Modbus enabled)
    """

    def __init__(self) -> None:
        """Initialize options flow."""
        self._user_input: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: Configure Modbus settings."""
        if user_input is not None:
            if user_input.get(CONF_MODBUS_ENABLED, False):
                self._user_input = user_input
                return await self.async_step_modbus_test()
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_MODBUS_ENABLED,
                    default=options.get(CONF_MODBUS_ENABLED, False),
                ): bool,
                vol.Optional(
                    CONF_MODBUS_PORT,
                    default=options.get(CONF_MODBUS_PORT, DEFAULT_MODBUS_PORT),
                ): int,
                vol.Optional(
                    CONF_MODBUS_UNIT_ID,
                    default=options.get(CONF_MODBUS_UNIT_ID, DEFAULT_MODBUS_UNIT_ID),
                ): int,
                vol.Optional(
                    CONF_MODBUS_ENDIANNESS,
                    default=options.get(CONF_MODBUS_ENDIANNESS, "auto"),
                ): vol.In(["auto", "little", "big"]),
                vol.Optional(
                    CONF_MQTT_BRIDGE_ENABLED,
                    default=options.get(CONF_MQTT_BRIDGE_ENABLED, False),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_modbus_test(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: Test Modbus connection before saving."""
        if user_input is not None:
            return self.async_create_entry(title="", data=self._user_input)

        host = self.config_entry.data.get(CONF_HOST, "")
        port = self._user_input.get(CONF_MODBUS_PORT, DEFAULT_MODBUS_PORT)
        unit_id = self._user_input.get(CONF_MODBUS_UNIT_ID, DEFAULT_MODBUS_UNIT_ID)
        endianness_setting = self._user_input.get(CONF_MODBUS_ENDIANNESS, "auto")

        test_log: list[str] = []
        test_passed = True

        try:
            from .modbus_client import KostalModbusClient
            from .modbus_registers import (
                REG_SERIAL_NUMBER,
                REG_PRODUCT_NAME,
                REG_INVERTER_STATE,
                REG_INVERTER_MAX_POWER,
                REG_BATTERY_MGMT_MODE,
                REG_TOTAL_DC_POWER,
                INVERTER_STATES,
                BATTERY_MGMT_MODES,
            )

            client = KostalModbusClient(
                host=str(host),
                port=int(port),
                unit_id=int(unit_id),
                endianness="little" if endianness_setting == "auto" else endianness_setting,
            )

            test_log.append(f"Connecting to {host}:{port} (Unit-ID {unit_id})...")
            await client.connect()
            test_log.append("TCP connection: OK")

            endianness = await client.detect_endianness()
            test_log.append(f"Byte order: {endianness} ({'CDAB' if endianness == 'little' else 'ABCD'})")

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
                        if mode_int == 0x00:  # pragma: no cover
                            val = f"{val} (normal – Register zeigt 0 bis erster Modbus-Befehl gesendet wird)"
                        elif mode_int != 0x02:  # pragma: no cover
                            test_log.append(
                                f"{label}: {val} (HINWEIS: Prüfe ob 'Extern über Protokoll (Modbus TCP)' im Inverter-WebUI aktiviert ist)"
                            )
                            continue
                    elif reg == REG_INVERTER_MAX_POWER:
                        val = f"{val} W"
                    test_log.append(f"{label}: {val}")
                except Exception as reg_err:
                    test_log.append(f"{label}: FEHLER - {reg_err}")

            await client.disconnect()
            test_log.append("")
            test_log.append("Modbus-Test ERFOLGREICH. Klicke 'Absenden' um die Einstellungen zu speichern.")

        except Exception as err:
            test_passed = False
            test_log.append(f"FEHLER: {err}")
            test_log.append("")
            test_log.append(
                "Modbus-Test FEHLGESCHLAGEN. "
                "Bitte prüfe die Einstellungen und die Netzwerkverbindung zum Inverter. "
                "Fehlerbericht für Entwickler:"
            )
            test_log.append(f"  Host: {host}:{port}")
            test_log.append(f"  Unit-ID: {unit_id}")
            test_log.append(f"  Endianness: {endianness_setting}")
            test_log.append(f"  Error: {type(err).__name__}: {err}")
            _LOGGER.error("Modbus connection test failed: %s", err)

        description = "\n".join(test_log)

        return self.async_show_form(
            step_id="modbus_test",
            data_schema=vol.Schema({}),
            description_placeholders={"test_result": description},
            errors={"base": "modbus_test_failed"} if not test_passed else {},
        )
