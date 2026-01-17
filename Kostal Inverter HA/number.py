"""Platform for Kostal Plenticore numbers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging

from pykoplenti import ApiException, SettingsData

from aiohttp.client_exceptions import ClientError

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfPower, UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_SERVICE_CODE
from .coordinator import PlenticoreConfigEntry, SettingDataUpdateCoordinator, _parse_modbus_exception
from .helper import PlenticoreDataFormatter

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class PlenticoreNumberEntityDescription(NumberEntityDescription):
    """A class that describes plenticore number entities."""

    module_id: str
    data_id: str
    fmt_from: str
    fmt_to: str


NUMBER_SETTINGS_DATA = [
    PlenticoreNumberEntityDescription(
        key="battery_min_soc",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:battery-negative",
        name="Battery min SoC",
        native_unit_of_measurement=PERCENTAGE,
        native_max_value=100,
        native_min_value=5,
        native_step=5,
        module_id="devices:local",
        data_id="Battery:MinSoc",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    # Battery:MaxSoc removed - not available in REST API
    # Use Battery:ExternControl:MaxSocRel instead if needed
    PlenticoreNumberEntityDescription(
        key="battery_min_home_consumption",
        device_class=NumberDeviceClass.POWER,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        name="Battery min Home Consumption",
        native_unit_of_measurement=UnitOfPower.WATT,
        native_max_value=38000,
        native_min_value=50,
        native_step=1,
        module_id="devices:local",
        # Typo in Kostal API: 'Comsumption' instead of 'Consumption' - do NOT fix this string or the entity will fail
        data_id="Battery:MinHomeComsumption",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    # Battery Charge/Discharge Setpoints (Section 3.4 External Battery Management)
    # Note: Negative values charge the battery, positive values discharge the battery
    PlenticoreNumberEntityDescription(
        key="battery_charge_power_ac_absolute",
        device_class=NumberDeviceClass.POWER,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:battery-charging-100",
        name="Battery Charge Power (AC) Absolute",
        native_unit_of_measurement=UnitOfPower.WATT,
        native_max_value=50000,
        native_min_value=-50000,
        native_step=100,
        module_id="devices:local",
        data_id="Battery:ExternControl:AcPowerAbs",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="battery_charge_current_dc_relative",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:battery-charging",
        name="Battery Charge Current (DC) Relative",
        native_unit_of_measurement=PERCENTAGE,
        native_max_value=100,
        native_min_value=-100,
        native_step=1,
        module_id="devices:local",
        data_id="Battery:ExternControl:DcCurrentRel",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="battery_charge_power_ac_relative",
        device_class=NumberDeviceClass.POWER,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:battery-charging",
        name="Battery Charge Power (AC) Relative",
        native_unit_of_measurement=PERCENTAGE,
        native_max_value=100,
        native_min_value=-100,
        native_step=1,
        module_id="devices:local",
        data_id="Battery:ExternControl:AcPowerRel",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="battery_charge_current_dc_absolute",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:battery-charging",
        name="Battery Charge Current (DC) Absolute",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        native_max_value=200,
        native_min_value=-200,
        native_step=1,
        module_id="devices:local",
        data_id="Battery:ExternControl:DcCurrentAbs",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="battery_charge_power_dc_absolute",
        device_class=NumberDeviceClass.POWER,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:battery-charging",
        name="Battery Charge Power (DC) Absolute",
        native_unit_of_measurement=UnitOfPower.WATT,
        native_max_value=50000,
        native_min_value=-50000,
        native_step=100,
        module_id="devices:local",
        data_id="Battery:ExternControl:DcPowerAbs",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="battery_charge_power_dc_relative",
        device_class=NumberDeviceClass.POWER,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:battery-charging",
        name="Battery Charge Power (DC) Relative",
        native_unit_of_measurement=PERCENTAGE,
        native_max_value=100,
        native_min_value=-100,
        native_step=1,
        module_id="devices:local",
        data_id="Battery:ExternControl:DcPowerRel",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    # Battery Limitation (G3 Only - Section 3.5)
    # Available only for PLENTICORE G3 inverters from software version 03.05.xxxxx
    # Note: Registers 0x500 and 0x502 must be written cyclically. If not written,
    # after the time in 0x508, the fallback limits (0x504, 0x506) become active.
    PlenticoreNumberEntityDescription(
        key="battery_max_charge_power_g3",
        device_class=NumberDeviceClass.POWER,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:battery-charging-limit",
        name="Battery Max Charge Power (G3)",
        native_unit_of_measurement=UnitOfPower.WATT,
        native_max_value=50000,
        native_min_value=0,
        native_step=100,
        module_id="devices:local",
        data_id="Battery:Limit:Charge_P",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="battery_max_discharge_power_g3",
        device_class=NumberDeviceClass.POWER,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:battery-discharging-limit",
        name="Battery Max Discharge Power (G3)",
        native_unit_of_measurement=UnitOfPower.WATT,
        native_max_value=50000,
        native_min_value=0,
        native_step=100,
        module_id="devices:local",
        data_id="Battery:Limit:Discharge_P",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="battery_max_charge_power_fallback",
        device_class=NumberDeviceClass.POWER,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:battery-charging-limit",
        name="Battery Max Charge Power Fallback (G3)",
        native_unit_of_measurement=UnitOfPower.WATT,
        native_max_value=50000,
        native_min_value=0,
        native_step=100,
        module_id="devices:local",
        data_id="Battery:Limit:FallbackCharge_P",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="battery_max_discharge_power_fallback",
        device_class=NumberDeviceClass.POWER,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:battery-discharging-limit",
        name="Battery Max Discharge Power Fallback (G3)",
        native_unit_of_measurement=UnitOfPower.WATT,
        native_max_value=50000,
        native_min_value=0,
        native_step=100,
        module_id="devices:local",
        data_id="Battery:Limit:FallbackDischarge_P",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="battery_time_until_fallback",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:timer",
        name="Battery Time Until Fallback (G3)",
        native_unit_of_measurement="s",
        native_max_value=10800,
        native_min_value=1,
        native_step=1,
        module_id="devices:local",
        data_id="Battery:Limit:FallbackTime",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    # Additional External Control Settings
    PlenticoreNumberEntityDescription(
        key="battery_extern_control_max_charge_power_abs",
        device_class=NumberDeviceClass.POWER,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:battery-charging-limit",
        name="Battery External Control Max Charge Power Absolute",
        native_unit_of_measurement=UnitOfPower.WATT,
        native_max_value=50000,
        native_min_value=0,
        native_step=100,
        module_id="devices:local",
        data_id="Battery:ExternControl:MaxChargePowerAbs",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="battery_extern_control_max_discharge_power_abs",
        device_class=NumberDeviceClass.POWER,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:battery-discharging-limit",
        name="Battery External Control Max Discharge Power Absolute",
        native_unit_of_measurement=UnitOfPower.WATT,
        native_max_value=50000,
        native_min_value=0,
        native_step=100,
        module_id="devices:local",
        data_id="Battery:ExternControl:MaxDischargePowerAbs",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="battery_extern_control_max_soc_rel",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:battery-positive",
        name="Battery External Control Max SoC Relative",
        native_unit_of_measurement=PERCENTAGE,
        native_max_value=100,
        native_min_value=0,
        native_step=1,
        module_id="devices:local",
        data_id="Battery:ExternControl:MaxSocRel",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="battery_extern_control_min_soc_rel",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:battery-negative",
        name="Battery External Control Min SoC Relative",
        native_unit_of_measurement=PERCENTAGE,
        native_max_value=100,
        native_min_value=0,
        native_step=1,
        module_id="devices:local",
        data_id="Battery:ExternControl:MinSocRel",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    # ESB (Emergency Supply Battery) Settings
    PlenticoreNumberEntityDescription(
        key="battery_esb_min_soc",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:battery-alert",
        name="Battery ESB Minimum SoC",
        native_unit_of_measurement=PERCENTAGE,
        native_max_value=100,
        native_min_value=5,
        native_step=1,
        module_id="devices:local",
        data_id="Battery:Esb:MinSoC",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="battery_esb_start_soc",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:battery-alert-variant",
        name="Battery ESB Start SoC",
        native_unit_of_measurement=PERCENTAGE,
        native_max_value=100,
        native_min_value=5,
        native_step=1,
        module_id="devices:local",
        data_id="Battery:Esb:StartSoC",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    # Winter Mode Settings
    PlenticoreNumberEntityDescription(
        key="battery_winter_min_soc",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:snowflake",
        name="Battery Winter Minimum SoC",
        native_unit_of_measurement=PERCENTAGE,
        native_max_value=100,
        native_min_value=5,
        native_step=1,
        module_id="devices:local",
        data_id="Battery:Winter:MinSoC",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="battery_winter_start_month",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:calendar-month",
        name="Battery Winter Start Month",
        native_unit_of_measurement=None,
        native_max_value=12,
        native_min_value=1,
        native_step=1,
        module_id="devices:local",
        data_id="Battery:Winter:StartMonth",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="battery_winter_end_month",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:calendar-month",
        name="Battery Winter End Month",
        native_unit_of_measurement=None,
        native_max_value=12,
        native_min_value=1,
        native_step=1,
        module_id="devices:local",
        data_id="Battery:Winter:EndMonth",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    # Grid Feed-in Settings
    PlenticoreNumberEntityDescription(
        key="battery_min_grid_feed_in",
        device_class=NumberDeviceClass.POWER,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:transmission-tower",
        name="Battery Minimum Grid Feed-in",
        native_unit_of_measurement=UnitOfPower.WATT,
        native_max_value=100000,
        native_min_value=50,
        native_step=1,
        module_id="devices:local",
        data_id="Battery:MinGridFeedIn",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    # Battery Communication Monitor Time
    PlenticoreNumberEntityDescription(
        key="battery_com_monitor_time",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:timer",
        name="Battery Communication Monitor Time",
        native_unit_of_measurement="s",
        native_max_value=86400,
        native_min_value=1,
        native_step=1,
        module_id="devices:local",
        data_id="Battery:ComMonitor:Time",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    # Energy Management Settings
    PlenticoreNumberEntityDescription(
        key="energy_mgmt_bat_ctrl_power_offset",
        device_class=NumberDeviceClass.POWER,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:battery-sync",
        name="Energy Management Battery Power Offset",
        native_unit_of_measurement=UnitOfPower.WATT,
        native_max_value=50000,
        native_min_value=-50000,
        native_step=100,
        module_id="devices:local",
        data_id="EnergyMgmt:BatCtrl:PowerOffset",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="energy_mgmt_limit_grid_supply",
        device_class=NumberDeviceClass.POWER,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:transmission-tower",
        name="Energy Management Limit Grid Supply",
        native_unit_of_measurement=UnitOfPower.WATT,
        native_max_value=50000,
        native_min_value=0,
        native_step=100,
        module_id="devices:local",
        data_id="EnergyMgmt:LimitGridSupply",
        fmt_from="format_round",
        fmt_to="format_round_back",
    ),
    PlenticoreNumberEntityDescription(
        key="energy_mgmt_smart_control_fallback_max_time",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        icon="mdi:timer-sand",
        name="Energy Management Fallback Max Time",
        native_unit_of_measurement="s",
        native_max_value=3600,
        native_min_value=0,
        native_step=1,
        module_id="devices:local",
        data_id="EnergyMgmt:SmartControl:FallbackMaxTime",
        fmt_from="format_round",
        fmt_to="format_round_back",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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
        module_id="devices:local",
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

    # Fetch fresh settings data
    try:
        available_settings_data = await plenticore.client.get_settings()
        
        # Diagnostic: Log all battery-related settings discovered from REST API
        battery_settings_discovered = []
        for module_id, settings_list in available_settings_data.items():
            for setting in settings_list:
                if "Battery" in setting.id:
                    battery_settings_discovered.append(f"{module_id}/{setting.id} (access: {setting.access})")
        
        if battery_settings_discovered:
            _LOGGER.info(
                "REST API discovered %d battery settings: %s",
                len(battery_settings_discovered),
                ", ".join(battery_settings_discovered)
            )
        else:
            _LOGGER.warning("REST API returned no battery settings - battery features may not be supported")
            
    except (ApiException, ClientError, TimeoutError, Exception) as err:
        error_msg = str(err)
        if isinstance(err, ApiException):
            modbus_err = _parse_modbus_exception(err)
            _LOGGER.error("Could not get settings data for numbers: %s", modbus_err.message)
        elif "Unknown API response [500]" in error_msg:
            _LOGGER.error("Inverter API returned 500 error for number settings - feature not supported on this model")
        else:
            _LOGGER.error("Could not get settings data for numbers: %s", err)
        available_settings_data = {}
    
    settings_data_update_coordinator = SettingDataUpdateCoordinator(
        hass, entry, _LOGGER, "Settings Data", timedelta(seconds=30), plenticore
    )

    # Track battery-related settings for better logging
    battery_settings_found = []
    battery_settings_skipped = []
    
    for description in NUMBER_SETTINGS_DATA:
        # Check if the module even exists before trying to access its settings
        if description.module_id not in plenticore.available_modules:
             _LOGGER.debug(
                 "Skipping number %s because module %s is not available",
                 description.name,
                 description.module_id
             )
             continue

        if (
            description.module_id not in available_settings_data
            or description.data_id
            not in (
                setting.id for setting in available_settings_data[description.module_id]
            )
        ):
            if "Battery" in description.data_id:
                battery_settings_skipped.append(f"{description.module_id}/{description.data_id}")
            _LOGGER.debug(
                "Skipping non existing setting data %s/%s",
                description.module_id,
                description.data_id,
            )
            continue
        
        if "Battery" in description.data_id:
            battery_settings_found.append(f"{description.module_id}/{description.data_id}")
        
        # Find the setting data - use more defensive approach to avoid StopIteration
        setting_data = None
        for sd in available_settings_data[description.module_id]:
            if description.data_id == sd.id:
                setting_data = sd
                break
        
        if setting_data is None:
            _LOGGER.warning(
                "Setting data %s/%s not found in available settings despite passing initial check",
                description.module_id,
                description.data_id,
            )
            continue

        entities.append(
            PlenticoreDataNumber(
                settings_data_update_coordinator,
                entry.entry_id,
                entry.title,
                plenticore.device_info,
                description,
                setting_data,
            )
        )
    
    # Log battery settings summary
    if battery_settings_found:
        _LOGGER.info(
            "Battery number entities created: %s",
            ", ".join(battery_settings_found)
        )
    if battery_settings_skipped:
        _LOGGER.warning(
            "Battery number entities NOT available on this inverter model (skipped): %s",
            ", ".join(battery_settings_skipped)
        )

    async_add_entities(entities)


class PlenticoreDataNumber(
    CoordinatorEntity[SettingDataUpdateCoordinator], NumberEntity
):
    """Representation of a Kostal Plenticore Number entity."""

    entity_description: PlenticoreNumberEntityDescription

    def __init__(
        self,
        coordinator: SettingDataUpdateCoordinator,
        entry_id: str,
        platform_name: str,
        device_info: DeviceInfo,
        description: PlenticoreNumberEntityDescription,
        setting_data: SettingsData,
    ) -> None:
        """Initialize the Plenticore Number entity."""
        super().__init__(coordinator)

        self.entity_description = description
        self.entry_id = entry_id

        self._attr_device_info = device_info
        self._attr_unique_id = f"{self.entry_id}_{self.module_id}_{self.data_id}"
        self._attr_name = f"{platform_name} {description.name}"
        self._attr_mode = NumberMode.BOX

        self._formatter = PlenticoreDataFormatter.get_method(description.fmt_from)
        self._formatter_back = PlenticoreDataFormatter.get_method(description.fmt_to)

        # overwrite from retrieved setting data
        if setting_data.min is not None:
            self._attr_native_min_value = self._formatter(setting_data.min)
        if setting_data.max is not None:
            self._attr_native_max_value = self._formatter(setting_data.max)

    @property
    def module_id(self) -> str:
        """Return the plenticore module id of this entity."""
        return self.entity_description.module_id

    @property
    def data_id(self) -> str:
        """Return the plenticore data id for this entity."""
        return self.entity_description.data_id

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            super().available
            and self.coordinator.data is not None
            and self.module_id in self.coordinator.data
            and self.data_id in self.coordinator.data[self.module_id]
        )

    async def async_added_to_hass(self) -> None:
        """Register this entity on the Update Coordinator."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.start_fetch_data(self.module_id, self.data_id)
        )

    async def async_will_remove_from_hass(self) -> None:
        """Unregister this entity from the Update Coordinator."""
        self.coordinator.stop_fetch_data(self.module_id, self.data_id)
        await super().async_will_remove_from_hass()

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        if self.available:
            raw_value = self.coordinator.data[self.module_id][self.data_id]
            return self._formatter(raw_value)

        return None

    async def async_set_native_value(self, value: float) -> None:
        """Set a new value."""
        # Enhanced safety logging and validation for battery controls
        if "Battery" in self.data_id:
            entry = self.coordinator.config_entry
            
            # Check if installer service code is required for advanced battery controls
            # Advanced controls include charge/discharge setpoints and G3 limitation features
            advanced_controls = [
                "ChargePower",
                "ChargeCurrent",
                "MaxChargePower",
                "MaxDischargePower",
                "TimeUntilFallback",
            ]
            requires_installer = any(
                control in self.data_id for control in advanced_controls
            )
            
            if requires_installer:
                if entry.data.get(CONF_SERVICE_CODE) is None:
                    _LOGGER.warning(
                        "Installer service code required for battery control %s/%s. Operation not performed.",
                        self.module_id,
                        self.data_id,
                    )
                    return
            
            # Validate value ranges for safety
            if "Power" in self.data_id and "Limit" not in self.data_id:
                # Charge/discharge power setpoints can be negative (charge) or positive (discharge)
                if abs(value) > 50000:
                    _LOGGER.warning(
                        "Battery power setpoint %s exceeds safe limit (50000W). Operation not performed.",
                        value,
                    )
                    return
            
            # Log all battery control operations for safety audit
            user_type = "installer" if requires_installer and entry.data.get(CONF_SERVICE_CODE) else "user"
            _LOGGER.info(
                "Setting battery control %s/%s to %s (user: %s)",
                self.module_id,
                self.data_id,
                value,
                user_type,
            )
        
        str_value = self._formatter_back(value)
        await self.coordinator.async_write_data(
            self.module_id, {self.data_id: str_value}
        )
        await self.coordinator.async_request_refresh()
