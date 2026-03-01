"""Fire safety sensor and binary sensor entities.

Exposes fire risk level, active alert count, and individual hazard
binary sensors for HA automations (push notifications, sirens, etc.).
"""

from __future__ import annotations

import logging
from typing import Any, Final

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo

from .fire_safety import FireRiskLevel, FireSafetyMonitor

_LOGGER: Final = logging.getLogger(__name__)


class FireRiskSensor(SensorEntity):
    """Current fire risk level."""

    _attr_icon = "mdi:fire-alert"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Fire Risk Level"

    def __init__(self, monitor: FireSafetyMonitor, entry_id: str, device_info: DeviceInfo) -> None:
        self._monitor = monitor
        self._attr_unique_id = f"{entry_id}_fire_risk_level"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        return self._monitor.current_risk_level

    @property
    def icon(self) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        level = self._monitor.current_risk_level
        if level == FireRiskLevel.EMERGENCY:
            return "mdi:fire"
        if level == FireRiskLevel.HIGH:
            return "mdi:fire-alert"
        if level == FireRiskLevel.ELEVATED:
            return "mdi:alert"
        return "mdi:shield-check"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # pyright: ignore[reportIncompatibleVariableOverride]
        active = self._monitor.active_alerts
        return {
            "active_alert_count": len(active),
            "total_alerts": len(self._monitor.alerts),
            "active_alerts": [
                {
                    "category": a.category,
                    "title": a.title,
                    "risk_level": a.risk_level,
                    "action": a.action,
                }
                for a in active[-5:]
            ],
        }


class FireAlertCountSensor(SensorEntity):
    """Number of active fire safety alerts."""

    _attr_icon = "mdi:bell-alert"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Active Safety Alerts"

    def __init__(self, monitor: FireSafetyMonitor, entry_id: str, device_info: DeviceInfo) -> None:
        self._monitor = monitor
        self._attr_unique_id = f"{entry_id}_fire_alert_count"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> int:  # pyright: ignore[reportIncompatibleVariableOverride]
        return self._monitor.alert_count


class FireSafetyOkBinarySensor(BinarySensorEntity):
    """Binary sensor: ON = system safe, OFF = safety alert active."""

    _attr_device_class = BinarySensorDeviceClass.SAFETY
    _attr_has_entity_name = True
    _attr_name = "PV System Safety"

    def __init__(self, monitor: FireSafetyMonitor, entry_id: str, device_info: DeviceInfo) -> None:
        self._monitor = monitor
        self._attr_unique_id = f"{entry_id}_fire_safety_ok"
        self._attr_device_info = device_info

    @property
    def is_on(self) -> bool:  # pyright: ignore[reportIncompatibleVariableOverride]
        level = self._monitor.current_risk_level
        if level not in (FireRiskLevel.SAFE, FireRiskLevel.MONITOR):
            active = self._monitor.active_alerts
            if active:
                import logging
                logging.getLogger(__name__).warning(
                    "PV System Safety is UNSAFE: risk=%s, alerts=%s",
                    level,
                    [(a.category, a.risk_level, a.title) for a in active[:3]],
                )
        return level in (FireRiskLevel.SAFE, FireRiskLevel.MONITOR)

    @property
    def icon(self) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        return "mdi:shield-check" if self.is_on else "mdi:shield-alert"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # pyright: ignore[reportIncompatibleVariableOverride]
        return {"risk_level": self._monitor.current_risk_level}


class IsolationDangerBinarySensor(BinarySensorEntity):
    """ON when isolation resistance indicates cable/ground fault danger."""

    _attr_device_class = BinarySensorDeviceClass.SAFETY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Isolation Fault Danger"

    def __init__(self, monitor: FireSafetyMonitor, entry_id: str, device_info: DeviceInfo) -> None:
        self._monitor = monitor
        self._attr_unique_id = f"{entry_id}_fire_isolation_danger"
        self._attr_device_info = device_info

    @property
    def is_on(self) -> bool:  # pyright: ignore[reportIncompatibleVariableOverride]
        return any(
            a.category == "isolation" and a.risk_level in (FireRiskLevel.HIGH, FireRiskLevel.EMERGENCY)
            for a in self._monitor.active_alerts
        )

    @property
    def icon(self) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        return "mdi:flash-alert" if self.is_on else "mdi:flash-off"


class BatteryFireRiskBinarySensor(BinarySensorEntity):
    """ON when battery shows thermal runaway precursors."""

    _attr_device_class = BinarySensorDeviceClass.SAFETY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Battery Fire Risk"

    def __init__(self, monitor: FireSafetyMonitor, entry_id: str, device_info: DeviceInfo) -> None:
        self._monitor = monitor
        self._attr_unique_id = f"{entry_id}_fire_battery_risk"
        self._attr_device_info = device_info

    @property
    def is_on(self) -> bool:  # pyright: ignore[reportIncompatibleVariableOverride]
        return any(
            a.category in ("battery_thermal", "battery_voltage_anomaly")
            and a.risk_level in (FireRiskLevel.HIGH, FireRiskLevel.EMERGENCY)
            for a in self._monitor.active_alerts
        )

    @property
    def icon(self) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        return "mdi:battery-alert" if self.is_on else "mdi:battery-check"


class DCCableDangerBinarySensor(BinarySensorEntity):
    """ON when DC string data indicates possible cable fault/arc risk."""

    _attr_device_class = BinarySensorDeviceClass.SAFETY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "DC Cable Danger"

    def __init__(self, monitor: FireSafetyMonitor, entry_id: str, device_info: DeviceInfo) -> None:
        self._monitor = monitor
        self._attr_unique_id = f"{entry_id}_fire_dc_cable_danger"
        self._attr_device_info = device_info

    @property
    def is_on(self) -> bool:  # pyright: ignore[reportIncompatibleVariableOverride]
        return any(
            a.category == "dc_arc_indicator"
            for a in self._monitor.active_alerts
        )

    @property
    def icon(self) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        return "mdi:cable-data" if not self.is_on else "mdi:alert-octagon"


def create_fire_safety_sensors(
    monitor: FireSafetyMonitor, entry_id: str, device_info: DeviceInfo,
) -> list[SensorEntity]:
    return [
        FireRiskSensor(monitor, entry_id, device_info),
        FireAlertCountSensor(monitor, entry_id, device_info),
    ]


def create_fire_safety_binary_sensors(
    monitor: FireSafetyMonitor, entry_id: str, device_info: DeviceInfo,
) -> list[BinarySensorEntity]:
    return [
        FireSafetyOkBinarySensor(monitor, entry_id, device_info),
        IsolationDangerBinarySensor(monitor, entry_id, device_info),
        BatteryFireRiskBinarySensor(monitor, entry_id, device_info),
        DCCableDangerBinarySensor(monitor, entry_id, device_info),
    ]
