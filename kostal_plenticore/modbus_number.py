"""Modbus-backed number entities for battery charge/discharge control."""

from __future__ import annotations

import logging
from typing import Any, Final

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfPower
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .modbus_coordinator import ModbusDataUpdateCoordinator
from .modbus_registers import (
    ModbusRegister,
    REG_BAT_CHARGE_DC_ABS_POWER,
    REG_BAT_MAX_CHARGE_LIMIT,
    REG_BAT_MAX_DISCHARGE_LIMIT,
    REG_BAT_MIN_SOC,
    REG_BAT_MAX_SOC,
    REG_ACTIVE_POWER_SETPOINT,
    REG_G3_MAX_CHARGE,
    REG_G3_MAX_DISCHARGE,
)

_LOGGER: Final = logging.getLogger(__name__)


MODBUS_NUMBER_DESCRIPTIONS: Final = [
    {
        "register": REG_BAT_CHARGE_DC_ABS_POWER,
        "name": "Battery Charge Power (Modbus)",
        "icon": "mdi:battery-charging",
        "min_value": -20000,
        "max_value": 20000,
        "step": 100,
        "unit": UnitOfPower.WATT,
        "device_class": NumberDeviceClass.POWER,
        "entity_category": EntityCategory.CONFIG,
    },
    {
        "register": REG_BAT_MAX_CHARGE_LIMIT,
        "name": "Battery Max Charge Limit (Modbus)",
        "icon": "mdi:battery-charging-high",
        "min_value": 0,
        "max_value": 20000,
        "step": 100,
        "unit": UnitOfPower.WATT,
        "device_class": NumberDeviceClass.POWER,
        "entity_category": EntityCategory.CONFIG,
    },
    {
        "register": REG_BAT_MAX_DISCHARGE_LIMIT,
        "name": "Battery Max Discharge Limit (Modbus)",
        "icon": "mdi:battery-arrow-down",
        "min_value": 0,
        "max_value": 20000,
        "step": 100,
        "unit": UnitOfPower.WATT,
        "device_class": NumberDeviceClass.POWER,
        "entity_category": EntityCategory.CONFIG,
    },
    {
        "register": REG_BAT_MIN_SOC,
        "name": "Battery Min SoC (Modbus)",
        "icon": "mdi:battery-low",
        "min_value": 0,
        "max_value": 100,
        "step": 1,
        "unit": "%",
        "device_class": None,
        "entity_category": EntityCategory.CONFIG,
    },
    {
        "register": REG_BAT_MAX_SOC,
        "name": "Battery Max SoC (Modbus)",
        "icon": "mdi:battery-high",
        "min_value": 0,
        "max_value": 100,
        "step": 1,
        "unit": "%",
        "device_class": None,
        "entity_category": EntityCategory.CONFIG,
    },
    {
        "register": REG_ACTIVE_POWER_SETPOINT,
        "name": "Active Power Setpoint (Modbus)",
        "icon": "mdi:flash",
        "min_value": 0,
        "max_value": 100,
        "step": 1,
        "unit": "%",
        "device_class": None,
        "entity_category": EntityCategory.CONFIG,
    },
    {
        "register": REG_G3_MAX_CHARGE,
        "name": "G3 Max Battery Charge Power (Modbus)",
        "icon": "mdi:battery-charging-100",
        "min_value": 0,
        "max_value": 20000,
        "step": 100,
        "unit": UnitOfPower.WATT,
        "device_class": NumberDeviceClass.POWER,
        "entity_category": EntityCategory.CONFIG,
    },
    {
        "register": REG_G3_MAX_DISCHARGE,
        "name": "G3 Max Battery Discharge Power (Modbus)",
        "icon": "mdi:battery-arrow-down-outline",
        "min_value": 0,
        "max_value": 20000,
        "step": 100,
        "unit": UnitOfPower.WATT,
        "device_class": NumberDeviceClass.POWER,
        "entity_category": EntityCategory.CONFIG,
    },
]


def create_modbus_number_entities(
    coordinator: ModbusDataUpdateCoordinator,
    entry_id: str,
    device_info: DeviceInfo,
) -> list[ModbusNumberEntity]:
    """Create Modbus-backed number entities for battery control.

    Called from the number platform's async_setup_entry when Modbus is enabled.
    """
    entities: list[ModbusNumberEntity] = []
    for desc in MODBUS_NUMBER_DESCRIPTIONS:
        entities.append(
            ModbusNumberEntity(
                coordinator=coordinator,
                register=desc["register"],
                name=desc["name"],
                icon=desc["icon"],
                min_value=desc["min_value"],
                max_value=desc["max_value"],
                step=desc["step"],
                unit=desc["unit"],
                device_class=desc.get("device_class"),
                entity_category=desc.get("entity_category"),
                entry_id=entry_id,
                device_info=device_info,
            )
        )
    return entities


class ModbusNumberEntity(
    CoordinatorEntity[ModbusDataUpdateCoordinator], NumberEntity
):
    """A number entity backed by a Kostal Modbus register."""

    def __init__(
        self,
        coordinator: ModbusDataUpdateCoordinator,
        register: ModbusRegister,
        name: str,
        icon: str,
        min_value: float,
        max_value: float,
        step: float,
        unit: str,
        device_class: NumberDeviceClass | None,
        entity_category: EntityCategory | None,
        entry_id: str,
        device_info: Any,
    ) -> None:
        super().__init__(coordinator)
        self._register = register
        self._attr_name = name
        self._attr_icon = icon
        self._attr_native_min_value = min_value
        self._attr_native_max_value = max_value
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_entity_category = entity_category
        self._attr_mode = NumberMode.BOX
        self._attr_has_entity_name = True
        self._attr_unique_id = f"{entry_id}_modbus_{register.name}"
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return if entity is available."""
        return self.coordinator.data is not None and super().available

    @property
    def native_value(self) -> float | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the current value from Modbus data."""
        if self.coordinator.data is None:
            return None
        val = self.coordinator.data.get(self._register.name)
        if val is None:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        """Write a new value to the Modbus register."""
        await self.coordinator.async_write_register(self._register, value)
        await self.coordinator.async_request_refresh()
