"""Button platform for KOSTAL KORE integration."""

from __future__ import annotations

from datetime import datetime
import logging
import time
from typing import Any, Final

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_MODBUS_ENABLED,
    DATA_KEY_LEGACY_CLEANUP_CODE_INPUT,
    DATA_KEY_LEGACY_CLEANUP_GUARD,
    DOMAIN,
)
from .coordinator import PlenticoreConfigEntry
from .helper import generate_confirmation_code, integration_entry_store
from .legacy_migration import finalize_legacy_cleanup, migrate_legacy_plenticore_entry

_LOGGER: Final = logging.getLogger(__name__)
LEGACY_CLEANUP_CHALLENGE_TTL_SECONDS: Final[int] = 300
LEGACY_CLEANUP_FINAL_CONFIRM_TTL_SECONDS: Final[int] = 60
LEGACY_CLEANUP_CODE_LEN: Final[int] = 6
LEGACY_CLEANUP_CODE_ALPHABET: Final[str] = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class LegacyMigrationButton(ButtonEntity):
    """Step-1 import from legacy ``kostal_plenticore`` config entry."""

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
                remove_source_entry=False,
            )
            self._attr_extra_state_attributes = {
                "last_status": "ok",
                "last_run": datetime.now().isoformat(),
                "source_entry_id": result.source_entry_id,
                "migrated_entities": result.migrated_entities,
                "migrated_devices": result.migrated_devices,
                "duplicates_removed": result.removed_target_duplicates,
                "removed_source_entry": result.removed_source_entry,
                "next_step": "Use 'Finalize Legacy Cleanup' after validation period.",
            }
            self.async_write_ha_state()

            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "KOSTAL KORE migration completed",
                    "message": (
                        "Legacy import step 1 completed.\n\n"
                        f"Source entry: `{result.source_entry_id}`\n"
                        f"Migrated entities: **{result.migrated_entities}**\n"
                        f"Migrated devices: **{result.migrated_devices}**\n"
                        f"Removed duplicate target entities: **{result.removed_target_duplicates}**\n"
                        f"Legacy entry removed: **{result.removed_source_entry}**\n\n"
                        "Step 2 is optional and can be done later: "
                        "**Finalize Legacy Cleanup**."
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


class LegacyCleanupButton(ButtonEntity):
    """Finalize migration by removing legacy entry and leftover artifacts."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Finalize Legacy Cleanup"
    _attr_icon = "mdi:delete-sweep"

    def __init__(self, entry: PlenticoreConfigEntry) -> None:
        """Initialize legacy cleanup button."""
        self._entry_id = entry.entry_id
        self._attr_unique_id = f"{entry.entry_id}_finalize_legacy_cleanup"
        self._attr_device_info = entry.runtime_data.device_info
        self._attr_extra_state_attributes: dict[str, Any] = {
            "last_status": "idle",
        }

    async def _show_confirmation_step1(self, code: str) -> None:
        """Show first critical confirmation popup with copy/paste challenge."""
        await self.hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "KOSTAL KORE cleanup confirmation required (Step 1/2)",
                "message": (
                    "⚠️ **Critical data loss step**\n\n"
                    "Finalize Legacy Cleanup permanently removes old legacy registry data.\n\n"
                    f"**Confirmation code:** `{code}`\n\n"
                    "Please copy this code and paste it into the text entity:\n"
                    "**Legacy Cleanup Confirmation Code**\n\n"
                    "Then press **Finalize Legacy Cleanup** again to continue."
                ),
                "notification_id": f"kostal_kore_cleanup_confirm_{self._entry_id}",
            },
            blocking=True,
        )

    async def _show_confirmation_step2(self) -> None:
        """Show second confirmation popup before destructive cleanup."""
        await self.hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "KOSTAL KORE cleanup final confirmation (Step 2/2)",
                "message": (
                    "Final confirmation armed.\n\n"
                    "Press **Finalize Legacy Cleanup** one more time within "
                    f"{LEGACY_CLEANUP_FINAL_CONFIRM_TTL_SECONDS} seconds to execute "
                    "the irreversible cleanup."
                ),
                "notification_id": f"kostal_kore_cleanup_confirm_{self._entry_id}",
            },
            blocking=True,
        )

    async def _show_confirmation_mismatch(self) -> None:
        """Inform user that entered confirmation code is invalid."""
        await self.hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "KOSTAL KORE cleanup confirmation failed",
                "message": (
                    "Entered confirmation code is invalid.\n\n"
                    "Please copy the shown code exactly into **Legacy Cleanup "
                    "Confirmation Code**, then press **Finalize Legacy Cleanup** again."
                ),
                "notification_id": f"kostal_kore_cleanup_confirm_{self._entry_id}",
            },
            blocking=True,
        )

    async def _show_confirmation_expired(self) -> None:
        """Inform user that confirmation session has expired."""
        await self.hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "KOSTAL KORE cleanup confirmation expired",
                "message": (
                    "Cleanup confirmation expired for safety.\n\n"
                    "Press **Finalize Legacy Cleanup** again to start a new "
                    "confirmation challenge."
                ),
                "notification_id": f"kostal_kore_cleanup_confirm_{self._entry_id}",
            },
            blocking=True,
        )

    def _reset_cleanup_guard(self, store: dict[str, Any]) -> None:
        """Reset multi-step cleanup confirmation state."""
        store[DATA_KEY_LEGACY_CLEANUP_GUARD] = {
            "phase": 0,
            "code": None,
            "expires_at": 0.0,
        }
        store[DATA_KEY_LEGACY_CLEANUP_CODE_INPUT] = ""

    async def async_press(self) -> None:
        """Delete remaining legacy artifacts."""
        store = integration_entry_store(self.hass, self._entry_id)
        guard = dict(
            store.get(
                DATA_KEY_LEGACY_CLEANUP_GUARD,
                {"phase": 0, "code": None, "expires_at": 0.0},
            )
        )
        phase = int(guard.get("phase", 0))
        code = guard.get("code")
        expires_at = float(guard.get("expires_at", 0.0))
        now = time.monotonic()

        if phase == 2:
            if now > expires_at:
                self._reset_cleanup_guard(store)
                self._attr_extra_state_attributes = {
                    "last_status": "expired",
                    "last_run": datetime.now().isoformat(),
                }
                self.async_write_ha_state()
                await self._show_confirmation_expired()
                return
        elif phase == 1:
            if now > expires_at:
                self._reset_cleanup_guard(store)
                self._attr_extra_state_attributes = {
                    "last_status": "expired",
                    "last_run": datetime.now().isoformat(),
                }
                self.async_write_ha_state()
                await self._show_confirmation_expired()
                return
            entered_code = str(store.get(DATA_KEY_LEGACY_CLEANUP_CODE_INPUT, "")).strip().upper()
            expected_code = str(code or "")
            if entered_code != expected_code:
                self._attr_extra_state_attributes = {
                    "last_status": "awaiting_code",
                    "last_run": datetime.now().isoformat(),
                    "confirmation_phase": 1,
                    "confirmation_expires_in_s": max(0, int(expires_at - now)),
                }
                self.async_write_ha_state()
                await self._show_confirmation_mismatch()
                return

            # Code is correct -> arm final confirmation step.
            store[DATA_KEY_LEGACY_CLEANUP_GUARD] = {
                "phase": 2,
                "code": expected_code,
                "expires_at": now + LEGACY_CLEANUP_FINAL_CONFIRM_TTL_SECONDS,
            }
            store[DATA_KEY_LEGACY_CLEANUP_CODE_INPUT] = ""
            self._attr_extra_state_attributes = {
                "last_status": "awaiting_final_confirm",
                "last_run": datetime.now().isoformat(),
                "confirmation_phase": 2,
                "confirmation_expires_in_s": LEGACY_CLEANUP_FINAL_CONFIRM_TTL_SECONDS,
            }
            self.async_write_ha_state()
            await self._show_confirmation_step2()
            return
        else:
            # Step 1/2: start confirmation challenge on first press.
            confirmation_code = generate_confirmation_code(
                length=LEGACY_CLEANUP_CODE_LEN,
                alphabet=LEGACY_CLEANUP_CODE_ALPHABET,
            )
            store[DATA_KEY_LEGACY_CLEANUP_GUARD] = {
                "phase": 1,
                "code": confirmation_code,
                "expires_at": now + LEGACY_CLEANUP_CHALLENGE_TTL_SECONDS,
            }
            self._attr_extra_state_attributes = {
                "last_status": "awaiting_code",
                "last_run": datetime.now().isoformat(),
                "confirmation_phase": 1,
                "confirmation_expires_in_s": LEGACY_CLEANUP_CHALLENGE_TTL_SECONDS,
            }
            self.async_write_ha_state()
            await self._show_confirmation_step1(confirmation_code)
            return

        try:
            result = await finalize_legacy_cleanup(
                self.hass,
                target_entry_id=self._entry_id,
            )
            self._reset_cleanup_guard(store)
            self._attr_extra_state_attributes = {
                "last_status": "ok",
                "last_run": datetime.now().isoformat(),
                "source_entry_id": result.source_entry_id,
                "removed_legacy_entities": result.removed_legacy_entities,
                "detached_legacy_devices": result.detached_legacy_devices,
                "removed_source_entry": result.removed_source_entry,
            }
            self.async_write_ha_state()
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "KOSTAL KORE legacy cleanup completed",
                    "message": (
                        "Migration cleanup step completed.\n\n"
                        f"Source entry: `{result.source_entry_id}`\n"
                        f"Removed remaining legacy entities: **{result.removed_legacy_entities}**\n"
                        f"Detached legacy devices: **{result.detached_legacy_devices}**\n"
                        f"Removed legacy entry: **{result.removed_source_entry}**"
                    ),
                    "notification_id": f"kostal_kore_migration_{self._entry_id}",
                },
                blocking=True,
            )
        except Exception as err:
            self._reset_cleanup_guard(store)
            self._attr_extra_state_attributes = {
                "last_status": "error",
                "last_run": datetime.now().isoformat(),
                "error": str(err),
            }
            self.async_write_ha_state()
            _LOGGER.error("Legacy cleanup failed for entry %s: %s", self._entry_id, err)
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "KOSTAL KORE legacy cleanup failed",
                    "message": (
                        "Cleanup step failed.\n\n"
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
    entry_store = integration_entry_store(hass, entry.entry_id)
    entry_store.setdefault(
        DATA_KEY_LEGACY_CLEANUP_GUARD,
        {"phase": 0, "code": None, "expires_at": 0.0},
    )
    entry_store.setdefault(DATA_KEY_LEGACY_CLEANUP_CODE_INPUT, "")

    buttons: list[ButtonEntity] = [
        LegacyMigrationButton(entry),
        LegacyCleanupButton(entry),
    ]

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
