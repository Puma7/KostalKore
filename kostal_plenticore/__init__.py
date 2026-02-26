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

from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_MODBUS_ENABLED,
    CONF_MODBUS_ENDIANNESS,
    CONF_MODBUS_PORT,
    CONF_MODBUS_UNIT_ID,
    CONF_MQTT_BRIDGE_ENABLED,
    DEFAULT_MODBUS_PORT,
    DEFAULT_MODBUS_UNIT_ID,
    DOMAIN,
)
from .coordinator import Plenticore, PlenticoreConfigEntry
from .helper import parse_modbus_exception
from .battery_chemistry import detect_chemistry
from .degradation_tracker import DegradationTracker
from .diagnostics_engine import DiagnosticsEngine
from .fire_safety import FireSafetyMonitor
from .health_monitor import InverterHealthMonitor
from .longevity_advisor import LongevityAdvisor
from .modbus_client import KostalModbusClient, ModbusClientError
from .modbus_coordinator import ModbusDataUpdateCoordinator
from .mqtt_bridge import KostalMqttBridge
from .repairs import clear_issue

_LOGGER = logging.getLogger(__name__)

# Platform constants
PLATFORMS: Final[list[Platform]] = [
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

MODBUS_PLATFORMS: Final[list[Platform]] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
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

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    # Optional Modbus TCP setup
    modbus_coordinator = None
    mqtt_bridge = None
    if entry.options.get(CONF_MODBUS_ENABLED, False):
        host = entry.data[CONF_HOST]
        port = entry.options.get(CONF_MODBUS_PORT, DEFAULT_MODBUS_PORT)
        unit_id = entry.options.get(CONF_MODBUS_UNIT_ID, DEFAULT_MODBUS_UNIT_ID)
        endianness = entry.options.get(CONF_MODBUS_ENDIANNESS, "auto")

        modbus_client = KostalModbusClient(
            host=host,
            port=port,
            unit_id=unit_id,
            endianness="little" if endianness == "auto" else endianness,
        )
        modbus_coordinator = ModbusDataUpdateCoordinator(hass, modbus_client)
        try:
            await modbus_coordinator.async_setup()
            if endianness == "auto":
                await modbus_client.detect_endianness()
            _LOGGER.info("Modbus TCP connected to %s:%s", host, port)
        except Exception as err:
            _LOGGER.warning(
                "Modbus TCP setup failed: %s (REST API still active)", err
            )
            modbus_coordinator = None

        if modbus_coordinator and entry.options.get(
            CONF_MQTT_BRIDGE_ENABLED, False
        ):
            device_id = plenticore.device_info.get("serial_number", entry.entry_id)
            if isinstance(device_id, tuple):
                device_id = str(entry.entry_id)
            mqtt_bridge = KostalMqttBridge(
                hass, modbus_coordinator, str(device_id)
            )
            await mqtt_bridge.async_start()

    # Health + Fire Safety + Degradation monitors
    health_monitor = None
    fire_safety = None
    degradation_tracker = None
    if modbus_coordinator is not None:
        health_monitor = InverterHealthMonitor()
        fire_safety = FireSafetyMonitor()
        degradation_tracker = DegradationTracker()

        @callback
        def _feed_health_data() -> None:  # pragma: no cover
            data = modbus_coordinator.data
            if data:
                health_monitor.update_from_modbus(data)
                degradation_tracker.update_from_modbus(data)
                new_alerts = fire_safety.analyze(data)
                if new_alerts:
                    from .notifications import notify_safety_alert, notify_safety_clear
                    for alert in new_alerts:
                        hass.async_create_task(
                            notify_safety_alert(
                                hass, alert.risk_level, alert.title,
                                alert.detail, alert.action,
                            )
                        )
                elif fire_safety.alert_count == 0 and fire_safety._total_polls > 0:
                    from .notifications import notify_safety_clear
                    hass.async_create_task(notify_safety_clear(hass))

        modbus_coordinator.async_add_listener(_feed_health_data)
        fire_safety._total_polls = 0

    diagnostics_engine = None
    longevity_advisor = None
    if health_monitor is not None and fire_safety is not None:
        diagnostics_engine = DiagnosticsEngine(health_monitor, fire_safety)
        bat_type = modbus_coordinator.device_info_data.get("battery_type") if modbus_coordinator else None
        bat_type_int = None  # pragma: no cover
        if bat_type is not None:  # pragma: no cover
            try:
                bat_type_int = int(bat_type)
            except (TypeError, ValueError):
                pass
        bat_thresholds = detect_chemistry(bat_type_int)
        longevity_advisor = LongevityAdvisor(health_monitor, bat_thresholds)
        _LOGGER.info("Battery chemistry: %s (%s)", bat_thresholds.chemistry, bat_thresholds.chemistry_full)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "modbus_coordinator": modbus_coordinator,
        "mqtt_bridge": mqtt_bridge,
        "health_monitor": health_monitor,
        "fire_safety": fire_safety,
        "degradation_tracker": degradation_tracker,
        "diagnostics_engine": diagnostics_engine,
        "longevity_advisor": longevity_advisor,
    }

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        if modbus_coordinator is not None:
            try:
                await hass.config_entries.async_forward_entry_setups(entry, MODBUS_PLATFORMS)
            except Exception as modbus_err:  # pragma: no cover
                _LOGGER.warning("Modbus platform setup incomplete: %s", modbus_err)
    except Exception as err:
        _handle_init_error(err, "platform setup")
        _log_setup_metrics(start_time, False)
        return False

    _log_setup_metrics(start_time, True)
    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: PlenticoreConfigEntry
) -> None:
    """Reload integration when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: PlenticoreConfigEntry) -> bool:
    """
    Unload the Kostal Plenticore integration with graceful cleanup.

    This function handles the graceful shutdown of the integration,
    ensuring all resources are properly cleaned up and the inverter
    connection is properly terminated.

    Unload Process:
    1. Clean up Modbus + MQTT bridge
    2. Unload all platforms (sensors, switches, numbers, selects)
    3. Logout from inverter with timeout protection
    4. Clean up resources and connections
    5. Monitor cleanup performance

    Performance Features:
    - Concurrent platform unloading
    - Timeout protection for logout operations
    - Resource cleanup monitoring
    """
    start_time = time.time()

    # Clean up Modbus + MQTT bridge
    entry_data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, {})
    mqtt_bridge = entry_data.get("mqtt_bridge")
    if mqtt_bridge:
        await mqtt_bridge.async_stop()
    modbus_coordinator = entry_data.get("modbus_coordinator")
    if modbus_coordinator:
        try:
            await hass.config_entries.async_unload_platforms(entry, MODBUS_PLATFORMS)
        except Exception:  # pragma: no cover
            _LOGGER.debug("Modbus platform unload incomplete (non-fatal)")
        await modbus_coordinator.async_shutdown()

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
