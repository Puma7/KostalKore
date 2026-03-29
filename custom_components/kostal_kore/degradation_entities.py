"""Degradation tracking sensor entities with persistence.

Uses HA's RestoreEntity to persist daily snapshots across restarts.
Each parameter gets a sensor showing its degradation rate and trend.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Final

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity

from .degradation_tracker import DegradationTracker, TrackedParameter

_LOGGER: Final = logging.getLogger(__name__)

_PARAM_CONFIG: Final[list[dict[str, str]]] = [
    {"key": "isolation", "name": "Degradation: Isolationswiderstand", "icon": "mdi:resistor"},
    {"key": "battery_soh", "name": "Degradation: Batterie SoH", "icon": "mdi:battery-heart-variant"},
    {"key": "battery_capacity", "name": "Degradation: Batterie Kapazität", "icon": "mdi:battery-medium"},
    {"key": "battery_temp", "name": "Degradation: Batterie Temperatur", "icon": "mdi:thermometer"},
    {"key": "controller_temp", "name": "Degradation: Controller Temperatur", "icon": "mdi:thermometer-lines"},
    {"key": "dc1_peak", "name": "Degradation: DC1 Spitzenleistung", "icon": "mdi:solar-panel"},
    {"key": "dc2_peak", "name": "Degradation: DC2 Spitzenleistung", "icon": "mdi:solar-panel"},
    {"key": "daily_yield", "name": "Degradation: Tagesertrag", "icon": "mdi:chart-line"},
]


class DegradationSensor(RestoreEntity, SensorEntity):
    """Sensor that tracks long-term degradation with persistence."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True

    def __init__(
        self,
        tracker: DegradationTracker,
        param_key: str,
        name: str,
        icon: str,
        entry_id: str,
        device_info: DeviceInfo,
    ) -> None:
        self._tracker = tracker
        self._param_key = param_key
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{entry_id}_degrad_{param_key}"
        self._attr_device_info = device_info

    def _get_param(self) -> TrackedParameter:
        return self._tracker.all_parameters[self._param_key]

    @property
    def native_value(self) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        return self._get_param().trend_description

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # pyright: ignore[reportIncompatibleVariableOverride]
        p = self._get_param()
        return {
            "days_tracked": p.days_tracked,
            "baseline_avg": round(p.baseline_avg, 2) if p.baseline_avg is not None else None,
            "current_avg": round(p.current_avg, 2) if p.current_avg is not None else None,
            "baseline_deviation_pct": round(p.baseline_deviation_pct, 1) if p.baseline_deviation_pct is not None else None,
            "rate_per_month": round(p.degradation_rate_per_month, 2) if p.degradation_rate_per_month is not None else None,
            "seasonal_avg": round(sa, 2) if (sa := p.seasonal_avg()) is not None else None,
            "seasonal_deviation_pct": round(p.seasonal_deviation_pct, 1) if p.seasonal_deviation_pct is not None else None,
            "seasonal_trend": p.seasonal_trend_description,
            "unit": p.unit,
        }

    async def async_added_to_hass(self) -> None:
        """Restore persisted data for this parameter on startup."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_extra_data()
        if last_state and last_state.as_dict():
            raw = last_state.as_dict()
            snapshot_json = raw.get("snapshot_data")
            if snapshot_json:
                try:
                    data = json.loads(snapshot_json) if isinstance(snapshot_json, str) else snapshot_json
                    # Only restore our own parameter to avoid last-entity-wins races
                    # when multiple DegradationSensors share the same tracker.
                    if self._param_key in data:
                        self._tracker.restore_from_dict({self._param_key: data[self._param_key]})
                        _LOGGER.info(
                            "Restored degradation data for %s (%d days)",
                            self._param_key,
                            self._get_param().days_tracked,
                        )
                    elif "snapshots" in data:
                        # Legacy format: per-parameter dict stored directly
                        self._tracker.restore_from_dict({self._param_key: data})
                        _LOGGER.info(
                            "Restored degradation data for %s (legacy format, %d days)",
                            self._param_key,
                            self._get_param().days_tracked,
                        )
                except Exception as err:
                    _LOGGER.debug("Could not restore degradation data for %s: %s", self._param_key, err)

    @property
    def extra_restore_state_data(self) -> ExtraStoredData | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Persist only this parameter's data (not the full tracker)."""

        class _Data(ExtraStoredData):
            def __init__(self, data: str) -> None:
                self._data = data

            def as_dict(self) -> dict[str, Any]:
                return {"snapshot_data": self._data}

        param = self._get_param()
        return _Data(json.dumps({self._param_key: param.to_dict()}, default=str))


class DegradationAlertSensor(SensorEntity):
    """Sensor showing active degradation alerts."""

    _attr_icon = "mdi:trending-down"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Degradation Alerts"

    def __init__(self, tracker: DegradationTracker, entry_id: str, device_info: DeviceInfo) -> None:
        self._tracker = tracker
        self._attr_unique_id = f"{entry_id}_degrad_alerts"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> int:  # pyright: ignore[reportIncompatibleVariableOverride]
        return len(self._tracker.get_alerts())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # pyright: ignore[reportIncompatibleVariableOverride]
        alerts = self._tracker.get_alerts()
        return {
            "alert_count": len(alerts),
            "alerts": alerts,
        }


def create_degradation_sensors(
    tracker: DegradationTracker,
    entry_id: str,
    device_info: DeviceInfo,
) -> list[SensorEntity]:
    """Create degradation tracking sensors."""
    entities: list[SensorEntity] = [
        DegradationAlertSensor(tracker, entry_id, device_info),
    ]
    for cfg in _PARAM_CONFIG:
        entities.append(DegradationSensor(
            tracker, cfg["key"], cfg["name"], cfg["icon"], entry_id, device_info,
        ))
    return entities
