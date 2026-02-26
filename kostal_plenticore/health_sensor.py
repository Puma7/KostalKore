"""Health monitoring sensor entities for Kostal Plenticore.

Provides diagnostic sensors that expose inverter health data:
- Overall health score (0-100)
- Isolation resistance with trend
- Controller/battery temperature with min/max tracking
- Battery health (SoH trend, cycle count, capacity loss)
- Error rate and communication reliability
"""

from __future__ import annotations

import logging
from typing import Any, Final

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    PERCENTAGE,
    UnitOfTemperature,
)
from homeassistant.helpers.device_registry import DeviceInfo

from .health_monitor import HealthLevel, InverterHealthMonitor

_LOGGER: Final = logging.getLogger(__name__)


class HealthScoreSensor(SensorEntity):
    """Overall inverter health score (0-100)."""

    _attr_icon = "mdi:heart-pulse"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Inverter Health Score"

    def __init__(self, monitor: InverterHealthMonitor, entry_id: str, device_info: DeviceInfo) -> None:
        self._monitor = monitor
        self._attr_unique_id = f"{entry_id}_health_score"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> int:  # pyright: ignore[reportIncompatibleVariableOverride]
        return self._monitor.health_score

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # pyright: ignore[reportIncompatibleVariableOverride]
        return {
            "overall_health": self._monitor.overall_health.value,
            "communication_reliability": round(self._monitor.communication_reliability, 1),
            "error_rate_per_hour": round(self._monitor.error_rate_per_hour, 1),
            "total_polls": self._monitor._total_polls,
            "failed_polls": self._monitor._failed_polls,
        }


class HealthLevelSensor(SensorEntity):
    """Overall health level as text (excellent/good/warning/critical)."""

    _attr_icon = "mdi:shield-check"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Inverter Health Level"

    def __init__(self, monitor: InverterHealthMonitor, entry_id: str, device_info: DeviceInfo) -> None:
        self._monitor = monitor
        self._attr_unique_id = f"{entry_id}_health_level"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        return self._monitor.overall_health.value

    @property
    def icon(self) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        level = self._monitor.overall_health
        if level == HealthLevel.CRITICAL:
            return "mdi:shield-alert"
        if level == HealthLevel.WARNING:
            return "mdi:shield-half-full"
        return "mdi:shield-check"


class IsolationResistanceSensor(SensorEntity):
    """Isolation resistance with trend tracking."""

    _attr_icon = "mdi:resistor"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "Ohm"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Isolation Resistance (Health)"

    def __init__(self, monitor: InverterHealthMonitor, entry_id: str, device_info: DeviceInfo) -> None:
        self._monitor = monitor
        self._attr_unique_id = f"{entry_id}_health_isolation"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> float | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        return self._monitor.isolation.current

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # pyright: ignore[reportIncompatibleVariableOverride]
        t = self._monitor.isolation
        return {
            "trend": t.trend,
            "level": t.level.value,
            "min": t.min_value,
            "max": t.max_value,
            "avg": round(t.avg_value, 0) if t.avg_value is not None else None,
            "samples": t.sample_count,
        }


class ControllerTempHealthSensor(SensorEntity):
    """Controller temperature with peak tracking."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Controller Temperature (Health)"

    def __init__(self, monitor: InverterHealthMonitor, entry_id: str, device_info: DeviceInfo) -> None:
        self._monitor = monitor
        self._attr_unique_id = f"{entry_id}_health_ctrl_temp"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> float | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        return self._monitor.controller_temp.current

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # pyright: ignore[reportIncompatibleVariableOverride]
        t = self._monitor.controller_temp
        return {
            "trend": t.trend,
            "level": t.level.value,
            "peak": t.max_value,
            "avg": round(t.avg_value, 1) if t.avg_value is not None else None,
        }


class BatteryHealthSensor(SensorEntity):
    """Battery State of Health with degradation tracking."""

    _attr_icon = "mdi:battery-heart-variant"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Battery Health (SoH Trend)"

    def __init__(self, monitor: InverterHealthMonitor, entry_id: str, device_info: DeviceInfo) -> None:
        self._monitor = monitor
        self._attr_unique_id = f"{entry_id}_health_battery_soh"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> float | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        return self._monitor.battery_soh.current

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # pyright: ignore[reportIncompatibleVariableOverride]
        soh = self._monitor.battery_soh
        cycles = self._monitor.battery_cycles
        return {
            "soh_trend": soh.trend,
            "soh_level": soh.level.value,
            "soh_min": soh.min_value,
            "cycles_current": cycles.current,
            "cycles_total": cycles.max_value,
        }


class ErrorRateSensor(SensorEntity):
    """Error rate per hour with event counter."""

    _attr_icon = "mdi:alert-circle-outline"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Error Rate (per hour)"

    def __init__(self, monitor: InverterHealthMonitor, entry_id: str, device_info: DeviceInfo) -> None:
        self._monitor = monitor
        self._attr_unique_id = f"{entry_id}_health_error_rate"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> float:  # pyright: ignore[reportIncompatibleVariableOverride]
        return round(self._monitor.error_rate_per_hour, 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # pyright: ignore[reportIncompatibleVariableOverride]
        recent = self._monitor.recent_events
        return {
            "total_events": self._monitor.event_count,
            "recent_events": [
                {"category": e.category, "message": e.message, "level": e.level.value}
                for e in recent[-5:]
            ],
        }


class CommunicationReliabilitySensor(SensorEntity):
    """Modbus communication success rate."""

    _attr_icon = "mdi:lan-connect"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Modbus Communication Reliability"

    def __init__(self, monitor: InverterHealthMonitor, entry_id: str, device_info: DeviceInfo) -> None:
        self._monitor = monitor
        self._attr_unique_id = f"{entry_id}_health_comm_reliability"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> float:  # pyright: ignore[reportIncompatibleVariableOverride]
        return round(self._monitor.communication_reliability, 1)


def create_health_sensors(
    monitor: InverterHealthMonitor,
    entry_id: str,
    device_info: DeviceInfo,
) -> list[SensorEntity]:
    """Create all health monitoring sensor entities."""
    return [
        HealthScoreSensor(monitor, entry_id, device_info),
        HealthLevelSensor(monitor, entry_id, device_info),
        IsolationResistanceSensor(monitor, entry_id, device_info),
        ControllerTempHealthSensor(monitor, entry_id, device_info),
        BatteryHealthSensor(monitor, entry_id, device_info),
        ErrorRateSensor(monitor, entry_id, device_info),
        CommunicationReliabilitySensor(monitor, entry_id, device_info),
    ]
