"""Button entities for Modbus management actions.

Provides a 'Reset Modbus Registers' button in the HA UI that clears
the suppressed-register list, so all registers are re-polled on the
next cycle. Useful after firmware updates or inverter replacement.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo

from .modbus_coordinator import ModbusDataUpdateCoordinator

_LOGGER: Final = logging.getLogger(__name__)


class ModbusResetButton(ButtonEntity):
    """Button to reset suppressed Modbus registers."""

    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:restart"
    _attr_has_entity_name = True
    _attr_name = "Reset Modbus Registers"

    def __init__(
        self,
        coordinator: ModbusDataUpdateCoordinator,
        entry_id: str,
        device_info: DeviceInfo,
    ) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry_id}_modbus_reset_registers"
        self._attr_device_info = device_info

    async def async_press(self) -> None:
        """Handle button press -- reset all suppressed registers."""
        client = self._coordinator.client
        suppressed_count = len(client.unavailable_registers)
        client.reset_unavailable()
        _LOGGER.info(
            "Modbus register reset: cleared %d suppressed registers, "
            "all registers will be re-polled on next cycle",
            suppressed_count,
        )
        await self._coordinator.async_request_refresh()


def create_modbus_buttons(
    coordinator: ModbusDataUpdateCoordinator,
    entry_id: str,
    device_info: DeviceInfo,
) -> list[ButtonEntity]:
    """Create Modbus management button entities."""
    return [
        ModbusResetButton(coordinator, entry_id, device_info),
    ]
