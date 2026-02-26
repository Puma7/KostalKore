"""Health warning binary sensors for Kostal Plenticore.

Binary sensors that turn ON when a health parameter exceeds its
warning/critical threshold. Useful for HA automations (e.g. send
notification when isolation resistance drops too low).
"""

from __future__ import annotations

import logging
from typing import Any, Final

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo

from .health_monitor import HealthLevel, InverterHealthMonitor, ParameterTracker

_LOGGER: Final = logging.getLogger(__name__)


class _HealthWarningBinarySensor(BinarySensorEntity):
    """Base class for health warning binary sensors."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True

    def __init__(
        self,
        monitor: InverterHealthMonitor,
        tracker: ParameterTracker,
        entry_id: str,
        device_info: DeviceInfo,
        name: str,
        unique_suffix: str,
        icon_on: str,
        icon_off: str,
    ) -> None:
        self._monitor = monitor
        self._tracker = tracker
        self._icon_on = icon_on
        self._icon_off = icon_off
        self._attr_name = name
        self._attr_unique_id = f"{entry_id}_health_{unique_suffix}"
        self._attr_device_info = device_info

    @property
    def is_on(self) -> bool | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        level = self._tracker.level
        if level == HealthLevel.UNKNOWN:
            return None
        return level in (HealthLevel.WARNING, HealthLevel.CRITICAL)

    @property
    def icon(self) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        return self._icon_on if self.is_on else self._icon_off

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # pyright: ignore[reportIncompatibleVariableOverride]
        return {
            "level": self._tracker.level.value,
            "current_value": self._tracker.current,
            "trend": self._tracker.trend,
            "unit": self._tracker.unit,
        }


class IsolationWarning(_HealthWarningBinarySensor):
    """Warning: isolation resistance below safe threshold."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, monitor: InverterHealthMonitor, entry_id: str, device_info: DeviceInfo) -> None:
        super().__init__(
            monitor, monitor.isolation, entry_id, device_info,
            name="Isolation Resistance Warning",
            unique_suffix="warn_isolation",
            icon_on="mdi:flash-alert",
            icon_off="mdi:flash-off",
        )


class ControllerOverheatWarning(_HealthWarningBinarySensor):
    """Warning: controller temperature too high."""

    _attr_device_class = BinarySensorDeviceClass.HEAT

    def __init__(self, monitor: InverterHealthMonitor, entry_id: str, device_info: DeviceInfo) -> None:
        super().__init__(
            monitor, monitor.controller_temp, entry_id, device_info,
            name="Controller Overheat Warning",
            unique_suffix="warn_ctrl_temp",
            icon_on="mdi:thermometer-alert",
            icon_off="mdi:thermometer-check",
        )


class BatteryHealthWarning(_HealthWarningBinarySensor):
    """Warning: battery state of health degrading."""

    _attr_device_class = BinarySensorDeviceClass.BATTERY

    def __init__(self, monitor: InverterHealthMonitor, entry_id: str, device_info: DeviceInfo) -> None:
        super().__init__(
            monitor, monitor.battery_soh, entry_id, device_info,
            name="Battery Health Warning",
            unique_suffix="warn_battery_soh",
            icon_on="mdi:battery-alert-variant-outline",
            icon_off="mdi:battery-heart-variant",
        )


class BatteryTempWarning(_HealthWarningBinarySensor):
    """Warning: battery temperature too high."""

    _attr_device_class = BinarySensorDeviceClass.HEAT

    def __init__(self, monitor: InverterHealthMonitor, entry_id: str, device_info: DeviceInfo) -> None:
        super().__init__(
            monitor, monitor.battery_temp, entry_id, device_info,
            name="Battery Temperature Warning",
            unique_suffix="warn_battery_temp",
            icon_on="mdi:thermometer-alert",
            icon_off="mdi:thermometer-check",
        )


class GridFrequencyWarning(_HealthWarningBinarySensor):
    """Warning: grid frequency outside normal range."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, monitor: InverterHealthMonitor, entry_id: str, device_info: DeviceInfo) -> None:
        super().__init__(
            monitor, monitor.grid_frequency, entry_id, device_info,
            name="Grid Frequency Warning",
            unique_suffix="warn_grid_freq",
            icon_on="mdi:sine-wave",
            icon_off="mdi:sine-wave",
        )


class HighErrorRateWarning(BinarySensorEntity):
    """Warning: error rate exceeds threshold."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "High Error Rate Warning"

    def __init__(self, monitor: InverterHealthMonitor, entry_id: str, device_info: DeviceInfo) -> None:
        self._monitor = monitor
        self._attr_unique_id = f"{entry_id}_health_warn_error_rate"
        self._attr_device_info = device_info

    @property
    def is_on(self) -> bool:  # pyright: ignore[reportIncompatibleVariableOverride]
        return self._monitor.error_rate_per_hour > 5.0

    @property
    def icon(self) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        return "mdi:alert-circle" if self.is_on else "mdi:alert-circle-check-outline"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # pyright: ignore[reportIncompatibleVariableOverride]
        return {
            "error_rate_per_hour": round(self._monitor.error_rate_per_hour, 1),
            "total_events": self._monitor.event_count,
        }


def create_health_binary_sensors(
    monitor: InverterHealthMonitor,
    entry_id: str,
    device_info: DeviceInfo,
) -> list[BinarySensorEntity]:
    """Create all health warning binary sensor entities."""
    return [
        IsolationWarning(monitor, entry_id, device_info),
        ControllerOverheatWarning(monitor, entry_id, device_info),
        BatteryHealthWarning(monitor, entry_id, device_info),
        BatteryTempWarning(monitor, entry_id, device_info),
        GridFrequencyWarning(monitor, entry_id, device_info),
        HighErrorRateWarning(monitor, entry_id, device_info),
    ]
