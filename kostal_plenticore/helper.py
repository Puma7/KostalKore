"""Code to handle the Plenticore API."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, Final, cast

from aiohttp.client_exceptions import ClientError
from pykoplenti import ApiClient, ApiException

_LOGGER = logging.getLogger(__name__)

# Known hostname identifiers
_KNOWN_HOSTNAME_IDS: Final[tuple[str, ...]] = ("Network:Hostname", "Hostname")

# Performance constants
HOSTNAME_ID_TIMEOUT_SECONDS: Final[float] = 30.0

# Inverter state constants
INVERTER_STATE_OFF: Final[int] = 0
INVERTER_STATE_INIT: Final[int] = 1
INVERTER_STATE_ISOMEAS: Final[int] = 2
INVERTER_STATE_GRID_CHECK: Final[int] = 3
INVERTER_STATE_START_UP: Final[int] = 4
INVERTER_STATE_FEED_IN: Final[int] = 6
INVERTER_STATE_THROTTLED: Final[int] = 7
INVERTER_STATE_EXT_SWITCH_OFF: Final[int] = 8
INVERTER_STATE_UPDATE: Final[int] = 9
INVERTER_STATE_STANDBY: Final[int] = 10
INVERTER_STATE_GRID_SYNC: Final[int] = 11
INVERTER_STATE_GRID_PRE_CHECK: Final[int] = 12
INVERTER_STATE_GRID_SWITCH_OFF: Final[int] = 13
INVERTER_STATE_OVERHEATING: Final[int] = 14
INVERTER_STATE_SHUTDOWN: Final[int] = 15
INVERTER_STATE_IMPROPER_DC_VOLTAGE: Final[int] = 16
INVERTER_STATE_ESB: Final[int] = 17
INVERTER_STATE_BATTERY_CHARGING: Final[int] = 18
INVERTER_STATE_BATTERY_DISCHARGING: Final[int] = 19
INVERTER_STATE_MAINTENANCE: Final[int] = 20

# Energy manager state constants
EM_STATE_IDLE: Final[int] = 0
EM_STATE_EMERGENCY_BATTERY_CHARGE: Final[int] = 2
EM_STATE_WINTER_MODE_STEP_1: Final[int] = 8
EM_STATE_WINTER_MODE_STEP_2: Final[int] = 16
EM_STATE_WINTER_MODE_STEP_3: Final[int] = 32
EM_STATE_SELF_CONSUMPTION: Final[int] = 64
EM_STATE_PEAK_SHAVING: Final[int] = 128
EM_STATE_EXPORT_LIMIT: Final[int] = 256
EM_STATE_BATTERY_MANAGEMENT: Final[int] = 512


def _safe_int_conversion(state: str) -> int | str:
    """
    Safely convert string to int with fallback.
    
    Args:
        state: String to convert
        
    Returns:
        Integer if successful, original string otherwise
    """
    try:
        return int(state)
    except (TypeError, ValueError):
        try:
            # Handle float-like strings (e.g., "6.0")
            return int(float(state))
        except (TypeError, ValueError):
            return state


def _safe_float_conversion(state: str) -> float | str:
    """
    Safely convert string to float with fallback.
    
    Args:
        state: String to convert
        
    Returns:
        Float if successful, original string otherwise
    """
    try:
        return float(state)
    except (TypeError, ValueError):
        return state


def _handle_format_error(state: str, formatter_name: str) -> str:
    """
    Handle formatting errors consistently with logging.
    
    Args:
        state: Input state that caused the error
        formatter_name: Name of the formatter for logging
        
    Returns:
        Original state as fallback
    """
    _LOGGER.debug("Error in %s formatter with input: %s", formatter_name, state)
    return state


class PlenticoreDataFormatter:
    """Provides method to format values of process or settings data."""

    INVERTER_STATES: Final[dict[int, str]] = {
        INVERTER_STATE_OFF: "Off",
        INVERTER_STATE_INIT: "Init",
        INVERTER_STATE_ISOMEAS: "IsoMEas",
        INVERTER_STATE_GRID_CHECK: "GridCheck",
        INVERTER_STATE_START_UP: "StartUp",
        INVERTER_STATE_FEED_IN: "FeedIn",
        INVERTER_STATE_THROTTLED: "Throttled",
        INVERTER_STATE_EXT_SWITCH_OFF: "ExtSwitchOff",
        INVERTER_STATE_UPDATE: "Update",
        INVERTER_STATE_STANDBY: "Standby",
        INVERTER_STATE_GRID_SYNC: "GridSync",
        INVERTER_STATE_GRID_PRE_CHECK: "GridPreCheck",
        INVERTER_STATE_GRID_SWITCH_OFF: "GridSwitchOff",
        INVERTER_STATE_OVERHEATING: "Overheating",
        INVERTER_STATE_SHUTDOWN: "Shutdown",
        INVERTER_STATE_IMPROPER_DC_VOLTAGE: "ImproperDcVoltage",
        INVERTER_STATE_ESB: "ESB",
        INVERTER_STATE_BATTERY_CHARGING: "BatteryCharging",
        INVERTER_STATE_BATTERY_DISCHARGING: "BatteryDischarging",
        INVERTER_STATE_MAINTENANCE: "Maintenance",
    }

    EM_STATES: Final[dict[int, str]] = {
        EM_STATE_IDLE: "Idle",
        1: "n/a",
        EM_STATE_EMERGENCY_BATTERY_CHARGE: "Emergency Battery Charge",
        4: "n/a",
        EM_STATE_WINTER_MODE_STEP_1: "Winter Mode Step 1",
        EM_STATE_WINTER_MODE_STEP_2: "Winter Mode Step 2",
        EM_STATE_WINTER_MODE_STEP_3: "Winter Mode Step 3",
        EM_STATE_SELF_CONSUMPTION: "Self Consumption",
        EM_STATE_PEAK_SHAVING: "Peak Shaving",
        EM_STATE_EXPORT_LIMIT: "Export Limit",
        EM_STATE_BATTERY_MANAGEMENT: "Battery Management",
    }

    @classmethod
    def get_method(cls, name: str) -> Callable[[Any], Any]:
        """Return a callable formatter of the given name."""
        return cast(Callable[[Any], Any], getattr(cls, name))

    @staticmethod
    def format_round(state: str) -> int | str:
        """Return the given state value as rounded integer."""
        try:
            return round(float(state))
        except (TypeError, ValueError):
            return _handle_format_error(state, "round")

    @staticmethod
    def format_round_back(value: float) -> str:
        """Return a rounded integer value from a float."""
        try:
            if isinstance(value, float) and value.is_integer():
                int_value = int(value)
            elif isinstance(value, int):
                int_value = value
            else:
                int_value = round(value)

            return str(int_value)
        except (TypeError, ValueError):
            return ""

    @staticmethod
    def format_float(state: str) -> float | str:
        """Return the given state value as float rounded to three decimal places."""
        try:
            return round(float(state), 3)
        except (TypeError, ValueError):
            return _handle_format_error(state, "float")

    @staticmethod
    def format_float_back(value: float) -> str:
        """Return the given float value as string for the inverter API."""
        try:
            return str(round(float(value), 3))
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def format_energy(state: str) -> float | str:
        """Return the given state value as energy value, scaled to kWh."""
        try:
            return round(float(state) / 1000, 1)
        except (TypeError, ValueError):
            return _handle_format_error(state, "energy")

    @staticmethod
    def format_inverter_state(state: str) -> str | None:
        """Return a readable string of the inverter state."""
        value = _safe_int_conversion(state)
        if isinstance(value, str):
            return value

        # Try to get the known state, fallback to "Unknown State X" for unknown codes
        known_state = PlenticoreDataFormatter.INVERTER_STATES.get(value)
        if known_state is not None:
            return known_state
        return f"Unknown State {value}"

    @staticmethod
    def format_em_manager_state(state: str) -> str | None:
        """Return a readable state of the energy manager."""
        value = _safe_int_conversion(state)
        if isinstance(value, str):
            return value

        # Try to get the known state, fallback to "Unknown EM State X" for unknown codes
        known_state = PlenticoreDataFormatter.EM_STATES.get(value)
        if known_state is not None:
            return known_state
        return f"Unknown EM State {value}"

    @staticmethod
    def format_battery_management_mode(state: str) -> str:
        """Return readable battery management mode."""
        modes: Final[dict[int, str]] = {
            0x00: "No external battery management",
            0x01: "External management via digital I/O",
            0x02: "External management via MODBUS"
        }
        try:
            return modes.get(int(state), f"Unknown mode: {state}")
        except (TypeError, ValueError):
            return state

    @staticmethod
    def format_sensor_type(state: str) -> str:
        """Return readable sensor type."""
        sensors: Final[dict[int, str]] = {
            0x00: "SDM 630 (B+G E-Tech)",
            0x01: "B-Control EM-300 LR",
            0x02: "Reserved",
            0x03: "KOSTAL Smart Energy Meter",
            0xFF: "No sensor"
        }
        try:
            return sensors.get(int(state), f"Unknown sensor: {state}")
        except (TypeError, ValueError):
            return state

    @staticmethod
    def format_string(state: str) -> str:
        """Return the string value as-is."""
        return state

    @staticmethod
    def format_battery_type(state: str) -> str:
        battery_types: Final[dict[int, str]] = {
            0x0000: "No battery (PV-Functionality)",
            0x0002: "PIKO Battery Li",
            0x0004: "BYD",
            0x0008: "BMZ",
            0x0010: "AXIstorage Li SH",
            0x0040: "LG",
            0x0200: "Pyontech Force H",
            0x0400: "AXIstorage Li SV",
            0x1000: "Dyness Tower / TowerPro",
            0x2000: "VARTA.wall",
            0x4000: "ZYC",
        }
        try:
            value = int(state)
            return battery_types.get(value, f"Unknown battery type: {state}")
        except (TypeError, ValueError):
            return state

    @staticmethod
    def format_pssb_fuse_state(state: str) -> str:
        """Return readable PSSB fuse state."""
        fuse_states: Final[dict[int, str]] = {
            0x00: "Fuse fail",
            0x01: "Fuse ok",
            0xFF: "Unchecked",
        }
        try:
            value = int(state)
            return fuse_states.get(value, f"Unknown fuse state: {state}")
        except (TypeError, ValueError):
            return state


async def get_hostname_id(client: ApiClient) -> str:
    """Check for known existing hostname ids with timeout protection."""
    try:
        all_settings = await asyncio.wait_for(
            client.get_settings(),
            timeout=HOSTNAME_ID_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        _LOGGER.error("Timeout getting settings for hostname ID")
        raise ApiException("Timeout getting settings")  # type: ignore[no-untyped-call]
    except (ApiException, ClientError, TimeoutError) as err:
        _LOGGER.error("Could not get settings for hostname ID: %s", err)
        raise ApiException(f"Could not get settings: {err}") from err  # type: ignore[no-untyped-call]
    
    network_settings = all_settings.get("scb:network", [])
    if not network_settings:
        raise ApiException("No network settings found in API response")  # type: ignore[no-untyped-call]
    
    for entry in network_settings:
        if entry.id in _KNOWN_HOSTNAME_IDS:
            return entry.id
    
    raise ApiException("Hostname identifier not found in KNOWN_HOSTNAME_IDS")  # type: ignore[no-untyped-call]
