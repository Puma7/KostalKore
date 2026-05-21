"""Sensors that expose the calculated battery SoH (see battery_soh_calculator)."""

from __future__ import annotations

from typing import Any, Final

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, PERCENTAGE
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .battery_soh_calculator import BatterySohCalculator
from .modbus_coordinator import ModbusDataUpdateCoordinator


class _BatterySohBase(
    CoordinatorEntity[ModbusDataUpdateCoordinator], SensorEntity
):
    """Common base for both SoH sensors."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ModbusDataUpdateCoordinator,
        calc: BatterySohCalculator,
        entry_id: str,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self._calc = calc
        self._attr_device_info = device_info


class BatterySohCalculatedSensor(_BatterySohBase):
    """Current calculated SoH from work_capacity vs baseline."""

    _attr_icon = "mdi:battery-heart-outline"
    _attr_name = "Battery SoH (Calculated)"

    def __init__(
        self,
        coordinator: ModbusDataUpdateCoordinator,
        calc: BatterySohCalculator,
        entry_id: str,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator, calc, entry_id, device_info)
        self._attr_unique_id = f"{entry_id}_battery_soh_calculated"

    @property
    def available(self) -> bool:  # pyright: ignore[reportIncompatibleVariableOverride]
        return self._calc.soh_pct is not None

    @property
    def native_value(self) -> float | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        val = self._calc.soh_pct
        return round(val, 2) if val is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # pyright: ignore[reportIncompatibleVariableOverride]
        age = self._calc.baseline_age_days
        return {
            "baseline_wh": self._calc.baseline_capacity_wh,
            "current_wh": self._calc.current_capacity_wh,
            "baseline_age_days": round(age, 1) if age is not None else None,
            "total_throughput_kwh": (
                round(self._calc.total_throughput_kwh, 1)
                if self._calc.total_throughput_kwh is not None else None
            ),
            "cycles_observed": self._calc.cycles,
            "samples": self._calc.sample_count,
        }


class BatterySohProjection5yearsSensor(_BatterySohBase):
    """Extrapolated SoH in 5 years from observed degradation slope."""

    _attr_icon = "mdi:chart-line-variant"
    _attr_name = "Battery SoH Projection 5y"

    def __init__(
        self,
        coordinator: ModbusDataUpdateCoordinator,
        calc: BatterySohCalculator,
        entry_id: str,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator, calc, entry_id, device_info)
        self._attr_unique_id = f"{entry_id}_battery_soh_projection_5y"

    @property
    def available(self) -> bool:  # pyright: ignore[reportIncompatibleVariableOverride]
        return self._calc.soh_projection_5y_pct is not None

    @property
    def native_value(self) -> float | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        val = self._calc.soh_projection_5y_pct
        return round(val, 2) if val is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # pyright: ignore[reportIncompatibleVariableOverride]
        slope = self._calc.degradation_per_kwh
        annual = self._calc.annual_throughput_kwh
        return {
            "degradation_per_kwh": round(slope, 4) if slope is not None else None,
            "annual_throughput_kwh": round(annual, 1) if annual is not None else None,
            "samples": self._calc.sample_count,
        }


def create_battery_soh_sensors(
    coordinator: ModbusDataUpdateCoordinator,
    calc: BatterySohCalculator,
    entry_id: str,
    device_info: DeviceInfo,
) -> list[SensorEntity]:
    return [
        BatterySohCalculatedSensor(coordinator, calc, entry_id, device_info),
        BatterySohProjection5yearsSensor(coordinator, calc, entry_id, device_info),
    ]
