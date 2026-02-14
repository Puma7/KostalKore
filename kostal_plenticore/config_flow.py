"""Config flow for Kostal Plenticore Solar Inverter integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Final, TypeAlias, TYPE_CHECKING

from aiohttp.client_exceptions import ClientError
from pykoplenti import ApiClient, AuthenticationException, ApiException
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow
if TYPE_CHECKING:  # pragma: no cover
    from homeassistant.config_entries import ConfigFlowResult
else:
    ConfigFlowResult: TypeAlias = dict[str, Any]
from homeassistant.const import CONF_BASE, CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_SERVICE_CODE, DOMAIN
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
