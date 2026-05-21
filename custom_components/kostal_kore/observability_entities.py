"""Observability sensor entities for KostalKore.

Exposes internal operational metrics that are normally only visible in logs:
- Write-audit ring buffer (who wrote what, when, with what result)
- Request-scheduler metrics (queue depth, waits, timeouts)
- Modbus coordinator poll-phase and slow-data age
- REST ↔ Modbus consistency check for key metrics
"""

from __future__ import annotations

import logging
import time
from typing import Any, Final

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .modbus_coordinator import ModbusDataUpdateCoordinator
from .write_audit import WriteAuditLog

_LOGGER: Final = logging.getLogger(__name__)

# Consistency thresholds (W: relative, SoC: absolute percentage-points)
_WARN_REL = 0.05    # 5 % relative deviation for power values
_MISMATCH_REL = 0.15  # 15 %
_SOC_WARN_ABS = 2.0   # ±2 pp absolute for SoC
_SOC_MISMATCH_ABS = 5.0
# Absolute floor for power-pair status: prevents spurious "mismatch" at
# low-power readings where a small absolute delta becomes a large relative %.
# Status escalates only when BOTH relative AND absolute thresholds are exceeded.
_WARN_ABS_FLOOR_W = 50.0
_MISMATCH_ABS_FLOOR_W = 150.0


class WriteAuditSensor(CoordinatorEntity[ModbusDataUpdateCoordinator], SensorEntity):
    """Diagnostic sensor: write-audit events per minute + recent history.

    The rate counts ALL audit events (ok writes plus rejections/errors), not
    only successful writes — this matches the underlying audit log semantics.
    """

    _attr_icon = "mdi:pencil-box-outline"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "events/min"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Write Audit Event Rate"
    _attr_suggested_display_precision = 1

    def __init__(
        self,
        coordinator: ModbusDataUpdateCoordinator,
        audit: WriteAuditLog,
        entry_id: str,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self._audit = audit
        self._attr_unique_id = f"{entry_id}_write_audit"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> float:
        return self._audit.write_rate_per_min

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        recent = self._audit.recent
        last_10 = [e.as_dict() for e in recent[-10:]]
        return {
            "last_10_writes": last_10,
            "total_count": self._audit.total_count,
            "error_count_5min": self._audit.error_count_5min,
            "write_rate_per_min": self._audit.write_rate_per_min,
        }


class RequestSchedulerSensor(CoordinatorEntity[ModbusDataUpdateCoordinator], SensorEntity):
    """Diagnostic sensor: REST request-scheduler statistics."""

    _attr_icon = "mdi:timer-cog-outline"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Request Scheduler Requests"

    def __init__(
        self,
        coordinator: ModbusDataUpdateCoordinator,
        scheduler: Any,
        entry_id: str,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self._scheduler = scheduler
        self._attr_unique_id = f"{entry_id}_scheduler"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> int:
        stats = self._scheduler.get_stats()
        return int(stats.get("total_requests", 0))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        stats = self._scheduler.get_stats()
        return {
            "waits": stats.get("waits", 0),
            "timeouts": stats.get("timeouts", 0),
            "lock_held": stats.get("lock_held", False),
        }


class ModbusCoordinatorSensor(CoordinatorEntity[ModbusDataUpdateCoordinator], SensorEntity):
    """Diagnostic sensor: Modbus coordinator poll-phase and error counts."""

    _attr_icon = "mdi:lan-pending"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Modbus Coordinator Updates"

    def __init__(
        self,
        coordinator: ModbusDataUpdateCoordinator,
        entry_id: str,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_modbus_coord"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> int:
        return int(self.coordinator.update_count)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        age = self.coordinator.slow_data_age_s
        return {
            "poll_phase": self.coordinator.poll_phase,
            "slow_data_age_s": round(age, 1) if age is not None else None,
            "fast_error_count": self.coordinator._fast_error_count,
        }


class RestModbusConsistencySensor(CoordinatorEntity[ModbusDataUpdateCoordinator], SensorEntity):
    """Diagnostic sensor: cross-checks REST ↔ Modbus for key metrics.

    Compares three pairs:
      - Battery SoC    : REST devices:local:battery[SoC]  ↔  Modbus battery_soc
      - DC Power       : REST devices:local[Dc_P]         ↔  Modbus total_dc_power
      - Home Power     : REST devices:local[Home_P]        ↔  Modbus home_from_pv/battery/grid

    Returns "ok" / "warn" / "mismatch" / "insufficient_data".
    """

    _attr_icon = "mdi:scale-balance"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "REST↔Modbus Consistency"

    def __init__(
        self,
        coordinator: ModbusDataUpdateCoordinator,
        process_coordinator: Any,   # ProcessDataUpdateCoordinator | None
        entry_id: str,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self._process_coord = process_coordinator
        self._attr_unique_id = f"{entry_id}_consistency"
        self._attr_device_info = device_info

    def _get_rest_float(self, module_id: str, key: str) -> float | None:
        if self._process_coord is None:
            return None
        data = getattr(self._process_coord, "data", None) or {}
        module = data.get(module_id)
        if not module:
            return None
        raw = module.get(key)
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    def _check_pair(
        self,
        label: str,
        rest_val: float | None,
        modbus_val: float | None,
        *,
        absolute_threshold: bool = False,
        warn_threshold: float = _WARN_REL,
        mismatch_threshold: float = _MISMATCH_REL,
    ) -> dict[str, Any]:
        if rest_val is None or modbus_val is None:
            return {"key": label, "status": "insufficient_data"}
        if absolute_threshold:
            delta = abs(rest_val - modbus_val)
            status = "ok"
            if delta > mismatch_threshold:
                status = "mismatch"
            elif delta > warn_threshold:
                status = "warn"
            return {
                "key": label,
                "rest_val": round(rest_val, 2),
                "modbus_val": round(modbus_val, 2),
                "delta_abs": round(delta, 2),
                "status": status,
            }
        # relative comparison (avoid division by zero)
        delta_abs = abs(rest_val - modbus_val)
        denom = max(abs(rest_val), abs(modbus_val), 1.0)
        delta_pct = delta_abs / denom
        # Hybrid threshold: escalate only when BOTH relative AND absolute
        # deltas exceed the floor — small absolute deltas at low power readings
        # would otherwise trip the relative threshold spuriously.
        status = "ok"
        if delta_pct > mismatch_threshold and delta_abs > _MISMATCH_ABS_FLOOR_W:
            status = "mismatch"
        elif delta_pct > warn_threshold and delta_abs > _WARN_ABS_FLOOR_W:
            status = "warn"
        return {
            "key": label,
            "rest_val": round(rest_val, 1),
            "modbus_val": round(modbus_val, 1),
            "delta_pct": round(delta_pct * 100, 1),
            "status": status,
        }

    @property
    def native_value(self) -> str:
        pairs = self._compute_pairs()
        statuses = {p["status"] for p in pairs}
        if "mismatch" in statuses:
            return "mismatch"
        if "warn" in statuses:
            return "warn"
        # "ok" only when ALL pairs are ok — partial REST outage must not
        # silently report "ok" while some pairs show insufficient_data.
        if statuses == {"ok"}:
            return "ok"
        if "ok" in statuses:
            return "partial"
        return "insufficient_data"

    def _compute_pairs(self) -> list[dict[str, Any]]:
        modbus_data = self.coordinator.data or {}

        # SoC: absolute ±2 pp / ±5 pp
        rest_soc = self._get_rest_float("devices:local:battery", "SoC")
        modbus_soc = _to_float(modbus_data.get("battery_soc"))
        soc_pair = self._check_pair(
            "battery_soc", rest_soc, modbus_soc,
            absolute_threshold=True,
            warn_threshold=_SOC_WARN_ABS,
            mismatch_threshold=_SOC_MISMATCH_ABS,
        )

        # DC power: relative 5 % / 15 %
        rest_dc = self._get_rest_float("devices:local", "Dc_P")
        modbus_dc = _to_float(modbus_data.get("total_dc_power"))
        dc_pair = self._check_pair("dc_power_w", rest_dc, modbus_dc)

        # Home power: REST single value vs Modbus sum of three registers
        rest_home = self._get_rest_float("devices:local", "Home_P")
        home_pv = _to_float(modbus_data.get("home_from_pv")) or 0.0
        home_bat = _to_float(modbus_data.get("home_from_battery")) or 0.0
        home_grid = _to_float(modbus_data.get("home_from_grid")) or 0.0
        modbus_home: float | None = None
        if any(
            modbus_data.get(k) is not None
            for k in ("home_from_pv", "home_from_battery", "home_from_grid")
        ):
            modbus_home = abs(home_pv) + abs(home_bat) + abs(home_grid)
        home_pair = self._check_pair("home_power_w", rest_home, modbus_home)

        return [soc_pair, dc_pair, home_pair]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        pairs = self._compute_pairs()
        return {"pairs": pairs}


def _to_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def create_observability_sensors(
    modbus_coordinator: ModbusDataUpdateCoordinator,
    process_coordinator: Any,
    write_audit: WriteAuditLog,
    scheduler: Any,
    entry_id: str,
    device_info: DeviceInfo,
) -> list[SensorEntity]:
    """Factory: create all observability sensor entities for one config entry."""
    return [
        WriteAuditSensor(modbus_coordinator, write_audit, entry_id, device_info),
        RequestSchedulerSensor(modbus_coordinator, scheduler, entry_id, device_info),
        ModbusCoordinatorSensor(modbus_coordinator, entry_id, device_info),
        RestModbusConsistencySensor(modbus_coordinator, process_coordinator, entry_id, device_info),
    ]
