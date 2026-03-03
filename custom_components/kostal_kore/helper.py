"""Code to handle the Plenticore API."""

from __future__ import annotations

import asyncio
import logging
import math
import secrets
from collections.abc import Callable
from typing import Any, Final, cast

from homeassistant.core import HomeAssistant

from .const import CONF_INSTALLER_ACCESS, CONF_SERVICE_CODE, DOMAIN
from .const_ids import ModuleId, SettingId
from .repairs import clear_issue, create_installer_required_issue

from aiohttp.client_exceptions import ClientError
from pykoplenti import ApiClient, ApiException

_LOGGER = logging.getLogger(__name__)

# Known hostname identifiers
_KNOWN_HOSTNAME_IDS: Final[tuple[str, ...]] = (SettingId.HOSTNAME, "Hostname")

# Performance constants
HOSTNAME_ID_TIMEOUT_SECONDS: Final[float] = 30.0
DEFAULT_CONFIRMATION_CODE_LEN: Final[int] = 6
DEFAULT_CONFIRMATION_CODE_ALPHABET: Final[str] = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def integration_entry_store(hass: HomeAssistant, entry_id: str) -> dict[str, Any]:
    """Return mutable per-entry integration state store."""
    return cast(dict[str, Any], hass.data.setdefault(DOMAIN, {}).setdefault(entry_id, {}))


def generate_confirmation_code(
    *,
    length: int = DEFAULT_CONFIRMATION_CODE_LEN,
    alphabet: str = DEFAULT_CONFIRMATION_CODE_ALPHABET,
) -> str:
    """Generate a short human-friendly confirmation code."""
    return "".join(secrets.choice(alphabet) for _ in range(length))


def normalize_isolation_resistance_ohm(
    value: Any,
    *,
    pv_active: bool,
    inverter_state: int | None = None,
) -> float | None:
    """Normalize isolation resistance to ohm across firmware unit variants.

    Some firmware variants report isolation resistance in kOhm while others use
    ohm. When PV is actively producing and a very small value (<10k) is seen, it
    is treated as kOhm and converted to ohm.
    """
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric) or math.isinf(numeric):
        return None
    if not pv_active:
        return numeric
    if inverter_state in (0, 1, 10, 15):
        return numeric
    if 0 < abs(numeric) < 10_000:
        return numeric * 1000.0
    return numeric

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
            value = round(float(state) / 1000, 1)
            if value < 0:
                # Recorder rejects negative values for total_increasing sensors.
                return 0.0
            return value
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
            0x02: "External management via MODBUS",
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
            0xFF: "No sensor",
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
            client.get_settings(), timeout=HOSTNAME_ID_TIMEOUT_SECONDS
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


class ModbusException(Exception):
    """Base exception for MODBUS communication errors."""

    def __init__(self, message: str, exception_code: int | None = None) -> None:
        super().__init__(message)
        self.exception_code = exception_code
        self.message = message


class ModbusIllegalFunctionError(ModbusException):
    """MODBUS illegal function exception (0x01)."""

    def __init__(self, function_code: int) -> None:
        super().__init__(
            f"Function code 0x{function_code:02X} not supported by inverter",
            0x01,
        )


class ModbusIllegalDataAddressError(ModbusException):
    """MODBUS illegal data address exception (0x02)."""

    def __init__(self) -> None:
        super().__init__("Register address not valid for this inverter model", 0x02)


class ModbusIllegalDataValueError(ModbusException):
    """MODBUS illegal data value exception (0x03)."""

    def __init__(self) -> None:
        super().__init__("Invalid value provided", 0x03)


class ModbusServerDeviceFailureError(ModbusException):
    """MODBUS server device failure exception (0x04)."""

    def __init__(self) -> None:
        super().__init__("Inverter internal error during operation", 0x04)


class ModbusServerDeviceBusyError(ModbusException):
    """MODBUS server device busy exception (0x06)."""

    def __init__(self) -> None:
        super().__init__("Inverter busy processing long command, retry later", 0x06)


class ModbusMemoryParityError(ModbusException):
    """MODBUS memory parity error exception (0x08)."""

    def __init__(self) -> None:
        super().__init__("Inverter memory consistency check failed", 0x08)


def parse_modbus_exception(api_exception: ApiException) -> ModbusException:
    """Parse an ApiException into a specific ModbusException.

    Uses specific multi-word phrases to avoid false-positive matches
    on common single words like "value" or "failure".
    """
    error_msg = str(api_exception).lower()

    if "illegal function" in error_msg:
        return ModbusIllegalFunctionError(0x01)
    if "illegal data address" in error_msg:
        return ModbusIllegalDataAddressError()
    if "illegal data value" in error_msg:
        return ModbusIllegalDataValueError()
    if "server device failure" in error_msg:
        return ModbusServerDeviceFailureError()
    if "server device busy" in error_msg:
        return ModbusServerDeviceBusyError()
    if "memory parity" in error_msg:
        return ModbusMemoryParityError()

    return ModbusException(f"MODBUS communication error: {api_exception}")


def requires_installer_service_code(data_id: str) -> bool:
    """Return True if a data ID requires installer/service code."""
    advanced_controls = (
        "ChargePower",
        "ChargeCurrent",
        "MaxChargePower",
        "MaxDischargePower",
        "TimeUntilFallback",
        "DigitalOutputs:Customer:",
        "Battery:BackupMode:Enable",
    )
    return any(control in data_id for control in advanced_controls)


def is_battery_control(data_id: str) -> bool:
    """Return True if a data ID targets battery control settings."""
    return data_id.startswith("Battery:")


# Explicit allowlist of writable setting prefixes/IDs used by this integration.
# Unknown targets are blocked by default.
_ALLOWED_WRITE_PREFIXES: Final[tuple[str, ...]] = (
    "Battery:",
    "EnergyMgmt:",
    "EnergyManagement:",
    "ActivePower:",
    "ReactivePower:",
    "DigitalOut",
    "DigitalOutputs:Customer:",
    "Generator:",
    "Inverter:",
    "POfF:",
    "POfU:",
    "LvrtHvrt:",
    "Pave:",
)

_ALLOWED_WRITE_IDS: Final[frozenset[str]] = frozenset(
    {
        SettingId.SHADOW_MGMT_ENABLE,
        SettingId.BATTERY_MIN_SOC,
        SettingId.BATTERY_MIN_SOC_REL,
        SettingId.BATTERY_MIN_HOME_CONSUMPTION,
        SettingId.BATTERY_MIN_HOME_CONSUMPTION_LEGACY,
        "Battery:BackupMode:Enable",
        "Battery:SmartBatteryControl:Enable",
        "Battery:Strategy",
    }
)

# High-impact controls require explicit temporary arming.
_ARM_REQUIRED_PREFIXES: Final[tuple[str, ...]] = (
    "DigitalOutputs:Customer:",
)

_ARM_REQUIRED_IDS: Final[frozenset[str]] = frozenset(
    {
        "Battery:BackupMode:Enable",
        "Battery:ExternControl:AcPowerAbs",
        "Battery:ChargePowerAcAbsolute",
        "Battery:ChargePowerDcAbs",
        "Battery:ChargePower",
        "Battery:ChargeCurrent",
        "Battery:MaxChargePowerG3",
        "Battery:MaxDischargePowerG3",
        "Battery:Limit:Charge_P",
        "Battery:Limit:Discharge_P",
        "Battery:Limit:FallbackCharge_P",
        "Battery:Limit:FallbackDischarge_P",
        "Battery:Limit:FallbackTime",
    }
)
_ARM_REQUIRED_FRAGMENTS: Final[tuple[str, ...]] = (
    "Battery:ChargePower",
    "Battery:ChargeCurrent",
    "Battery:ExternControl",
)

# Battery charge/discharge control is intentionally MODBUS-only in this
# integration. These REST IDs are kept out of write paths to avoid false
# "accepted" writes that do not reliably change inverter behavior.
_REST_MODBUS_ONLY_IDS: Final[frozenset[str]] = frozenset(
    {
        "Battery:ExternControl:AcPowerAbs",
        "Battery:ChargePowerAcAbsolute",
        "Battery:ChargeCurrentDcRel",
        "Battery:ChargePowerAcRel",
        "Battery:ChargeCurrentDcAbs",
        "Battery:ChargePowerDcAbs",
        "Battery:ChargePowerDcRel",
    }
)


def is_allowed_write_target(module_id: str, data_id: str) -> bool:
    """Return True if this module/data_id is allowed for writes."""
    if module_id != ModuleId.DEVICES_LOCAL:
        return False
    if not is_rest_write_supported_target(data_id):
        return False
    if data_id in _ALLOWED_WRITE_IDS:
        return True
    return any(data_id.startswith(prefix) for prefix in _ALLOWED_WRITE_PREFIXES)


def is_rest_write_supported_target(data_id: str) -> bool:
    """Return False for settings intentionally kept Modbus-only."""
    return data_id not in _REST_MODBUS_ONLY_IDS


def requires_advanced_write_arm(data_id: str) -> bool:
    """Return True if writing this data_id requires temporary arming."""
    if data_id in _ARM_REQUIRED_IDS:
        return True
    if any(fragment in data_id for fragment in _ARM_REQUIRED_FRAGMENTS):
        return True
    return any(data_id.startswith(prefix) for prefix in _ARM_REQUIRED_PREFIXES)


def validate_cross_field_write_rules(
    data_id: str,
    new_value: str,
    current_module_values: dict[str, str] | None = None,
) -> str | None:
    """Validate cross-field write rules and return error message if invalid."""
    try:
        if data_id.endswith("OnPowerThreshold"):
            off_key = data_id.replace("OnPowerThreshold", "OffPowerThreshold")
            if current_module_values and off_key in current_module_values:
                off_value = float(current_module_values[off_key])
                on_value = float(new_value)
                if on_value <= off_value:
                    return (
                        f"{data_id} must be greater than {off_key} "
                        f"(got on={on_value}, off={off_value})"
                    )
        elif data_id.endswith("OffPowerThreshold"):
            on_key = data_id.replace("OffPowerThreshold", "OnPowerThreshold")
            if current_module_values and on_key in current_module_values:
                on_value = float(current_module_values[on_key])
                off_value = float(new_value)
                if off_value >= on_value:
                    return (
                        f"{data_id} must be lower than {on_key} "
                        f"(got off={off_value}, on={on_value})"
                    )
    except (TypeError, ValueError):
        # If value cannot be parsed as float, let lower-level validation handle it.
        return None
    return None


def ensure_installer_access(
    entry: Any,
    requires_installer: bool,
    module_id: str,
    data_id: str,
    operation: str,
    log_level: str = "warning",
    hass: HomeAssistant | None = None,
) -> bool:
    """Return True if installer access is available or not required."""
    if not requires_installer:
        return True

    installer_access = bool(
        entry.data.get(
            CONF_INSTALLER_ACCESS,
            bool(entry.data.get(CONF_SERVICE_CODE)),
        )
    )
    if not installer_access:
        log_fn = getattr(_LOGGER, log_level, _LOGGER.warning)
        log_fn(
            "Installer service code required for %s on %s/%s",
            operation,
            module_id,
            data_id,
        )
        if hass is not None:
            create_installer_required_issue(hass)
        return False

    if hass is not None:
        clear_issue(hass, "installer_required")
    return True
