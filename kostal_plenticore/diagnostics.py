"""Diagnostics support for Kostal Plenticore."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Final

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.const import ATTR_IDENTIFIERS, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .const import CONF_SERVICE_CODE
from .const_ids import ModuleId, SettingId, STRING_FEATURE_TEMPLATE, string_feature_id
from .coordinator import PlenticoreConfigEntry

from pykoplenti import ApiException

# Import MODBUS exception handling from coordinator
from .helper import parse_modbus_exception

import logging
_LOGGER = logging.getLogger(__name__)

# Data redaction constants
TO_REDACT: Final[set[str]] = {CONF_PASSWORD, CONF_SERVICE_CODE}

# Diagnostics constants
DEVICES_LOCAL_MODULE: Final[str] = ModuleId.DEVICES_LOCAL
STRING_COUNT_SETTING: Final[str] = SettingId.STRING_COUNT
STRING_FEATURE_PATTERN: Final[str] = STRING_FEATURE_TEMPLATE
DIAGNOSTICS_TIMEOUT_SECONDS: Final[float] = 30.0


def _handle_diagnostics_error(err: Exception, operation: str) -> Any:
    """
    Centralized error handling for diagnostics operations.
    
    Args:
        err: Exception that occurred
        operation: Description of the operation being performed
        
    Returns:
        Appropriate default value for the operation
    """
    if isinstance(err, ApiException):
        modbus_err = parse_modbus_exception(err)
        _LOGGER.warning("Could not get %s for diagnostics: %s", operation, modbus_err.message)
        if operation == "version" or operation == "me":
            return "Unknown"
        elif operation == "string_count":
            return 0
        else:
            return {}
    elif isinstance(err, (ValueError, AttributeError)):
        _LOGGER.warning("Could not parse %s for diagnostics: %s", operation, err)
        if operation == "string_count":
            return 0
        else:
            return {}
    elif isinstance(err, asyncio.TimeoutError):
        _LOGGER.warning("Timeout getting %s for diagnostics", operation)
        if operation == "version" or operation == "me":
            return "Unknown"
        elif operation == "string_count":
            return 0
        else:
            return {}
    else:
        _LOGGER.error("Unexpected error getting %s for diagnostics: %s", operation, err)
        if operation == "version" or operation == "me":
            return "Unknown"
        elif operation == "string_count":
            return 0
        else:
            return {}


async def _get_diagnostics_data_safe(
    plenticore: Any,
    operation: str,
    fetch_func: Callable[..., Awaitable[Any]],
    default_value: Any = None,
) -> Any:
    """
    Get diagnostics data with timeout and error handling.
    
    Args:
        plenticore: Plenticore client instance
        operation: Description of the operation
        fetch_func: Async function to fetch data
        default_value: Default value if operation fails
        
    Returns:
        Fetched data or default value
    """
    try:
        result = await asyncio.wait_for(fetch_func(), timeout=DIAGNOSTICS_TIMEOUT_SECONDS)
        return result
    except Exception as err:
        return _handle_diagnostics_error(err, operation) or default_value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: PlenticoreConfigEntry
) -> dict[str, dict[str, Any]]:
    """Return diagnostics for a config entry."""
    data: dict[str, dict[str, Any]] = {"config_entry": async_redact_data(config_entry.as_dict(), TO_REDACT)}

    plenticore = config_entry.runtime_data

    # Get information from Kostal Plenticore library with timeout protection
    available_process_data = await _get_diagnostics_data_safe(
        plenticore, "process data", plenticore.client.get_process_data
    )
    
    available_settings_data = await _get_diagnostics_data_safe(
        plenticore, "settings data", plenticore.client.get_settings
    )
    
    version = await _get_diagnostics_data_safe(
        plenticore, "version", plenticore.client.get_version, "Unknown"
    )
    
    me = await _get_diagnostics_data_safe(
        plenticore, "me", plenticore.client.get_me, "Unknown"
    )
    
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
    string_count_setting = await _get_diagnostics_data_safe(
        plenticore, 
        "string count", 
        lambda: plenticore.client.get_setting_values(DEVICES_LOCAL_MODULE, STRING_COUNT_SETTING)
    )
    
    string_count = 0
    try:
        string_count = int(
            string_count_setting.get(DEVICES_LOCAL_MODULE, {})
            .get(STRING_COUNT_SETTING, 0)
        )
    except (ValueError, AttributeError):
        string_count = 0

    # Generate feature IDs dynamically based on string count
    feature_ids = [STRING_FEATURE_PATTERN.format(index=idx) for idx in range(string_count)]
    if feature_ids:
        configuration_settings = await _get_diagnostics_data_safe(
            plenticore,
            "configuration settings",
            lambda: plenticore.client.get_setting_values(DEVICES_LOCAL_MODULE, feature_ids)
        )
    else:
        configuration_settings = {}

    data["configuration"] = {
        **configuration_settings,
    }

    device_info = {**plenticore.device_info}
    device_info[ATTR_IDENTIFIERS] = REDACTED  # contains serial number
    data["device"] = device_info

    return data
