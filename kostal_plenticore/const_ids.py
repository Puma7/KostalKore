"""Centralized identifiers for Plenticore API modules and data IDs."""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class ModuleId(StrEnum):
    """Known Plenticore module identifiers."""

    DEVICES_LOCAL = "devices:local"
    NETWORK = "scb:network"
    ENERGY_FLOW = "scb:statistic:EnergyFlow"


class SettingId(StrEnum):
    """Common setting identifiers used across platforms."""

    STRING_COUNT = "Properties:StringCnt"
    SHADOW_MGMT_ENABLE = "Generator:ShadowMgmt:Enable"
    HOSTNAME = "Network:Hostname"

    BATTERY_MIN_SOC = "Battery:MinSoc"
    BATTERY_MIN_SOC_REL = "Battery:MinSocRel"
    BATTERY_MIN_HOME_CONSUMPTION = "Battery:MinHomeConsumption"
    BATTERY_MIN_HOME_CONSUMPTION_LEGACY = "Battery:MinHomeComsumption"

    BATTERY_MAX_CHARGE_POWER_G3 = "Battery:MaxChargePowerG3"
    BATTERY_MAX_DISCHARGE_POWER_G3 = "Battery:MaxDischargePowerG3"
    BATTERY_TIME_UNTIL_FALLBACK = "Battery:TimeUntilFallback"


class ProcessId(StrEnum):
    """Common process data identifiers."""

    INVERTER_STATE = "Inverter:State"


STRING_FEATURE_TEMPLATE: Final[str] = "Properties:String{index}Features"


def string_feature_id(index: int) -> str:
    """Return the DC string feature data ID for the given index."""
    return STRING_FEATURE_TEMPLATE.format(index=index)
