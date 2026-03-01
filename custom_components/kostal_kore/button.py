"""Button platform for KOSTAL KORE integration."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any, Final

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_MODBUS_ENABLED, DOMAIN
from .coordinator import PlenticoreConfigEntry
from .legacy_migration import migrate_legacy_plenticore_entry

_LOGGER: Final = logging.getLogger(__name__)


class LegacyMigrationButton(ButtonEntity):
    """One-click import from legacy ``kostal_plenticore`` config entry."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Import Legacy Plenticore Data"
    _attr_icon = "mdi:database-import"

    def __init__(self, entry: PlenticoreConfigEntry) -> None:
        """Initialize migration button."""
        self._entry_id = entry.entry_id
        self._attr_unique_id = f"{entry.entry_id}_import_legacy_plenticore_data"
        self._attr_device_info = entry.runtime_data.device_info
        self._attr_extra_state_attributes: dict[str, Any] = {
            "last_status": "idle",
        }

    async def async_press(self) -> None:
        """Migrate legacy entry, then show result via persistent notification."""
        try:
            result = await migrate_legacy_plenticore_entry(
                self.hass,
                target_entry_id=self._entry_id,
            )
            self._attr_extra_state_attributes = {
                "last_status": "ok",
                "last_run": datetime.now().isoformat(),
                "source_entry_id": result.source_entry_id,
                "migrated_entities": result.migrated_entities,
                "migrated_devices": result.migrated_devices,
                "duplicates_removed": result.removed_target_duplicates,
                "removed_source_entry": result.removed_source_entry,
            }
            self.async_write_ha_state()

            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "KOSTAL KORE migration completed",
                    "message": (
                        "Legacy data import finished.\n\n"
                        f"Source entry: `{result.source_entry_id}`\n"
                        f"Migrated entities: **{result.migrated_entities}**\n"
                        f"Migrated devices: **{result.migrated_devices}**\n"
                        f"Removed duplicate target entities: **{result.removed_target_duplicates}**\n"
                        f"Removed legacy entry: **{result.removed_source_entry}**"
                    ),
                    "notification_id": f"kostal_kore_migration_{self._entry_id}",
                },
                blocking=True,
            )
        except Exception as err:
            self._attr_extra_state_attributes = {
                "last_status": "error",
                "last_run": datetime.now().isoformat(),
                "error": str(err),
            }
            self.async_write_ha_state()
            _LOGGER.error("Legacy migration failed for entry %s: %s", self._entry_id, err)
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "KOSTAL KORE migration failed",
                    "message": (
                        "Legacy data import failed.\n\n"
                        f"Target entry: `{self._entry_id}`\n"
                        f"Error: `{err}`"
                    ),
                    "notification_id": f"kostal_kore_migration_{self._entry_id}",
                },
                blocking=True,
            )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PlenticoreConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up button entities for the integration."""
    buttons: list[ButtonEntity] = [LegacyMigrationButton(entry)]

    if entry.options.get(CONF_MODBUS_ENABLED, False):
        entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        coordinator = entry_data.get("modbus_coordinator")
        if coordinator is not None:
            from .modbus_button import create_modbus_buttons

            buttons.extend(
                create_modbus_buttons(
                    coordinator, entry.entry_id, entry.runtime_data.device_info
                )
            )

    async_add_entities(buttons)
    _LOGGER.debug("Added %d button entities", len(buttons))
