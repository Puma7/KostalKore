"""The Kostal Plenticore Solar Inverter integration.

This integration provides monitoring and control capabilities for Kostal Plenticore
solar inverters through Home Assistant. It supports real-time data collection,
settings control, and calculated sensors.

Key Features:
- Real-time monitoring of power generation, consumption, and battery status
- Control of inverter settings and operating modes
- Calculated sensors for derived metrics (e.g., PV sum power, battery efficiency)
- Request deduplication and caching to reduce API load
- Comprehensive diagnostic support
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Final

from aiohttp.client_exceptions import ClientError
from pykoplenti import ApiException

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .coordinator import Plenticore, PlenticoreConfigEntry
from .helper import parse_modbus_exception
from .repairs import clear_issue

_LOGGER = logging.getLogger(__name__)

# Platform constants
PLATFORMS: Final[list[Platform]] = [
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

# Performance constants
SETUP_TIMEOUT_SECONDS: Final[float] = 30.0
UNLOAD_TIMEOUT_SECONDS: Final[float] = 5.0
PLATFORM_SETUP_TIMEOUT_SECONDS: Final[float] = 30.0

# Performance metrics constants
MEMORY_CLEANUP_MAX_MS: Final[int] = 500


def _handle_init_error(err: Exception, operation: str) -> bool:
    """
    Handle initialization errors with appropriate logging.

    Args:
        err: Exception that occurred
        operation: Description of the operation being performed

    Returns:
        False to indicate failure
    """
    if isinstance(err, ApiException):
        modbus_err = parse_modbus_exception(err)
        _LOGGER.error("API error during %s: %s", operation, modbus_err.message)
    elif isinstance(err, TimeoutError):
        _LOGGER.warning("Timeout during %s", operation)
    elif isinstance(err, (ClientError, asyncio.TimeoutError)):
        _LOGGER.error("Network error during %s: %s", operation, err)
    else:
        _LOGGER.error("Unexpected error during %s: %s", operation, err)

    return False


def _log_setup_metrics(start_time: float, setup_success: bool) -> None:
    """
    Log setup performance metrics.

    Args:
        start_time: Setup start time
        setup_success: Whether setup was successful
    """
    setup_time = time.time() - start_time
    if setup_success:
        _LOGGER.info("Kostal Plenticore setup completed in %.2fs", setup_time)
    else:
        _LOGGER.warning("Kostal Plenticore setup failed after %.2fs", setup_time)


async def async_setup_entry(hass: HomeAssistant, entry: PlenticoreConfigEntry) -> bool:
    """Set up the Kostal Plenticore integration.

    Initializes the API client, authenticates with the inverter,
    and forwards platform setup (sensors, switches, numbers, selects).
    """
    start_time = time.time()

    plenticore = Plenticore(hass, entry)

    try:
        setup_success = await asyncio.wait_for(
            plenticore.async_setup(), timeout=SETUP_TIMEOUT_SECONDS
        )
    except Exception as err:
        setup_success = _handle_init_error(err, "setup")

    if not setup_success:
        _log_setup_metrics(start_time, False)
        return False

    clear_issue(hass, "auth_failed")
    clear_issue(hass, "api_unreachable")
    clear_issue(hass, "inverter_busy")
    clear_issue(hass, "installer_required")

    entry.runtime_data = plenticore

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception as err:
        _handle_init_error(err, "platform setup")
        _log_setup_metrics(start_time, False)
        return False

    _log_setup_metrics(start_time, True)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PlenticoreConfigEntry) -> bool:
    """
    Unload the Kostal Plenticore integration with graceful cleanup.

    This function handles the graceful shutdown of the integration,
    ensuring all resources are properly cleaned up and the inverter
    connection is properly terminated.

    Unload Process:
    1. Unload all platforms (sensors, switches, numbers, selects)
    2. Logout from inverter with timeout protection
    3. Clean up resources and connections
    4. Monitor cleanup performance

    Performance Features:
    - Concurrent platform unloading
    - Timeout protection for logout operations
    - Resource cleanup monitoring
    """
    start_time = time.time()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        try:
            await asyncio.wait_for(
                entry.runtime_data.async_unload(), timeout=UNLOAD_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            _LOGGER.warning("Timeout during inverter logout")
        except ApiException as err:
            _LOGGER.error("Error logging out from inverter: %s", err)
        except Exception as err:
            _LOGGER.error("Unexpected error during inverter logout: %s", err)

    cleanup_time = time.time() - start_time
    if cleanup_time > MEMORY_CLEANUP_MAX_MS / 1000:
        _LOGGER.warning(
            "Cleanup took %.2fs (expected < %.1fs)",
            cleanup_time,
            MEMORY_CLEANUP_MAX_MS / 1000,
        )

    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: PlenticoreConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow removal of stale devices from the device registry.

    This integration creates one device per inverter (config entry).
    If the device no longer exists, allow HA to remove it.
    """
    return True
