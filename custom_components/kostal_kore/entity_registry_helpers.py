"""Helpers to update the entity registry without triggering HA reload loops.

Home Assistant's ``EntityRegistryDisabledHandler`` schedules a config-entry
reload (``RELOAD_AFTER_UPDATE_DELAY`` = 30s) whenever an entity's ``disabled_by``
field changes.  Bulk ``async_update_entity(..., disabled_by=…)`` calls during
setup therefore cause ~55s reload cycles even though KORE never calls
``async_request_config_reload``.

These helpers only write ``disabled_by`` when the value actually changes.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_registry import RegistryEntry, RegistryEntryDisabler

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


def update_disabled_by_if_changed(
    entity_registry: er.EntityRegistry,
    entity_id: str,
    *,
    disabled_by: RegistryEntryDisabler | None,
) -> bool:
    """Update ``disabled_by`` only when it differs from the current registry value."""
    entry = entity_registry.async_get(entity_id)
    if entry is None:
        return False
    if entry.disabled_by is disabled_by:
        return False
    entity_registry.async_update_entity(entity_id, disabled_by=disabled_by)
    return True


def migrate_number_registry_before_add(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    forced_unique_ids_by_data_id: dict[str, set[str]] | None = None,
) -> None:
    """Re-enable / migrate critical number entities before ``async_add_entities``."""
    from .number import (  # noqa: PLC0415 — avoid import cycle at module load
        FORCE_CREATE_KEYS,
        LEGACY_SETTING_ALIASES,
        NUMBER_SETTINGS_DATA,
    )

    forced_map = forced_unique_ids_by_data_id or {}
    try:
        entity_registry = er.async_get(hass)
        entries = list(
            er.async_entries_for_config_entry(entity_registry, entry.entry_id)
        )
        entries_by_unique_id = {e.unique_id: e for e in entries if e.unique_id}

        for description in NUMBER_SETTINGS_DATA:
            if description.data_id not in FORCE_CREATE_KEYS:
                continue

            canonical_uid = (
                f"{entry.entry_id}_{description.module_id}_{description.data_id}"
            )
            expected_unique_ids_set = {
                canonical_uid,
                f"{entry.entry_id}_{description.module_id}_"
                f"{LEGACY_SETTING_ALIASES.get(description.data_id, description.data_id)}",
            }
            expected_unique_ids_set.update(
                forced_map.get(description.data_id, set())
            )

            expected_entry: RegistryEntry | None = None
            for uid in expected_unique_ids_set:
                if uid in entries_by_unique_id:
                    expected_entry = entries_by_unique_id[uid]
                    break

            if expected_entry is not None:
                update_disabled_by_if_changed(
                    entity_registry,
                    expected_entry.entity_id,
                    disabled_by=None,
                )

            for entity_entry in entries:
                if entity_entry.domain != "number":
                    continue

                original_name = entity_entry.original_name
                name = description.name
                if not isinstance(original_name, str) or not isinstance(name, str):
                    continue
                if not original_name.endswith(name):
                    continue

                if expected_entry is not None:
                    if entity_entry.entity_id != expected_entry.entity_id:
                        _LOGGER.debug(
                            "Found duplicate number entity %s (canonical: %s)",
                            entity_entry.entity_id,
                            expected_entry.entity_id,
                        )
                    continue

                _LOGGER.info(
                    "Migrating number unique_id for %s to %s",
                    entity_entry.entity_id,
                    canonical_uid,
                )
                entity_registry.async_update_entity(
                    entity_entry.entity_id,
                    new_unique_id=canonical_uid,
                )
                update_disabled_by_if_changed(
                    entity_registry,
                    entity_entry.entity_id,
                    disabled_by=None,
                )
                expected_entry = entity_entry
    except Exception as registry_err:
        _LOGGER.debug("Entity registry migration skipped: %s", registry_err)


def ensure_critical_numbers_enabled(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    forced_unique_ids_by_data_id: dict[str, set[str]] | None = None,
) -> None:
    """Enable critical battery numbers and disable duplicate registry rows."""
    from .number import (  # noqa: PLC0415
        FORCE_CREATE_KEYS,
        LEGACY_SETTING_ALIASES,
        NUMBER_SETTINGS_DATA,
    )

    forced_map = forced_unique_ids_by_data_id or {}
    try:
        entity_registry = er.async_get(hass)
        entries = list(
            er.async_entries_for_config_entry(entity_registry, entry.entry_id)
        )
        entries_by_unique_id = {e.unique_id: e for e in entries if e.unique_id}

        for description in NUMBER_SETTINGS_DATA:
            if description.data_id not in FORCE_CREATE_KEYS:
                continue

            expected_unique_ids = {
                f"{entry.entry_id}_{description.module_id}_{description.data_id}",
                f"{entry.entry_id}_{description.module_id}_"
                f"{LEGACY_SETTING_ALIASES.get(description.data_id, description.data_id)}",
            }
            expected_unique_ids.update(forced_map.get(description.data_id, set()))

            expected_entry: RegistryEntry | None = None
            for uid in expected_unique_ids:
                if uid in entries_by_unique_id:
                    expected_entry = entries_by_unique_id[uid]
                    break

            if expected_entry is not None:
                update_disabled_by_if_changed(
                    entity_registry,
                    expected_entry.entity_id,
                    disabled_by=None,
                )

            for entity_entry in entries:
                if entity_entry.domain != "number":
                    continue
                original_name = entity_entry.original_name
                name = description.name
                if not isinstance(original_name, str) or not isinstance(name, str):
                    continue
                if not original_name.endswith(name):
                    continue
                if (
                    expected_entry is not None
                    and entity_entry.entity_id != expected_entry.entity_id
                ):
                    update_disabled_by_if_changed(
                        entity_registry,
                        entity_entry.entity_id,
                        disabled_by=RegistryEntryDisabler.INTEGRATION,
                    )
    except Exception as registry_err:
        _LOGGER.debug(
            "Post-setup entity registry update skipped: %s", registry_err
        )


def migrate_select_registry_after_add(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Migrate legacy select unique_ids after entities are registered."""
    from .select import SELECT_SETTINGS_DATA  # noqa: PLC0415

    try:
        entity_registry = er.async_get(hass)
        entries = list(
            er.async_entries_for_config_entry(entity_registry, entry.entry_id)
        )
        entries_by_unique_id = {e.unique_id: e for e in entries if e.unique_id}

        for description in SELECT_SETTINGS_DATA:
            old_unique_id = f"{entry.entry_id}_{description.module_id}"
            new_unique_id = (
                f"{entry.entry_id}_{description.module_id}_{description.key}"
            )
            old_entry = entries_by_unique_id.get(old_unique_id)
            new_entry = entries_by_unique_id.get(new_unique_id)

            if old_entry and new_entry:
                entity_registry.async_remove(new_entry.entity_id)
                try:
                    entity_registry.async_update_entity(
                        old_entry.entity_id,
                        new_unique_id=new_unique_id,
                    )
                    update_disabled_by_if_changed(
                        entity_registry,
                        old_entry.entity_id,
                        disabled_by=None,
                    )
                except Exception as update_err:
                    _LOGGER.warning(
                        "Select migration: failed to update %s to %s: %s. "
                        "The duplicate entity will be recreated on next restart.",
                        old_unique_id,
                        new_unique_id,
                        update_err,
                    )
                continue

            if old_entry:
                entity_registry.async_update_entity(
                    old_entry.entity_id,
                    new_unique_id=new_unique_id,
                )
                update_disabled_by_if_changed(
                    entity_registry,
                    old_entry.entity_id,
                    disabled_by=None,
                )
    except Exception as err:
        _LOGGER.debug("Select entity registry migration failed: %s", err)


def run_post_setup_entity_registry_maintenance(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Run deferred registry maintenance once after all platforms are loaded."""
    ensure_critical_numbers_enabled(hass, entry)
    _LOGGER.debug(
        "Post-setup entity registry maintenance completed for %s",
        entry.title,
    )
