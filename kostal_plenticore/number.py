"""Platform for Kostal Plenticore numbers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import logging
from typing import Any, Final

from aiohttp.client_exceptions import ClientError
from pykoplenti import ApiException, SettingsData

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfPower,
    UnitOfElectricCurrent,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_registry import RegistryEntryDisabler
from homeassistant.helpers.event import async_call_later

from .const import CONF_SERVICE_CODE, DOMAIN, AddConfigEntryEntitiesCallback
from .const_ids import ModuleId, SettingId
from .coordinator import PlenticoreConfigEntry, SettingDataUpdateCoordinator
from .helper import (
    PlenticoreDataFormatter,
    ensure_installer_access,
    is_battery_control,
    parse_modbus_exception,
    requires_installer_service_code,
)

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0  # Coordinator serialises all API calls

# Legacy setting IDs used by some firmware versions (typos or renamed keys)
LEGACY_SETTING_ALIASES: Final[dict[str, str]] = {
    SettingId.BATTERY_MIN_SOC_REL: SettingId.BATTERY_MIN_SOC,
    SettingId.BATTERY_MIN_HOME_CONSUMPTION: SettingId.BATTERY_MIN_HOME_CONSUMPTION_LEGACY,
    SettingId.BATTERY_EXTERN_CONTROL_AC_POWER_ABS: "Battery:ChargePowerAcAbsolute",
    SettingId.BATTERY_LIMIT_CHARGE_POWER: SettingId.BATTERY_MAX_CHARGE_POWER_G3,
    SettingId.BATTERY_LIMIT_DISCHARGE_POWER: SettingId.BATTERY_MAX_DISCHARGE_POWER_G3,
    SettingId.BATTERY_LIMIT_FALLBACK_CHARGE_POWER: "Battery:MaxChargePowerFallback",
    SettingId.BATTERY_LIMIT_FALLBACK_DISCHARGE_POWER: "Battery:MaxDischargePowerFallback",
    SettingId.BATTERY_LIMIT_FALLBACK_TIME: SettingId.BATTERY_TIME_UNTIL_FALLBACK,
}
LEGACY_SETTING_REVERSE: Final[dict[str, str]] = {
    v: k for k, v in LEGACY_SETTING_ALIASES.items()
}

# Number entity constants
DEFAULT_ENTITY_REGISTRY_ENABLED: Final[bool] = False
CONFIG_ENTITY_CATEGORY: Final[EntityCategory] = EntityCategory.CONFIG


def _normalize_translation_key(key: str) -> str:
    """Normalize translation keys to a stable snake_case identifier."""
    normalized = key.replace(":", "_").replace(".", "_").replace(" ", "_")
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    return normalized.lower()


# Performance and validation constants
DEFAULT_MAX_POWER_WATTS: Final[int] = 38000
DEFAULT_MIN_POWER_WATTS: Final[int] = 0
DEFAULT_POWER_STEP_WATTS: Final[int] = 1
DEFAULT_MAX_CURRENT_AMPS: Final[int] = 100
DEFAULT_MIN_CURRENT_AMPS: Final[int] = 0
DEFAULT_CURRENT_STEP_AMPS: Final[float] = 0.1
DEFAULT_PERCENTAGE_MAX: Final[int] = 100
DEFAULT_PERCENTAGE_MIN: Final[int] = 0
DEFAULT_PERCENTAGE_STEP: Final[int] = 1
DEFAULT_TIME_MAX_SECONDS: Final[int] = 86400
DEFAULT_TIME_MIN_SECONDS: Final[int] = 0
DEFAULT_TIME_STEP_SECONDS: Final[int] = 1
SETTINGS_TIMEOUT_SECONDS: Final[float] = 30.0

# Battery-specific constants
BATTERY_MAX_POWER_WATTS: Final[int] = 1000000
BATTERY_MIN_POWER_WATTS: Final[int] = 10
BATTERY_POWER_STEP_WATTS: Final[int] = 100
BATTERY_TIME_MAX_SECONDS: Final[int] = 86400
BATTERY_TIME_MIN_SECONDS: Final[int] = 0
BATTERY_TIME_STEP_SECONDS: Final[int] = 1
G3_CYCLIC_LIMIT_IDS: Final[set[str]] = {
    SettingId.BATTERY_LIMIT_CHARGE_POWER,
    SettingId.BATTERY_LIMIT_DISCHARGE_POWER,
    SettingId.BATTERY_MAX_CHARGE_POWER_G3,
    SettingId.BATTERY_MAX_DISCHARGE_POWER_G3,
}
G3_KEEPALIVE_MIN_SECONDS: Final[int] = 10
G3_KEEPALIVE_MAX_SECONDS: Final[int] = 300
G3_FALLBACK_MIN_SECONDS: Final[int] = 30
G3_FALLBACK_MAX_SECONDS: Final[int] = 10800


def _handle_number_error(err: Exception, operation: str) -> dict[str, Any]:
    """
    Centralized error handling for number operations.

    Args:
        err: Exception that occurred
        operation: Description of the operation being performed

    Returns:
        Empty dict as fallback
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

    return {}


async def _get_settings_data_safe(plenticore: Any, operation: str) -> dict[str, Any]:
    """
    Get settings data with timeout protection.

    Args:
        plenticore: Plenticore client instance
        operation: Description of the operation

    Returns:
        Settings data or empty dict if error occurs
    """
    try:
        return await asyncio.wait_for(
            plenticore.client.get_settings(), timeout=SETTINGS_TIMEOUT_SECONDS
        )
    except Exception as err:
        return _handle_number_error(err, operation)


def create_battery_number_description(
    key: str,
    name: str,
    unit: str,
    max_value: int | None = None,
    min_value: int | None = None,
    step: int | float | None = None,
    icon: str | None = None,
    data_id: str | None = None,
) -> PlenticoreNumberEntityDescription:
    """
    Factory function for creating battery number descriptions with security defaults.

    Args:
        key: Entity key
        name: Entity name
        unit: Unit of measurement
        max_value: Maximum value (uses battery defaults if None)
        min_value: Minimum value (uses battery defaults if None)
        step: Step value (uses battery defaults if None)
        icon: Icon (uses battery icon if None)
        data_id: Data ID (uses key if None)

    Returns:
        Configured PlenticoreNumberEntityDescription
    """
    return PlenticoreNumberEntityDescription(
        key=key,
        name=name,
        native_unit_of_measurement=unit,
        native_max_value=max_value or BATTERY_MAX_POWER_WATTS,
        native_min_value=min_value or BATTERY_MIN_POWER_WATTS,
        native_step=step or BATTERY_POWER_STEP_WATTS,
        entity_category=CONFIG_ENTITY_CATEGORY,
        entity_registry_enabled_default=DEFAULT_ENTITY_REGISTRY_ENABLED,
        icon=icon or "mdi:battery",
        module_id=ModuleId.DEVICES_LOCAL,
        data_id=data_id or key,
        fmt_from="format_round",
        fmt_to="format_round_back",
    )


def create_power_number_description(
    key: str,
    name: str,
    unit: str,
    max_value: int | None = None,
    min_value: int | None = None,
    step: int | float | None = None,
    icon: str | None = None,
    data_id: str | None = None,
    entity_registry_enabled_default: bool | None = None,
) -> PlenticoreNumberEntityDescription:
    """
    Factory function for creating power number descriptions with security defaults.

    Args:
        key: Entity key
        name: Entity name
        unit: Unit of measurement
        max_value: Maximum value (uses power defaults if None)
        min_value: Minimum value (uses power defaults if None)
        step: Step value (uses power defaults if None)
        icon: Icon (uses power icon if None)
        data_id: Data ID (uses key if None)

    Returns:
        Configured PlenticoreNumberEntityDescription
    """
    return PlenticoreNumberEntityDescription(
        key=key,
        name=name,
        native_unit_of_measurement=unit,
        native_max_value=max_value or DEFAULT_MAX_POWER_WATTS,
        native_min_value=min_value or DEFAULT_MIN_POWER_WATTS,
        native_step=step or DEFAULT_POWER_STEP_WATTS,
        entity_category=CONFIG_ENTITY_CATEGORY,
        entity_registry_enabled_default=(
            DEFAULT_ENTITY_REGISTRY_ENABLED
            if entity_registry_enabled_default is None
            else entity_registry_enabled_default
        ),
        icon=icon or "mdi:flash",
        module_id=ModuleId.DEVICES_LOCAL,
        data_id=data_id or key,
        fmt_from="format_round",
        fmt_to="format_round_back",
    )


def create_percentage_number_description(
    key: str,
    name: str,
    icon: str | None = None,
    data_id: str | None = None,
    entity_registry_enabled_default: bool | None = None,
) -> PlenticoreNumberEntityDescription:
    """
    Factory function for creating percentage number descriptions with security defaults.

    Args:
        key: Entity key
        name: Entity name
        icon: Icon (uses percentage icon if None)
        data_id: Data ID (uses key if None)

    Returns:
        Configured PlenticoreNumberEntityDescription
    """
    return PlenticoreNumberEntityDescription(
        key=key,
        name=name,
        native_unit_of_measurement=PERCENTAGE,
        native_max_value=DEFAULT_PERCENTAGE_MAX,
        native_min_value=DEFAULT_PERCENTAGE_MIN,
        native_step=DEFAULT_PERCENTAGE_STEP,
        entity_category=CONFIG_ENTITY_CATEGORY,
        entity_registry_enabled_default=(
            DEFAULT_ENTITY_REGISTRY_ENABLED
            if entity_registry_enabled_default is None
            else entity_registry_enabled_default
        ),
        icon=icon or "mdi:percent",
        module_id=ModuleId.DEVICES_LOCAL,
        data_id=data_id or key,
        fmt_from="format_round",
        fmt_to="format_round_back",
    )


@dataclass(frozen=True, kw_only=True)
class PlenticoreNumberEntityDescription(NumberEntityDescription):
    """A class that describes plenticore number entities."""

    module_id: str
    data_id: str
    fmt_from: str
    fmt_to: str


NUMBER_SETTINGS_DATA = [
    create_percentage_number_description(
        key="battery_min_soc",
        name="Battery min SoC",
        icon="mdi:battery-negative",
        data_id=SettingId.BATTERY_MIN_SOC_REL,
        entity_registry_enabled_default=True,
    ),
    create_power_number_description(
        key="battery_min_home_consumption",
        name="Battery min Home Consumption",
        unit=UnitOfPower.WATT,
        max_value=DEFAULT_MAX_POWER_WATTS,
        icon="mdi:home-lightning-bolt",
        data_id=SettingId.BATTERY_MIN_HOME_CONSUMPTION,
        entity_registry_enabled_default=True,
    ),
    # Battery Charge/Discharge Setpoints (Section 3.4 External Battery Management)
    # Note: Negative values charge the battery, positive values discharge the battery
    create_battery_number_description(
        key="battery_charge_power_ac_absolute",
        name="Battery Charge Power (AC) Absolute",
        unit=UnitOfPower.WATT,
        max_value=BATTERY_MAX_POWER_WATTS,
        min_value=-BATTERY_MAX_POWER_WATTS,
        icon="mdi:battery-charging-100",
        data_id=SettingId.BATTERY_EXTERN_CONTROL_AC_POWER_ABS,
    ),
    create_percentage_number_description(
        key="battery_charge_current_dc_relative",
        name="Battery Charge Current (DC) Relative",
        icon="mdi:battery-charging",
        data_id="Battery:ChargeCurrentDcRel",
    ),
    create_percentage_number_description(
        key="battery_charge_power_ac_relative",
        name="Battery Charge Power (AC) Relative",
        icon="mdi:battery-charging",
        data_id="Battery:ChargePowerAcRel",
    ),
    create_battery_number_description(
        key="battery_charge_current_dc_absolute",
        name="Battery Charge Current (DC) Absolute",
        unit=UnitOfElectricCurrent.AMPERE,
        max_value=DEFAULT_MAX_CURRENT_AMPS,
        min_value=DEFAULT_MIN_CURRENT_AMPS,
        step=DEFAULT_CURRENT_STEP_AMPS,
        icon="mdi:battery-charging",
        data_id="Battery:ChargeCurrentDcAbs",
    ),
    create_battery_number_description(
        key="battery_charge_power_dc_absolute",
        name="Battery Charge Power (DC) Absolute",
        unit=UnitOfPower.WATT,
        max_value=BATTERY_MAX_POWER_WATTS,
        min_value=-BATTERY_MAX_POWER_WATTS,
        icon="mdi:battery-charging",
        data_id="Battery:ChargePowerDcAbs",
    ),
    create_percentage_number_description(
        key="battery_charge_power_dc_relative",
        name="Battery Charge Power (DC) Relative",
        icon="mdi:battery-charging",
        data_id="Battery:ChargePowerDcRel",
    ),
    # Battery Limitation (G3 Only - Section 3.5)
    # Available only for PLENTICORE G3 inverters from software version 03.05.xxxxx
    # Note: Registers 0x500 and 0x502 must be written cyclically. If not written,
    # after the time in 0x508, the fallback limits (0x504, 0x506) become active.
    create_battery_number_description(
        key="battery_max_charge_power_g3",
        name="Battery Max Charge Power (G3)",
        unit=UnitOfPower.WATT,
        max_value=BATTERY_MAX_POWER_WATTS,
        icon="mdi:battery-charging-limit",
        data_id=SettingId.BATTERY_LIMIT_CHARGE_POWER,
    ),
    create_battery_number_description(
        key="battery_max_discharge_power_g3",
        name="Battery Max Discharge Power (G3)",
        unit=UnitOfPower.WATT,
        max_value=BATTERY_MAX_POWER_WATTS,
        icon="mdi:battery-discharging-limit",
        data_id=SettingId.BATTERY_LIMIT_DISCHARGE_POWER,
    ),
    create_battery_number_description(
        key="battery_max_charge_power_fallback",
        name="Battery Max Charge Power Fallback (G3)",
        unit=UnitOfPower.WATT,
        max_value=BATTERY_MAX_POWER_WATTS,
        icon="mdi:battery-charging-limit",
        data_id=SettingId.BATTERY_LIMIT_FALLBACK_CHARGE_POWER,
    ),
    create_battery_number_description(
        key="battery_max_discharge_power_fallback",
        name="Battery Max Discharge Power Fallback (G3)",
        unit=UnitOfPower.WATT,
        max_value=BATTERY_MAX_POWER_WATTS,
        icon="mdi:battery-discharging-limit",
        data_id=SettingId.BATTERY_LIMIT_FALLBACK_DISCHARGE_POWER,
    ),
    create_power_number_description(
        key="battery_time_until_fallback",
        name="Battery Time Until Fallback (G3)",
        unit="s",
        max_value=BATTERY_TIME_MAX_SECONDS,
        min_value=BATTERY_TIME_MIN_SECONDS,
        step=BATTERY_TIME_STEP_SECONDS,
        icon="mdi:timer",
        data_id=SettingId.BATTERY_LIMIT_FALLBACK_TIME,
    ),
    # Additional External Control Settings
    create_battery_number_description(
        key="battery_extern_control_max_charge_power_abs",
        name="Battery External Control Max Charge Power Absolute",
        unit=UnitOfPower.WATT,
        max_value=BATTERY_MAX_POWER_WATTS,
        icon="mdi:battery-charging-limit",
        data_id="Battery:ExternControl:MaxChargePowerAbs",
    ),
    create_battery_number_description(
        key="battery_extern_control_max_discharge_power_abs",
        name="Battery External Control Max Discharge Power Absolute",
        unit=UnitOfPower.WATT,
        max_value=BATTERY_MAX_POWER_WATTS,
        icon="mdi:battery-discharging-limit",
        data_id="Battery:ExternControl:MaxDischargePowerAbs",
    ),
    create_percentage_number_description(
        key="battery_extern_control_max_soc_rel",
        name="Battery External Control Max SoC Relative",
        icon="mdi:battery-positive",
        data_id="Battery:ExternControl:MaxSocRel",
    ),
    create_percentage_number_description(
        key="battery_extern_control_min_soc_rel",
        name="Battery External Control Min SoC Relative",
        icon="mdi:battery-negative",
        data_id="Battery:ExternControl:MinSocRel",
    ),
    # ESB (Emergency Supply Battery) Settings
    create_percentage_number_description(
        key="battery_esb_min_soc",
        name="Battery ESB Minimum SoC",
        icon="mdi:battery-alert",
        data_id="Battery:Esb:MinSocRel",
    ),
    create_percentage_number_description(
        key="battery_esb_start_soc",
        name="Battery ESB Start SoC",
        icon="mdi:battery-alert-variant",
        data_id="Battery:Esb:StartSocRel",
    ),
    # Winter Mode Settings
    create_percentage_number_description(
        key="battery_winter_min_soc",
        name="Battery Winter Minimum SoC",
        icon="mdi:snowflake",
        data_id="Battery:Winter:MinSocRel",
    ),
    create_power_number_description(
        key="battery_winter_start_month",
        name="Battery Winter Start Month",
        unit="month",
        max_value=12,
        min_value=1,
        step=1,
        icon="mdi:calendar-month",
        data_id="Battery:Winter:StartMonth",
    ),
    create_power_number_description(
        key="battery_winter_end_month",
        name="Battery Winter End Month",
        unit="month",
        max_value=12,
        min_value=1,
        step=1,
        icon="mdi:calendar-month",
        data_id="Battery:Winter:EndMonth",
    ),
    # Grid Feed-in Settings
    create_power_number_description(
        key="battery_min_grid_feed_in",
        name="Battery Minimum Grid Feed-in",
        unit=UnitOfPower.WATT,
        max_value=DEFAULT_MAX_POWER_WATTS,
        icon="mdi:transmission-tower",
        data_id="Battery:MinGridFeedIn",
    ),
    # Battery Communication Monitor Time
    create_power_number_description(
        key="battery_com_monitor_time",
        name="Battery Communication Monitor Time",
        unit="s",
        max_value=BATTERY_TIME_MAX_SECONDS,
        min_value=BATTERY_TIME_MIN_SECONDS,
        step=BATTERY_TIME_STEP_SECONDS,
        icon="mdi:timer",
        data_id="Battery:ComMonitorTime",
    ),
    # Energy Management Settings
    create_power_number_description(
        key="energy_mgmt_bat_ctrl_power_offset",
        name="Energy Management Battery Control Power Offset",
        unit=UnitOfPower.WATT,
        max_value=DEFAULT_MAX_POWER_WATTS,
        icon="mdi:battery-charging",
        data_id="EnergyManagement:BatCtrlPowerOffset",
    ),
    create_power_number_description(
        key="energy_mgmt_limit_grid_supply",
        name="Energy Management Limit Grid Supply",
        unit=UnitOfPower.WATT,
        max_value=DEFAULT_MAX_POWER_WATTS,
        icon="mdi:transmission-tower-export",
        data_id="EnergyManagement:LimitGridSupply",
    ),
    create_power_number_description(
        key="energy_mgmt_smart_control_fallback_max_time",
        name="Energy Management Smart Control Fallback Max Time",
        unit="s",
        max_value=BATTERY_TIME_MAX_SECONDS,
        min_value=BATTERY_TIME_MIN_SECONDS,
        step=BATTERY_TIME_STEP_SECONDS,
        icon="mdi:timer",
        data_id="EnergyManagement:SmartControlFallbackMaxTime",
    ),
    PlenticoreNumberEntityDescription(
        key="energy_mgmt_timed_bat_charge_grid_power",
        device_class=NumberDeviceClass.POWER,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:battery-charging",
        name="Timed Battery Charge Grid Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        native_max_value=50000,
        native_min_value=0,
        native_step=100,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="EnergyMgmt:TimedBatCharge:GridPower",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="energy_mgmt_timed_bat_charge_soc",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:battery",
        name="Timed Battery Charge SoC",
        native_unit_of_measurement=PERCENTAGE,
        native_max_value=100,
        native_min_value=0,
        native_step=1,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="EnergyMgmt:TimedBatCharge:Soc",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="energy_mgmt_timed_bat_charge_wd_grid_power",
        device_class=NumberDeviceClass.POWER,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:battery-charging",
        name="Timed Battery Charge Weekend Grid Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        native_max_value=50000,
        native_min_value=0,
        native_step=100,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="EnergyMgmt:TimedBatCharge:WD_GridPower",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="energy_mgmt_timed_bat_charge_wd_soc",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:battery",
        name="Timed Battery Charge Weekend SoC",
        native_unit_of_measurement=PERCENTAGE,
        native_max_value=100,
        native_min_value=0,
        native_step=1,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="EnergyMgmt:TimedBatCharge:WD_Soc",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    # Active Power Control Settings
    PlenticoreNumberEntityDescription(
        key="active_power_ext_ctrl_mode_gradient",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:chart-line",
        name="Active Power Gradient Mode",
        native_unit_of_measurement="%Pnenn/s",
        native_max_value=6000,
        native_min_value=0,
        native_step=1,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="ActivePower:ExtCtrl:ModeGradient",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="active_power_ext_ctrl_mode_gradient_low_priority",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:chart-line",
        name="Active Power Gradient Mode Low Priority",
        native_unit_of_measurement="%Pnenn/s",
        native_max_value=6000,
        native_min_value=0,
        native_step=1,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="ActivePower:ExtCtrl:ModeGradientLowPriority",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="active_power_ext_ctrl_mode_pt1_tau",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:timer",
        name="Active Power PT1 Tau",
        native_unit_of_measurement="s",
        native_max_value=300,
        native_min_value=0,
        native_step=1,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="ActivePower:ExtCtrl:ModePT1Tau",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="active_power_ext_ctrl_mode_pt1_low_priority_tau",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:timer",
        name="Active Power PT1 Low Priority Tau",
        native_unit_of_measurement="s",
        native_max_value=300,
        native_min_value=0,
        native_step=1,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="ActivePower:ExtCtrl:ModePT1LowPriorityTau",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="active_power_ext_ctrl_ramp_time",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:timer",
        name="Active Power Ramp Time",
        native_unit_of_measurement="s",
        native_max_value=3600,
        native_min_value=0,
        native_step=1,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="ActivePower:ExtCtrl:RampTime",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="active_power_ext_ctrl_p_limit_grid_supply",
        device_class=NumberDeviceClass.POWER,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:transmission-tower",
        name="Active Power Limit Grid Supply",
        native_unit_of_measurement=UnitOfPower.WATT,
        native_max_value=50000,
        native_min_value=0,
        native_step=100,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="ActivePower:ExtCtrlP:LimitGridSupply",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="active_power_ext_ctrl_p_p",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:percent",
        name="Active Power P",
        native_unit_of_measurement=PERCENTAGE,
        native_max_value=100,
        native_min_value=0,
        native_step=1,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="ActivePower:ExtCtrlP:P",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="active_power_ext_ctrl_p_p_fine",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:percent",
        name="Active Power P Fine",
        native_unit_of_measurement=PERCENTAGE,
        native_max_value=100,
        native_min_value=0,
        native_step=0.1,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="ActivePower:ExtCtrlP:P_fine",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    # Inverter Control Settings
    PlenticoreNumberEntityDescription(
        key="inverter_active_power_limitation",
        device_class=NumberDeviceClass.POWER,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:lightning-bolt",
        name="Inverter Active Power Limitation",
        native_unit_of_measurement=UnitOfPower.WATT,
        native_max_value=50000,
        native_min_value=0,
        native_step=100,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="Inverter:ActivePowerLimitation",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="inverter_active_power_consum_limitation",
        device_class=NumberDeviceClass.POWER,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:lightning-bolt",
        name="Inverter Active Power Consumption Limitation",
        native_unit_of_measurement=UnitOfPower.WATT,
        native_max_value=50000,
        native_min_value=0,
        native_step=100,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="Inverter:ActivePowerConsumLimitation",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    # Reactive Power Control Settings
    PlenticoreNumberEntityDescription(
        key="reactive_power_fix_cos_phi_delta",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:sine-wave",
        name="Reactive Power Fix Cos Phi Delta",
        native_max_value=0.8,
        native_min_value=-0.8,
        native_step=0.01,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="ReactivePower:FixCosPhi:DeltaCosPhi",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="reactive_power_fix_q",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:sine-wave",
        name="Reactive Power Fix Q",
        native_unit_of_measurement=PERCENTAGE,
        native_max_value=100,
        native_min_value=-100,
        native_step=1,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="ReactivePower:FixQ:Q",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="reactive_power_ext_ctrl_fix_cos_phi_delta",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:sine-wave",
        name="Reactive Power Ext Ctrl Fix Cos Phi Delta",
        native_max_value=26214,
        native_min_value=-26214,
        native_step=1,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="ReactivePower:ExtCtrlFixCosPhi:DeltaCosPhi",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="reactive_power_ext_ctrl_fix_q",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:sine-wave",
        name="Reactive Power Ext Ctrl Fix Q",
        native_unit_of_measurement=PERCENTAGE,
        native_max_value=100,
        native_min_value=-100,
        native_step=1,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="ReactivePower:ExtCtrlFixQ:Q",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="reactive_power_ext_ctrl_settling_time",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:timer",
        name="Reactive Power Ext Ctrl Settling Time",
        native_unit_of_measurement="s",
        native_max_value=300,
        native_min_value=0,
        native_step=1,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="ReactivePower:ExtCtrl:SettlingTime",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="reactive_power_power_limit_input_prio_high_mode",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:percent",
        name="Reactive Power Power Limit Input Priority High Mode",
        native_unit_of_measurement=PERCENTAGE,
        native_max_value=100,
        native_min_value=0,
        native_step=1,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="ReactivePower:PowerLimitInputPrioHighMode",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    # LVRT/HVRT Settings
    PlenticoreNumberEntityDescription(
        key="lvrt_hvrt_k_factor_lvrt",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:chart-line",
        name="LVRT K Factor",
        native_max_value=10,
        native_min_value=0,
        native_step=0.1,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="LvrtHvrt:KFactorLvrt",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="lvrt_hvrt_k_factor_hvrt",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:chart-line",
        name="HVRT K Factor",
        native_max_value=10,
        native_min_value=0,
        native_step=0.1,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="LvrtHvrt:KFactorHvrt",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="lvrt_hvrt_threshold_lvrt",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:percent",
        name="LVRT Threshold",
        native_unit_of_measurement=PERCENTAGE,
        native_max_value=95,
        native_min_value=0,
        native_step=1,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="LvrtHvrt:ThresholdLvrt",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="lvrt_hvrt_threshold_hvrt",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:percent",
        name="HVRT Threshold",
        native_unit_of_measurement=PERCENTAGE,
        native_max_value=150,
        native_min_value=105,
        native_step=1,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="LvrtHvrt:ThresholdHvrt",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="lvrt_hvrt_lower_voltage_lvrt",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:percent",
        name="LVRT Lower Voltage",
        native_unit_of_measurement=PERCENTAGE,
        native_max_value=95,
        native_min_value=0,
        native_step=1,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="LvrtHvrt:LowerVoltageLvrt",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="lvrt_hvrt_upper_voltage_hvrt",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:percent",
        name="HVRT Upper Voltage",
        native_unit_of_measurement=PERCENTAGE,
        native_max_value=150,
        native_min_value=105,
        native_step=1,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="LvrtHvrt:UpperVoltageHvrt",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="lvrt_hvrt_gradient_and_time",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:timer",
        name="LVRT/HVRT Gradient and Time",
        native_unit_of_measurement="s",
        native_max_value=15,
        native_min_value=0,
        native_step=0.1,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="LvrtHvrt:GradientAndTime",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    # Power of Frequency Settings
    PlenticoreNumberEntityDescription(
        key="pof_f_nominal_frequency",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:sine-wave",
        name="Power of Frequency Nominal Frequency",
        native_unit_of_measurement="Hz",
        native_max_value=100,
        native_min_value=0,
        native_step=0.1,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="POfF:NominalFrequency",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="pof_f_reduction_start_frequency",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:sine-wave",
        name="Power of Frequency Reduction Start Frequency",
        native_unit_of_measurement="Hz",
        native_max_value=100,
        native_min_value=0,
        native_step=0.1,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="POfF:ReductionStartFrequency",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="pof_f_reduction_end_frequency",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:sine-wave",
        name="Power of Frequency Reduction End Frequency",
        native_unit_of_measurement="Hz",
        native_max_value=100,
        native_min_value=0,
        native_step=0.1,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="POfF:ReductionEndFrequency",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="pof_f_delay_reaction_time",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:timer",
        name="Power of Frequency Delay Reaction Time",
        native_unit_of_measurement="s",
        native_max_value=60,
        native_min_value=0,
        native_step=0.1,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="POfF:DelayReactionTime",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    # Power of Voltage Settings
    PlenticoreNumberEntityDescription(
        key="pof_u_reduction_start_voltage",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:lightning-bolt",
        name="Power of Voltage Reduction Start Voltage",
        native_max_value=2.0,
        native_min_value=0,
        native_step=0.01,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="POfU:ReductionStartVoltage",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="pof_u_reduction_end_voltage",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:lightning-bolt",
        name="Power of Voltage Reduction End Voltage",
        native_max_value=2.0,
        native_min_value=0,
        native_step=0.01,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="POfU:ReductionEndVoltage",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="pof_u_settling_time",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:timer",
        name="Power of Voltage Settling Time",
        native_unit_of_measurement="s",
        native_max_value=300,
        native_min_value=0,
        native_step=1,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="POfU:SettlingTime",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    # Digital Output Settings (Power Control)
    PlenticoreNumberEntityDescription(
        key="digital_out1_power_ctl_on_power_threshold",
        device_class=NumberDeviceClass.POWER,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:power",
        name="Digital Out 1 Power Control On Threshold",
        native_unit_of_measurement=UnitOfPower.WATT,
        native_max_value=1000000,
        native_min_value=10,
        native_step=100,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="DigitalOut1:PowerCtl:OnPowerThreshold",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="digital_out1_power_ctl_off_power_threshold",
        device_class=NumberDeviceClass.POWER,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:power",
        name="Digital Out 1 Power Control Off Threshold",
        native_unit_of_measurement=UnitOfPower.WATT,
        native_max_value=1000000,
        native_min_value=0,
        native_step=100,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="DigitalOut1:PowerCtl:OffPowerThreshold",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="digital_out1_power_ctl_delay_time",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:timer",
        name="Digital Out 1 Power Control Delay Time",
        native_unit_of_measurement="min",
        native_max_value=720,
        native_min_value=0,
        native_step=1,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="DigitalOut1:PowerCtl:DelayTime",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="digital_out1_power_ctl_stable_time",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:timer",
        name="Digital Out 1 Power Control Stable Time",
        native_unit_of_measurement="min",
        native_max_value=720,
        native_min_value=1,
        native_step=1,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="DigitalOut1:PowerCtl:StableTime",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="digital_out1_power_ctl_run_time",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:timer",
        name="Digital Out 1 Power Control Run Time",
        native_unit_of_measurement="min",
        native_max_value=1440,
        native_min_value=1,
        native_step=1,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="DigitalOut1:PowerCtl:RunTime",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    # Digital Output SoC Settings
    PlenticoreNumberEntityDescription(
        key="digital_out_bat_discharge_soc",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:battery",
        name="Digital Out Battery Discharge SoC",
        native_unit_of_measurement=PERCENTAGE,
        native_max_value=100,
        native_min_value=0,
        native_step=1,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="DigitalOut:BatDischarge_SoC",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="digital_out_output_enable_soc",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:battery",
        name="Digital Out Output Enable SoC",
        native_unit_of_measurement=PERCENTAGE,
        native_max_value=100,
        native_min_value=0,
        native_step=1,
        module_id=ModuleId.DEVICES_LOCAL,
        data_id="DigitalOut:OutputEnable_SoC",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PlenticoreConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add Kostal Plenticore Number entities."""
    plenticore = entry.runtime_data

    entities = []

    # Fetch fresh settings data with timeout protection and retry
    available_settings_data = await _get_settings_data_safe(
        plenticore, "number settings"
    )

    if not available_settings_data:
        _LOGGER.warning(
            "Initial number settings fetch failed, retrying in 2 seconds..."
        )
        await asyncio.sleep(2)
        available_settings_data = await _get_settings_data_safe(
            plenticore, "number settings (retry)"
        )
    available_settings_data = available_settings_data or {}

    from .const import CONF_MODBUS_ENABLED, DOMAIN as _DOMAIN
    _modbus_active = entry.options.get(CONF_MODBUS_ENABLED, False)
    _settings_interval = 90 if _modbus_active else 30

    settings_data_update_coordinator = SettingDataUpdateCoordinator(
        hass, entry, _LOGGER, "Settings Data", timedelta(seconds=_settings_interval), plenticore
    )

    # Track battery-related settings for better logging
    battery_settings_found = []
    battery_settings_skipped = []

    forced_unique_ids: set[str] = set()
    forced_unique_ids_by_data_id: dict[str, set[str]] = {}
    forced_fetch_pairs: set[tuple[str, str]] = set()

    # Special handling: Force create specific battery settings that might be hidden
    # due to permissions but are accessible if requested directly.
    FORCE_CREATE_KEYS = {
        "Battery:MinHomeConsumption",
        "Battery:MinHomeComsumption",
        "Battery:MinSocRel",
        "Battery:MinSoc",
    }

    for description in NUMBER_SETTINGS_DATA:
        setting_data = None
        description_to_use = description

        # If the inverter exposes a legacy ID, use it to avoid unavailable entities
        if (
            description.data_id in LEGACY_SETTING_ALIASES
            and description.module_id in available_settings_data
        ):
            legacy_id = LEGACY_SETTING_ALIASES[description.data_id]
            if legacy_id in (
                setting.id for setting in available_settings_data[description.module_id]
            ):
                description_to_use = replace(description, data_id=legacy_id)
                _LOGGER.debug(
                    "Using legacy data_id %s for %s",
                    legacy_id,
                    description.name,
                )
        # Check if the module even exists before trying to access its settings
        module_available = (
            description_to_use.module_id in plenticore.available_modules
            or (
                not plenticore.available_modules
                and description_to_use.module_id in available_settings_data
            )
        )
        if not module_available:
            _LOGGER.debug(
                "Skipping number %s because module %s is not available",
                description.name,
                description_to_use.module_id,
            )
            continue

        should_skip = False
        if (
            description_to_use.module_id not in available_settings_data
            or description_to_use.data_id
            not in (
                setting.id
                for setting in available_settings_data[description_to_use.module_id]
            )
        ):
            if description_to_use.data_id in FORCE_CREATE_KEYS and module_available:
                _LOGGER.debug(
                    "Force creating hidden setting %s/%s",
                    description_to_use.module_id,
                    description_to_use.data_id,
                )
                # Pass None as setting_data, trusting definitions in description defaults.
                # The entity will fetch the real values via coordinator on next update.
                setting_data = None
            else:
                should_skip = True

        if should_skip:
            if "Battery" in description_to_use.data_id:
                battery_settings_skipped.append(
                    f"{description_to_use.module_id}/{description_to_use.data_id}"
                )

            # Only debug log if we actually have some data
            if (
                available_settings_data
                and description_to_use.module_id in available_settings_data
            ):
                _LOGGER.debug(
                    "Skipping non existing setting data %s/%s",
                    description_to_use.module_id,
                    description_to_use.data_id,
                )
            elif not available_settings_data:
                _LOGGER.debug(
                    "Skipping number %s because settings data fetch failed/empty",
                    description.name,
                )

            continue

        if "Battery" in description_to_use.data_id:
            battery_settings_found.append(
                f"{description_to_use.module_id}/{description_to_use.data_id}"
            )

        # Find the setting data
        if (
            setting_data is None
            and description_to_use.module_id in available_settings_data
        ):
            for sd in available_settings_data[description_to_use.module_id]:
                if description_to_use.data_id == sd.id:
                    setting_data = sd
                    break

        # If we still don't have setting_data, we create a dummy one or pass None
        # The PlenticoreDataNumber expects setting_data.
        # But wait, looking at PlenticoreDataNumber, it might need it for initial state.
        # If we continue here, we skip creation.

        if setting_data is None and description_to_use.data_id not in FORCE_CREATE_KEYS:
            _LOGGER.warning(
                "Setting data %s/%s not found in available settings despite passing initial check",
                description_to_use.module_id,
                description_to_use.data_id,
            )
            continue

        entities.append(
            PlenticoreDataNumber(
                settings_data_update_coordinator,
                entry.entry_id,
                entry.title,
                plenticore.device_info,
                description_to_use,
                setting_data,
            )
        )

        # Track critical numbers for post-setup enablement and fetching
        if (
            description.data_id in FORCE_CREATE_KEYS
            or description_to_use.data_id in FORCE_CREATE_KEYS
        ):
            forced_id = f"{entry.entry_id}_{description_to_use.module_id}_{description_to_use.data_id}"
            forced_unique_ids.add(forced_id)
            forced_unique_ids_by_data_id.setdefault(
                description_to_use.data_id, set()
            ).add(forced_id)
            forced_fetch_pairs.add(
                (description_to_use.module_id, description_to_use.data_id)
            )

    # Log battery settings summary
    if battery_settings_found:
        _LOGGER.info(
            "Battery number entities created: %s", ", ".join(battery_settings_found)
        )
    if battery_settings_skipped:
        _LOGGER.info(
            "Battery number entities NOT available on this inverter model (skipped): %s",
            ", ".join(battery_settings_skipped),
        )

    # Re-enable critical battery numbers if they were disabled by earlier versions.
    try:
        entity_registry = er.async_get(hass)
        entries = list(
            er.async_entries_for_config_entry(entity_registry, entry.entry_id)
        )
        entries_by_unique_id = {e.unique_id: e for e in entries if e.unique_id}

        for description in NUMBER_SETTINGS_DATA:
            if description.data_id not in FORCE_CREATE_KEYS:
                continue

            expected_unique_ids = {
                f"{entry.entry_id}_{description.module_id}_{description.data_id}",
                f"{entry.entry_id}_{description.module_id}_{LEGACY_SETTING_ALIASES.get(description.data_id, description.data_id)}",
            }
            expected_unique_ids.update(
                forced_unique_ids_by_data_id.get(description.data_id, set())
            )

            expected_entry = None
            for uid in expected_unique_ids:
                if uid in entries_by_unique_id:
                    expected_entry = entries_by_unique_id[uid]
                    break

            # Always enable the expected entry if it exists.
            if expected_entry:
                entity_registry.async_update_entity(
                    expected_entry.entity_id, disabled_by=None
                )

            for entity_entry in entries:
                if entity_entry.domain != "number":
                    continue

                # Match by original name when possible.
                original_name = entity_entry.original_name
                name = description.name
                if not isinstance(original_name, str) or not isinstance(name, str):
                    continue
                name_matches = original_name.endswith(name)
                if not name_matches:
                    continue

                if expected_entry:
                    # We already have the expected entry; disable the duplicate.
                    if entity_entry.entity_id != expected_entry.entity_id:
                        _LOGGER.info(
                            "Disabling duplicate number entity %s (expected one of %s)",
                            entity_entry.entity_id,
                            ", ".join(sorted(expected_unique_ids)),
                        )
                        entity_registry.async_update_entity(
                            entity_entry.entity_id,
                            disabled_by=RegistryEntryDisabler.INTEGRATION,
                        )
                    continue

                # No expected entry yet: migrate the matching old entry.
                _LOGGER.info(
                    "Migrating number unique_id for %s to %s",
                    entity_entry.entity_id,
                    next(iter(expected_unique_ids)),
                )
                entity_registry.async_update_entity(
                    entity_entry.entity_id,
                    new_unique_id=next(iter(expected_unique_ids)),
                    disabled_by=None,
                )
                expected_entry = entity_entry
    except Exception as registry_err:
        _LOGGER.debug("Entity registry migration skipped: %s", registry_err)

    _LOGGER.debug("About to add %d number entities to Home Assistant", len(entities))
    async_add_entities(entities)
    _LOGGER.debug("async_add_entities completed for %d entities", len(entities))

    # Post-registration safety pass to ensure critical battery numbers are enabled.
    async def _ensure_critical_numbers_enabled(_now: datetime) -> None:
        try:
            entity_registry = er.async_get(hass)
            entries = list(
                er.async_entries_for_config_entry(entity_registry, entry.entry_id)
            )
            entries_by_unique_id = {e.unique_id: e for e in entries if e.unique_id}

            for description in NUMBER_SETTINGS_DATA:
                if description.data_id not in FORCE_CREATE_KEYS:
                    continue

                expected_unique_ids = {
                    f"{entry.entry_id}_{description.module_id}_{description.data_id}",
                    f"{entry.entry_id}_{description.module_id}_{LEGACY_SETTING_ALIASES.get(description.data_id, description.data_id)}",
                }
                expected_unique_ids.update(
                    forced_unique_ids_by_data_id.get(description.data_id, set())
                )

                expected_entry = None
                for uid in expected_unique_ids:
                    if uid in entries_by_unique_id:
                        expected_entry = entries_by_unique_id[uid]
                        break

                if expected_entry:
                    entity_registry.async_update_entity(
                        expected_entry.entity_id, disabled_by=None
                    )

                for entity_entry in entries:
                    if entity_entry.domain != "number":
                        continue
                    original_name = entity_entry.original_name
                    name = description.name
                    if not isinstance(original_name, str) or not isinstance(name, str):
                        continue
                    name_matches = original_name.endswith(name)
                    if not name_matches:
                        continue
                    if (
                        expected_entry
                        and entity_entry.entity_id != expected_entry.entity_id
                    ):
                        entity_registry.async_update_entity(
                            entity_entry.entity_id,
                            disabled_by=RegistryEntryDisabler.INTEGRATION,
                        )
        except Exception as registry_err:
            _LOGGER.debug(
                "Post-registration entity registry update skipped: %s", registry_err
            )

    async_call_later(hass, 10.0, _ensure_critical_numbers_enabled)

    # Ensure coordinator fetches critical values even if entity is disabled initially.
    for module_id, data_id in forced_fetch_pairs:
        settings_data_update_coordinator.start_fetch_data(module_id, data_id)

    # Add Modbus-backed number entities if Modbus is enabled
    modbus_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    modbus_coordinator = modbus_data.get("modbus_coordinator") if modbus_data else None
    if modbus_coordinator is not None:
        from .modbus_number import create_modbus_number_entities

        modbus_entities = await create_modbus_number_entities(
            modbus_coordinator, entry.entry_id, plenticore.device_info
        )
        if modbus_entities:
            async_add_entities(modbus_entities)
            _LOGGER.info("Added %d Modbus number entities", len(modbus_entities))

        # SoC Controller entities (Target SoC, Max Charge/Discharge Power)
        soc_ctrl = modbus_data.get("soc_controller")
        if soc_ctrl is not None:  # pragma: no cover
            from .soc_controller_entities import create_soc_controller_entities

            soc_entities = create_soc_controller_entities(
                soc_ctrl, entry.entry_id, plenticore.device_info
            )
            if soc_entities:
                async_add_entities(soc_entities)
                _LOGGER.info("Added %d SoC controller entities", len(soc_entities))


class PlenticoreDataNumber(
    CoordinatorEntity[SettingDataUpdateCoordinator], NumberEntity
):
    """Representation of a Kostal Plenticore Number entity."""

    def __init__(
        self,
        coordinator: SettingDataUpdateCoordinator,
        entry_id: str,
        platform_name: str,
        device_info: DeviceInfo,
        description: PlenticoreNumberEntityDescription,
        setting_data: SettingsData | None,
    ) -> None:
        """Initialize the Plenticore Number entity."""
        try:
            _LOGGER.debug(
                "Creating PlenticoreDataNumber for %s/%s",
                description.module_id,
                description.data_id,
            )
            super().__init__(coordinator)

            self.entity_description = description
            self._module_id = description.module_id
            self._data_id = description.data_id
            self.entry_id = entry_id

            self._attr_device_info = device_info
            self._attr_unique_id = f"{self.entry_id}_{self.module_id}_{self.data_id}"
            self._attr_has_entity_name = True
            name = description.name if isinstance(description.name, str) else ""
            self._attr_name = name
            self._attr_translation_key = _normalize_translation_key(description.key)
            self._attr_mode = NumberMode.BOX

            self._formatter = PlenticoreDataFormatter.get_method(description.fmt_from)
            self._formatter_back = PlenticoreDataFormatter.get_method(
                description.fmt_to
            )

            self._keepalive_task: asyncio.Task[None] | None = None
            self._keepalive_value: float | None = None

            # overwrite from retrieved setting data if available
            if setting_data is not None:
                if setting_data.min is not None:
                    self._attr_native_min_value = self._formatter(setting_data.min)
                if setting_data.max is not None:
                    self._attr_native_max_value = self._formatter(setting_data.max)

            _LOGGER.debug(
                "PlenticoreDataNumber created: unique_id=%s, name=%s",
                self._attr_unique_id,
                self._attr_name,
            )
        except Exception as err:
            _LOGGER.error(
                "Exception creating PlenticoreDataNumber for %s/%s: %s",
                description.module_id,
                description.data_id,
                err,
                exc_info=True,
            )
            raise

    @property
    def module_id(self) -> str:
        """Return the plenticore module id of this entity."""
        return self._module_id

    @property
    def data_id(self) -> str:
        """Return the plenticore data id for this entity."""
        return self._data_id

    @property
    def available(self) -> bool:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return if entity is available."""
        base_available = super().available
        has_data = self.coordinator.data is not None
        has_module = has_data and self.module_id in self.coordinator.data
        has_value = has_module and any(
            candidate in self.coordinator.data[self.module_id]
            for candidate in self._iter_data_id_candidates()
        )

        return base_available and has_data and has_module and has_value

    def _requires_installer(self, data_id_for_write: str) -> bool:
        """Return if this control requires installer service code."""
        return requires_installer_service_code(data_id_for_write)

    def _should_keepalive(self, data_id_for_write: str) -> bool:
        """Return if keepalive cyclic write is required for this data_id."""
        return data_id_for_write in G3_CYCLIC_LIMIT_IDS

    def _parse_seconds(self, value: Any) -> int | None:
        """Parse a seconds value from the coordinator data safely."""
        if value is None:
            return None
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    def _get_keepalive_interval(self) -> int:
        """Calculate the keepalive interval based on fallback timer."""
        fallback_value = None
        if self.coordinator.data and self.module_id in self.coordinator.data:
            fallback_value = self.coordinator.data[self.module_id].get(
                SettingId.BATTERY_LIMIT_FALLBACK_TIME
            )
        fallback_seconds = self._parse_seconds(fallback_value)
        if not fallback_seconds or fallback_seconds < 1:
            fallback_seconds = G3_FALLBACK_MIN_SECONDS
        fallback_seconds = max(
            G3_FALLBACK_MIN_SECONDS,
            min(G3_FALLBACK_MAX_SECONDS, fallback_seconds),
        )
        interval = int(max(G3_KEEPALIVE_MIN_SECONDS, fallback_seconds * 0.5))
        return min(interval, G3_KEEPALIVE_MAX_SECONDS)

    def _cancel_keepalive(self) -> None:
        """Cancel any active keepalive task."""
        self._keepalive_value = None
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
        self._keepalive_task = None

    def _start_keepalive(self, value: float) -> None:
        """Start or update the keepalive task for G3 cyclic limits."""
        if not self.hass:
            return
        self._keepalive_value = value
        if self._keepalive_task and not self._keepalive_task.done():
            return
        self._keepalive_task = self.hass.async_create_task(self._run_keepalive())

    async def _run_keepalive(self) -> None:
        """Re-apply G3 limit values cyclically to avoid fallback activation."""
        try:
            while self._keepalive_value is not None:
                await asyncio.sleep(self._get_keepalive_interval())
                if self._keepalive_value is None:
                    break
                data_id_for_write = self._resolve_data_id_for_write()
                if not self._should_keepalive(data_id_for_write):
                    break
                entry = self.coordinator.config_entry
                if not ensure_installer_access(
                    entry,
                    self._requires_installer(data_id_for_write),
                    self.module_id,
                    data_id_for_write,
                    "keepalive",
                    hass=self.hass,
                ):
                    break
                str_value = self._formatter_back(self._keepalive_value)
                await self.coordinator.async_write_data(
                    self.module_id, {data_id_for_write: str_value}
                )
        except asyncio.CancelledError:
            return
        except Exception as err:
            _LOGGER.debug(
                "Keepalive failed for %s/%s: %s",
                self.module_id,
                self.data_id,
                err,
            )

    async def async_added_to_hass(self) -> None:
        """Register this entity on the Update Coordinator."""
        await super().async_added_to_hass()
        _LOGGER.debug(
            "Number entity %s registering with coordinator for %s/%s",
            self._attr_name,
            self.module_id,
            self.data_id,
        )
        self.async_on_remove(
            self.coordinator.start_fetch_data(self.module_id, self.data_id)
        )
        self.async_on_remove(self._cancel_keepalive)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister this entity from the Update Coordinator."""
        self._cancel_keepalive()
        self.coordinator.stop_fetch_data(self.module_id, self.data_id)
        await super().async_will_remove_from_hass()

    @property
    def native_value(self) -> float | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the current value."""
        if self.available:
            data_id = self._resolve_data_id_for_read()
            raw_value = self.coordinator.data[self.module_id][data_id]
            value = self._formatter(raw_value)
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        return None

    async def async_set_native_value(self, value: float) -> None:
        """Set a new value."""
        data_id_for_write = self._resolve_data_id_for_write()
        # Enhanced safety logging and validation for battery controls
        if is_battery_control(data_id_for_write):
            entry = self.coordinator.config_entry

            # Check if installer service code is required for advanced battery controls
            # Advanced controls include charge/discharge setpoints and G3 limitation features
            requires_installer = self._requires_installer(data_id_for_write)

            if not ensure_installer_access(
                entry,
                requires_installer,
                self.module_id,
                data_id_for_write,
                "battery control",
                hass=self.hass,
            ):
                return

            # Validate value ranges for safety
            if "Power" in data_id_for_write and "Limit" not in data_id_for_write:
                # Charge/discharge power setpoints can be negative (charge) or positive (discharge)
                if abs(value) > 50000:
                    _LOGGER.warning(
                        "Battery power setpoint %s exceeds safe limit (50000W). Operation not performed.",
                        value,
                    )
                    return

            # Log all battery control operations for safety audit
            user_type = (
                "installer"
                if requires_installer and entry.data.get(CONF_SERVICE_CODE)
                else "user"
            )
            _LOGGER.info(
                "Setting battery control %s/%s to %s (user: %s)",
                self.module_id,
                data_id_for_write,
                value,
                user_type,
            )

        str_value = self._formatter_back(value)
        await self.coordinator.async_write_data(
            self.module_id, {data_id_for_write: str_value}
        )
        await self.coordinator.async_request_refresh()

        if self._should_keepalive(data_id_for_write):
            self._start_keepalive(value)

    def _iter_data_id_candidates(self) -> list[str]:
        """Return possible data_ids for legacy compatibility."""
        candidates = [self.data_id]
        if self.data_id in LEGACY_SETTING_ALIASES:
            candidates.append(LEGACY_SETTING_ALIASES[self.data_id])
        if self.data_id in LEGACY_SETTING_REVERSE:
            candidates.append(LEGACY_SETTING_REVERSE[self.data_id])
        return candidates

    def _resolve_data_id_for_read(self) -> str:
        """Resolve data_id based on what the coordinator currently has."""
        if not self.coordinator.data or self.module_id not in self.coordinator.data:
            return self.data_id
        for candidate in self._iter_data_id_candidates():
            if candidate in self.coordinator.data[self.module_id]:
                return candidate
        return self.data_id

    def _resolve_data_id_for_write(self) -> str:
        """Prefer an available key for writing to avoid 404s."""
        return self._resolve_data_id_for_read()
