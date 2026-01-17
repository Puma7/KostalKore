"""Config flow for Kostal Plenticore Solar Inverter integration."""

from __future__ import annotations

import logging
from typing import Any, Final

from aiohttp.client_exceptions import ClientError
from pykoplenti import ApiClient, AuthenticationException, ApiException
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_BASE, CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_SERVICE_CODE, DOMAIN
from .helper import get_hostname_id

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA: Final[vol.Schema] = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_SERVICE_CODE): str,
    }
)


async def test_connection(hass: HomeAssistant, data: dict[str, Any]) -> str:
    """Test the connection to the inverter.

    Data has the keys from DATA_SCHEMA with values provided by the user.
    """
    session = async_get_clientsession(hass)
    async with ApiClient(session, data[CONF_HOST]) as client:
        await client.login(
            data[CONF_PASSWORD], service_code=data.get(CONF_SERVICE_CODE)
        )
        hostname_id = await get_hostname_id(client)
        values = await client.get_setting_values("scb:network", hostname_id)

    return values["scb:network"][hostname_id]


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
                hostname = await test_connection(self.hass, user_input)
            except AuthenticationException as ex:
                errors[CONF_PASSWORD] = "invalid_auth"
                _LOGGER.error("Authentication error: %s", ex)
            except (ClientError, TimeoutError):
                errors[CONF_HOST] = "cannot_connect"
            except ApiException as ex:
                _LOGGER.error("API error during connection test: %s", ex)
                errors[CONF_HOST] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors[CONF_BASE] = "unknown"
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
                hostname = await test_connection(self.hass, user_input)
            except AuthenticationException as ex:
                errors[CONF_PASSWORD] = "invalid_auth"
                _LOGGER.error("Authentication error: %s", ex)
            except (ClientError, TimeoutError):
                errors[CONF_HOST] = "cannot_connect"
            except ApiException as ex:
                _LOGGER.error("API error during connection test: %s", ex)
                errors[CONF_HOST] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors[CONF_BASE] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry=self._get_reconfigure_entry(), title=hostname, data=user_input
                )

        return self.async_show_form(
            step_id="reconfigure", data_schema=DATA_SCHEMA, errors=errors
        )
