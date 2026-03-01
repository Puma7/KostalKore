"""Longevity advisor sensor entities for Home Assistant."""

from __future__ import annotations

from typing import Any, Final

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo

from .longevity_advisor import LongevityAdvisor


class BatteryLongevitySensor(SensorEntity):
    """Battery longevity assessment with chemistry-specific advice."""

    _attr_icon = "mdi:battery-clock"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Batterie Langlebigkeit"

    def __init__(self, advisor: LongevityAdvisor, entry_id: str, device_info: DeviceInfo) -> None:
        self._advisor = advisor
        self._attr_unique_id = f"{entry_id}_longevity_battery"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        return self._advisor.get_battery_temp_assessment()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # pyright: ignore[reportIncompatibleVariableOverride]
        tips = [t for t in self._advisor.get_tips() if t.component == "battery"]
        return {
            "chemistry": self._advisor.battery_chemistry,
            "chemistry_full": self._advisor.battery_chemistry_full,
            "tip_count": len(tips),
            "tips": [{"priority": t.priority, "title": t.title, "action": t.action} for t in tips],
        }


class InverterLongevitySensor(SensorEntity):
    """Inverter longevity assessment."""

    _attr_icon = "mdi:timer-cog"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Wechselrichter Langlebigkeit"

    def __init__(self, advisor: LongevityAdvisor, entry_id: str, device_info: DeviceInfo) -> None:
        self._advisor = advisor
        self._attr_unique_id = f"{entry_id}_longevity_inverter"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        return self._advisor.get_inverter_temp_assessment()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # pyright: ignore[reportIncompatibleVariableOverride]
        tips = [t for t in self._advisor.get_tips() if t.component == "inverter"]
        return {
            "tip_count": len(tips),
            "tips": [{"priority": t.priority, "title": t.title, "action": t.action} for t in tips],
        }


class PVLongevitySensor(SensorEntity):
    """PV string longevity assessment."""

    _attr_icon = "mdi:solar-power-variant"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "PV-Anlage Langlebigkeit"

    def __init__(self, advisor: LongevityAdvisor, entry_id: str, device_info: DeviceInfo) -> None:
        self._advisor = advisor
        self._attr_unique_id = f"{entry_id}_longevity_pv"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        tips = [t for t in self._advisor.get_tips() if t.component == "pv"]
        if not tips:
            return "Keine Auffälligkeiten"
        return tips[0].title

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # pyright: ignore[reportIncompatibleVariableOverride]
        tips = [t for t in self._advisor.get_tips() if t.component == "pv"]
        return {
            "tip_count": len(tips),
            "tips": [{"priority": t.priority, "title": t.title, "action": t.action} for t in tips],
        }


def create_longevity_sensors(
    advisor: LongevityAdvisor, entry_id: str, device_info: DeviceInfo,
) -> list[SensorEntity]:
    return [
        BatteryLongevitySensor(advisor, entry_id, device_info),
        InverterLongevitySensor(advisor, entry_id, device_info),
        PVLongevitySensor(advisor, entry_id, device_info),
    ]
