"""Binary sensor platform for Kostal Plenticore health warnings."""

from __future__ import annotations

import logging
from typing import Final

from homeassistant.core import HomeAssistant

from .const import AddConfigEntryEntitiesCallback, CONF_MODBUS_ENABLED, DOMAIN
from .coordinator import PlenticoreConfigEntry

_LOGGER: Final = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PlenticoreConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up health warning binary sensors."""
    if not entry.options.get(CONF_MODBUS_ENABLED, False):
        return

    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    health_monitor = entry_data.get("health_monitor")
    if health_monitor is None:
        return

    plenticore = entry.runtime_data

    from .health_binary_sensor import create_health_binary_sensors
    entities = create_health_binary_sensors(
        health_monitor, entry.entry_id, plenticore.device_info
    )

    fire_safety = entry_data.get("fire_safety")
    if fire_safety is not None:
        from .fire_safety_entities import create_fire_safety_binary_sensors
        entities.extend(create_fire_safety_binary_sensors(
            fire_safety, entry.entry_id, plenticore.device_info
        ))

    if entities:
        async_add_entities(entities)
        _LOGGER.debug("Added %d health + safety binary sensor entities", len(entities))
