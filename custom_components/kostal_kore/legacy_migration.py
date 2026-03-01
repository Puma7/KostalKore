"""Legacy config-entry migration helpers for KOSTAL KORE.

This module migrates a legacy ``kostal_plenticore`` config entry to an
existing ``kostal_kore`` entry while preserving entity IDs/history by moving
entity-registry entries to the new config entry.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import (
    CONF_ACCESS_ROLE,
    CONF_HOST,
    CONF_INSTALLER_ACCESS,
    CONF_PASSWORD,
    CONF_SERVICE_CODE,
    DOMAIN,
)

_LOGGER: Final = logging.getLogger(__name__)
LEGACY_DOMAIN: Final[str] = "kostal_plenticore"


@dataclass(slots=True)
class LegacyMigrationResult:
    """Summary for a legacy-to-kore migration run."""

    source_entry_id: str
    target_entry_id: str
    migrated_entities: int = 0
    migrated_devices: int = 0
    removed_target_duplicates: int = 0
    removed_source_entry: bool = False


def _rewrite_unique_id(
    unique_id: str,
    source_entry_id: str,
    target_entry_id: str,
) -> str:
    """Rewrite unique_id prefix from old config entry id to new config entry id."""
    if unique_id == source_entry_id:
        return target_entry_id
    source_prefix = f"{source_entry_id}_"
    if unique_id.startswith(source_prefix):
        return f"{target_entry_id}{unique_id[len(source_entry_id):]}"
    return unique_id


def _select_source_entry(
    hass: HomeAssistant,
    target_entry: ConfigEntry,
    source_entry_id: str | None,
) -> ConfigEntry:
    """Select the legacy source entry to migrate from."""
    legacy_entries = list(hass.config_entries.async_entries(LEGACY_DOMAIN))
    if not legacy_entries:
        raise HomeAssistantError(
            "No legacy 'kostal_plenticore' config entry found to import."
        )

    if source_entry_id:
        source_entry = next(
            (entry for entry in legacy_entries if entry.entry_id == source_entry_id),
            None,
        )
        if source_entry is None:
            raise HomeAssistantError(
                f"Legacy entry '{source_entry_id}' not found in domain '{LEGACY_DOMAIN}'."
            )
        return source_entry

    target_host = str(target_entry.data.get(CONF_HOST, "")).strip()
    if target_host:
        host_matches = [
            entry
            for entry in legacy_entries
            if str(entry.data.get(CONF_HOST, "")).strip() == target_host
        ]
        if len(host_matches) == 1:
            return host_matches[0]

    if len(legacy_entries) == 1:
        return legacy_entries[0]

    raise HomeAssistantError(
        "Multiple legacy entries found. Re-run migration with an explicit source_entry_id."
    )


def _merge_entry_data(target_entry: ConfigEntry, source_entry: ConfigEntry) -> dict[str, object]:
    """Merge source data into target data while preserving new access metadata."""
    merged_data: dict[str, object] = dict(source_entry.data)
    target_data = dict(target_entry.data)

    # Keep target role metadata when already available.
    merged_data[CONF_ACCESS_ROLE] = str(
        target_data.get(
            CONF_ACCESS_ROLE,
            merged_data.get(CONF_ACCESS_ROLE, "UNKNOWN"),
        )
    )
    merged_data[CONF_INSTALLER_ACCESS] = bool(
        target_data.get(
            CONF_INSTALLER_ACCESS,
            bool(merged_data.get(CONF_SERVICE_CODE)),
        )
    )

    # Guard rails when source entry has incomplete credentials.
    if not merged_data.get(CONF_HOST) and target_data.get(CONF_HOST):
        merged_data[CONF_HOST] = target_data[CONF_HOST]
    if not merged_data.get(CONF_PASSWORD) and target_data.get(CONF_PASSWORD):
        merged_data[CONF_PASSWORD] = target_data[CONF_PASSWORD]

    return merged_data


def _merge_options(target_entry: ConfigEntry, source_entry: ConfigEntry) -> dict[str, object]:
    """Merge source options into target options (source takes precedence)."""
    merged_options: dict[str, object] = dict(target_entry.options)
    merged_options.update(dict(source_entry.options))
    return merged_options


async def migrate_legacy_plenticore_entry(
    hass: HomeAssistant,
    target_entry_id: str,
    source_entry_id: str | None = None,
) -> LegacyMigrationResult:
    """Migrate a legacy ``kostal_plenticore`` entry to an existing ``kostal_kore`` entry."""
    target_entry = hass.config_entries.async_get_entry(target_entry_id)
    if target_entry is None:
        raise HomeAssistantError(f"Target entry '{target_entry_id}' not found.")
    if target_entry.domain != DOMAIN:
        raise HomeAssistantError(
            f"Target entry '{target_entry_id}' is not a '{DOMAIN}' entry."
        )

    source_entry = _select_source_entry(hass, target_entry, source_entry_id)
    if source_entry.entry_id == target_entry.entry_id:
        raise HomeAssistantError("Source and target entry are identical.")

    result = LegacyMigrationResult(
        source_entry_id=source_entry.entry_id,
        target_entry_id=target_entry.entry_id,
    )

    merged_data = _merge_entry_data(target_entry, source_entry)
    merged_options = _merge_options(target_entry, source_entry)
    hass.config_entries.async_update_entry(
        target_entry,
        title=source_entry.title,
        data=merged_data,
        options=merged_options,
    )

    entity_registry = er.async_get(hass)
    source_entities = list(
        er.async_entries_for_config_entry(entity_registry, source_entry.entry_id)
    )
    target_entities = list(
        er.async_entries_for_config_entry(entity_registry, target_entry.entry_id)
    )
    target_by_unique_id = {
        entity_entry.unique_id: entity_entry
        for entity_entry in target_entities
        if entity_entry.unique_id
    }

    for source_entity in source_entities:
        if source_entity.unique_id:
            rewritten_unique_id = _rewrite_unique_id(
                source_entity.unique_id,
                source_entry.entry_id,
                target_entry.entry_id,
            )
            duplicate_target_entity = target_by_unique_id.get(rewritten_unique_id)
            if (
                duplicate_target_entity is not None
                and duplicate_target_entity.entity_id != source_entity.entity_id
            ):
                entity_registry.async_remove(duplicate_target_entity.entity_id)
                result.removed_target_duplicates += 1
            if rewritten_unique_id != source_entity.unique_id:
                entity_registry.async_update_entity(
                    source_entity.entity_id,
                    config_entry_id=target_entry.entry_id,
                    new_unique_id=rewritten_unique_id,
                )
            else:
                entity_registry.async_update_entity(
                    source_entity.entity_id,
                    config_entry_id=target_entry.entry_id,
                )
        else:
            entity_registry.async_update_entity(
                source_entity.entity_id,
                config_entry_id=target_entry.entry_id,
            )
        result.migrated_entities += 1

    device_registry = dr.async_get(hass)
    source_devices = list(
        dr.async_entries_for_config_entry(device_registry, source_entry.entry_id)
    )
    for source_device in source_devices:
        updated = device_registry.async_update_device(
            source_device.id,
            add_config_entry_id=target_entry.entry_id,
            remove_config_entry_id=source_entry.entry_id,
        )
        if updated is not None:
            result.migrated_devices += 1

    await hass.config_entries.async_remove(source_entry.entry_id)
    result.removed_source_entry = (
        hass.config_entries.async_get_entry(source_entry.entry_id) is None
    )
    await hass.config_entries.async_reload(target_entry.entry_id)

    _LOGGER.info(
        "Legacy migration complete: source=%s target=%s entities=%d devices=%d duplicates_removed=%d removed_source=%s",
        result.source_entry_id,
        result.target_entry_id,
        result.migrated_entities,
        result.migrated_devices,
        result.removed_target_duplicates,
        result.removed_source_entry,
    )
    return result
