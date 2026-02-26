"""Button platform for Kostal Plenticore integration."""

from __future__ import annotations

import logging
from typing import Final

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_MODBUS_ENABLED, DOMAIN
from .coordinator import PlenticoreConfigEntry

_LOGGER: Final = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PlenticoreConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up button entities for the Kostal Plenticore integration."""
    if not entry.options.get(CONF_MODBUS_ENABLED, False):
        return

    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    coordinator = entry_data.get("modbus_coordinator")
    if coordinator is None:
        return

    plenticore = entry.runtime_data
    from .modbus_button import create_modbus_buttons

    buttons = create_modbus_buttons(
        coordinator, entry.entry_id, plenticore.device_info
    )
    if buttons:
        async_add_entities(buttons)
        _LOGGER.debug("Added %d Modbus button entities", len(buttons))
