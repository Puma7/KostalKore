"""Home Assistant entities for the battery SoC controller.

Provides user-friendly number entities:
    - Battery Target SoC (10-100%)
    - Battery Max Charge Power (W)
    - Battery Max Discharge Power (W)

Setting the Target SoC starts the automatic controller. Setting it to 0
(or the built-in minimum) stops the controller and returns to automatic.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfPower,
)
from homeassistant.helpers.device_registry import DeviceInfo

from .battery_soc_controller import BatterySocController
from .modbus_coordinator import ModbusDataUpdateCoordinator

_LOGGER: Final = logging.getLogger(__name__)


class TargetSocNumber(NumberEntity):
    """Number entity to set the battery target SoC."""

    _attr_has_entity_name = True
    _attr_name = "Battery Target SoC"
    _attr_icon = "mdi:battery-charging-wireless"
    _attr_native_min_value = 0
    _attr_native_max_value = 95
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        controller: BatterySocController,
        entry_id: str,
        device_info: DeviceInfo,
    ) -> None:
        self._controller = controller
        self._attr_unique_id = f"{entry_id}_battery_target_soc"
        self._attr_device_info = device_info
        self._value: float = 0

    @property
    def native_value(self) -> float:
        return self._value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "controller_status": self._controller.status,
            "controller_active": self._controller.active,
        }

    async def async_set_native_value(self, value: float) -> None:
        self._value = value
        if value < 10:
            _LOGGER.info("SoC Controller: Target auf 0 gesetzt → Automatik")
            await self._controller.set_target(None)
        else:
            _LOGGER.info("SoC Controller: Target auf %.0f%% gesetzt", value)
            await self._controller.set_target(value)
        self.async_write_ha_state()


class MaxChargePowerNumber(NumberEntity):
    """Number entity to set the maximum charge power."""

    _attr_has_entity_name = True
    _attr_name = "Battery Max Charge Power (SoC Ctrl)"
    _attr_icon = "mdi:battery-charging-high"
    _attr_native_min_value = 100
    _attr_native_max_value = 20000
    _attr_native_step = 100
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        controller: BatterySocController,
        entry_id: str,
        device_info: DeviceInfo,
    ) -> None:
        self._controller = controller
        self._attr_unique_id = f"{entry_id}_soc_ctrl_max_charge"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> float:
        return self._controller.max_charge_power

    async def async_set_native_value(self, value: float) -> None:
        self._controller.set_max_charge_power(value)
        self.async_write_ha_state()


class MaxDischargePowerNumber(NumberEntity):
    """Number entity to set the maximum discharge power."""

    _attr_has_entity_name = True
    _attr_name = "Battery Max Discharge Power (SoC Ctrl)"
    _attr_icon = "mdi:battery-arrow-down"
    _attr_native_min_value = 100
    _attr_native_max_value = 20000
    _attr_native_step = 100
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        controller: BatterySocController,
        entry_id: str,
        device_info: DeviceInfo,
    ) -> None:
        self._controller = controller
        self._attr_unique_id = f"{entry_id}_soc_ctrl_max_discharge"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> float:
        return self._controller.max_discharge_power

    async def async_set_native_value(self, value: float) -> None:
        self._controller.set_max_discharge_power(value)
        self.async_write_ha_state()


def create_soc_controller_entities(
    controller: BatterySocController,
    entry_id: str,
    device_info: DeviceInfo,
) -> list[NumberEntity]:
    """Create the SoC controller number entities."""
    return [
        TargetSocNumber(controller, entry_id, device_info),
        MaxChargePowerNumber(controller, entry_id, device_info),
        MaxDischargePowerNumber(controller, entry_id, device_info),
    ]
