"""Diagnostics support for Kostal Plenticore."""

from __future__ import annotations

from typing import Any, Final

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.const import ATTR_IDENTIFIERS, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .coordinator import PlenticoreConfigEntry

from pykoplenti import ApiException

# Import MODBUS exception handling from coordinator
from .coordinator import _parse_modbus_exception

import logging
_LOGGER = logging.getLogger(__name__)

TO_REDACT: Final[set[str]] = {CONF_PASSWORD}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: PlenticoreConfigEntry
) -> dict[str, dict[str, Any]]:
    """Return diagnostics for a config entry."""
    data: dict[str, dict[str, Any]] = {"config_entry": async_redact_data(config_entry.as_dict(), TO_REDACT)}

    plenticore = config_entry.runtime_data

    # Get information from Kostal Plenticore library
    try:
        available_process_data = await plenticore.client.get_process_data()
    except ApiException as err:
        modbus_err = _parse_modbus_exception(err)
        _LOGGER.warning("Could not get process data for diagnostics: %s", modbus_err.message)
        available_process_data = {}
    
    try:
        available_settings_data = await plenticore.client.get_settings()
    except ApiException as err:
        modbus_err = _parse_modbus_exception(err)
        _LOGGER.warning("Could not get settings data for diagnostics: %s", modbus_err.message)
        available_settings_data = {}
    
    try:
        version = str(await plenticore.client.get_version())
    except ApiException as err:
        modbus_err = _parse_modbus_exception(err)
        _LOGGER.warning("Could not get version for diagnostics: %s", modbus_err.message)
        version = "Unknown"
    
    try:
        me = str(await plenticore.client.get_me())
    except ApiException as err:
        modbus_err = _parse_modbus_exception(err)
        _LOGGER.warning("Could not get me for diagnostics: %s", modbus_err.message)
        me = "Unknown"
    
    data["client"] = {
        "version": version,
        "me": me,
        "available_process_data": available_process_data,
        "available_settings_data": {
            module_id: [str(setting) for setting in settings]
            for module_id, settings in available_settings_data.items()
        },
    }

    # Add important information how the inverter is configured
    try:
        string_count_setting = await plenticore.client.get_setting_values(
            "devices:local", "Properties:StringCnt"
        )
    except ApiException as err:
        modbus_err = _parse_modbus_exception(err)
        _LOGGER.warning("Could not get string count for diagnostics: %s", modbus_err.message)
        string_count_setting = {}
    
    try:
        string_count = int(
            string_count_setting.get("devices:local", {})
            .get("Properties:StringCnt", 0)
        )
    except (ValueError, AttributeError):
        string_count = 0

    try:
        configuration_settings = await plenticore.client.get_setting_values(
            "devices:local",
        (
            "Properties:StringCnt",
            *(f"Properties:String{idx}Features" for idx in range(string_count)),
        ),
        )
    except ApiException as err:
        modbus_err = _parse_modbus_exception(err)
        _LOGGER.warning("Could not get configuration settings for diagnostics: %s", modbus_err.message)
        configuration_settings = {}

    data["configuration"] = {
        **configuration_settings,
    }

    device_info = {**plenticore.device_info}
    device_info[ATTR_IDENTIFIERS] = REDACTED  # contains serial number
    data["device"] = device_info

    return data
