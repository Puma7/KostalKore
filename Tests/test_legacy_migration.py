"""Tests for two-step legacy migration from kostal_plenticore."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.exceptions import HomeAssistantError

from custom_components.kostal_kore.const import (
    CONF_HOST,
    CONF_PASSWORD,
    DOMAIN,
)
from custom_components.kostal_kore.legacy_migration import (
    LEGACY_DOMAIN,
    _merge_entry_data,
    _merge_options,
    _rewrite_unique_id,
    _select_source_entry,
    adopt_legacy_entity_ids,
    discover_legacy_duplicate_entity_pairs,
    finalize_legacy_cleanup,
    migrate_legacy_plenticore_entry,
)

from pytest_homeassistant_custom_component.common import MockConfigEntry


async def test_migrate_legacy_entry_moves_entities_devices_and_data(hass):
    """Migration keeps old entity_id and moves registry bindings to new entry."""
    target_entry = MockConfigEntry(
        domain=DOMAIN,
        title="kore-target",
        data={
            CONF_HOST: "10.0.0.22",
            CONF_PASSWORD: "new-password",
            "access_role": "UNKNOWN",
            "installer_access": False,
        },
        options={"modbus_enabled": False},
    )
    source_entry = MockConfigEntry(
        domain=LEGACY_DOMAIN,
        title="legacy-source",
        data={
            CONF_HOST: "10.0.0.11",
            CONF_PASSWORD: "legacy-password",
            "service_code": "12345",
        },
        options={"modbus_enabled": True, "mqtt_bridge_enabled": True},
    )
    target_entry.add_to_hass(hass)
    source_entry.add_to_hass(hass)

    entity_registry = er.async_get(hass)
    old_entity = entity_registry.async_get_or_create(
        "sensor",
        LEGACY_DOMAIN,
        f"{source_entry.entry_id}_devices:local_Dc_P",
        config_entry=source_entry,
        suggested_object_id="legacy_dc_power",
    )
    duplicate_target_entity = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{target_entry.entry_id}_devices:local_Dc_P",
        config_entry=target_entry,
        suggested_object_id="new_dc_power",
    )

    device_registry = dr.async_get(hass)
    old_device = device_registry.async_get_or_create(
        config_entry_id=source_entry.entry_id,
        identifiers={(LEGACY_DOMAIN, "SER-12345")},
        manufacturer="Kostal",
        name="Legacy inverter",
    )

    with patch.object(
        hass.config_entries,
        "async_reload",
        AsyncMock(return_value=True),
    ) as mock_reload:
        result = await migrate_legacy_plenticore_entry(
            hass,
            target_entry_id=target_entry.entry_id,
            remove_source_entry=False,
        )
    await hass.async_block_till_done()

    assert result.source_entry_id == source_entry.entry_id
    assert result.target_entry_id == target_entry.entry_id
    assert result.migrated_entities >= 1
    assert result.migrated_devices >= 1
    assert result.removed_target_duplicates == 1
    assert result.removed_source_entry is False
    mock_reload.assert_awaited_once_with(target_entry.entry_id)

    updated_target = hass.config_entries.async_get_entry(target_entry.entry_id)
    assert updated_target is not None
    assert updated_target.data[CONF_HOST] == "10.0.0.11"
    assert updated_target.data[CONF_PASSWORD] == "legacy-password"
    assert updated_target.options["modbus_enabled"] is True
    assert updated_target.options["mqtt_bridge_enabled"] is True

    migrated_entity = entity_registry.async_get(old_entity.entity_id)
    assert migrated_entity is not None
    assert migrated_entity.config_entry_id == target_entry.entry_id
    assert migrated_entity.unique_id == f"{target_entry.entry_id}_devices:local_Dc_P"
    assert entity_registry.async_get(duplicate_target_entity.entity_id) is None

    migrated_device = device_registry.async_get(old_device.id)
    assert migrated_device is not None
    assert target_entry.entry_id in migrated_device.config_entries


def test_legacy_helper_functions_cover_rewrite_merge_and_selection(hass):
    """Helper utilities handle rewrite, merge and selection edge cases."""
    assert _rewrite_unique_id("src", "src", "dst") == "dst"
    assert _rewrite_unique_id("src_value", "src", "dst") == "dst_value"
    assert _rewrite_unique_id("plain", "src", "dst") == "plain"

    target_entry = MockConfigEntry(
        domain=DOMAIN,
        title="target",
        data={
            CONF_HOST: "10.0.0.22",
            CONF_PASSWORD: "new-password",
            "access_role": "INSTALLER",
            "installer_access": True,
        },
        options={"mqtt_bridge_enabled": False},
    )
    source_entry = MockConfigEntry(
        domain=LEGACY_DOMAIN,
        title="source",
        data={CONF_HOST: "", CONF_PASSWORD: "", "service_code": "12345"},
        options={"mqtt_bridge_enabled": True},
    )
    target_entry.add_to_hass(hass)
    source_entry.add_to_hass(hass)

    merged_data = _merge_entry_data(target_entry, source_entry)
    assert merged_data[CONF_HOST] == "10.0.0.22"
    assert merged_data[CONF_PASSWORD] == "new-password"
    assert merged_data["access_role"] == "INSTALLER"
    assert merged_data["installer_access"] is True

    merged_options = _merge_options(target_entry, source_entry)
    assert merged_options["mqtt_bridge_enabled"] is True

    assert _select_source_entry(hass, target_entry, source_entry.entry_id).entry_id == source_entry.entry_id

    with patch.object(hass.config_entries, "async_entries", return_value=[]):
        with pytest.raises(HomeAssistantError, match="No legacy"):
            _select_source_entry(hass, target_entry, None)


async def test_discover_duplicate_pairs_validates_target_and_skips_nonduplicates(hass):
    """Duplicate discovery validates target entry and ignores unmatched entities."""
    with pytest.raises(HomeAssistantError, match="not found"):
        discover_legacy_duplicate_entity_pairs(hass, "missing")

    wrong_target = MockConfigEntry(domain=LEGACY_DOMAIN, title="wrong", data={})
    wrong_target.add_to_hass(hass)
    with pytest.raises(HomeAssistantError, match="is not a 'kostal_kore' entry"):
        discover_legacy_duplicate_entity_pairs(hass, wrong_target.entry_id)

    target_entry = MockConfigEntry(
        domain=DOMAIN,
        title="target",
        data={CONF_HOST: "10.0.0.99", CONF_PASSWORD: "pw"},
    )
    source_entry = MockConfigEntry(
        domain=LEGACY_DOMAIN,
        title="source",
        data={CONF_HOST: "10.0.0.99", CONF_PASSWORD: "pw"},
    )
    target_entry.add_to_hass(hass)
    source_entry.add_to_hass(hass)

    entity_registry = er.async_get(hass)
    entity_registry.async_get_or_create(
        "sensor",
        LEGACY_DOMAIN,
        "plain_unique_id",
        config_entry=source_entry,
        suggested_object_id="legacy_plain",
    )

    assert (
        discover_legacy_duplicate_entity_pairs(
            hass,
            target_entry_id=target_entry.entry_id,
            source_entry_id=source_entry.entry_id,
        )
        == []
    )


async def test_migration_and_cleanup_cover_remaining_runtime_branches(hass):
    """Migration paths cover unchanged IDs, source removal and cleanup counting."""
    target_entry = MockConfigEntry(
        domain=DOMAIN,
        title="target",
        data={CONF_HOST: "10.0.0.55", CONF_PASSWORD: "new"},
    )
    source_entry = MockConfigEntry(
        domain=LEGACY_DOMAIN,
        title="source",
        data={CONF_HOST: "10.0.0.55", CONF_PASSWORD: "old"},
    )
    target_entry.add_to_hass(hass)
    source_entry.add_to_hass(hass)

    entity_registry = er.async_get(hass)
    old_entity = entity_registry.async_get_or_create(
        "sensor",
        LEGACY_DOMAIN,
        "plain_unique_id",
        config_entry=source_entry,
        suggested_object_id="legacy_plain_branch",
    )

    device_registry = dr.async_get(hass)
    old_device = device_registry.async_get_or_create(
        config_entry_id=source_entry.entry_id,
        identifiers={(LEGACY_DOMAIN, "SER-BRANCH-1")},
        manufacturer="Kostal",
        name="Legacy inverter",
    )

    with patch.object(
        device_registry,
        "async_update_device",
        side_effect=device_registry.async_update_device,
    ) as mock_update_device, patch.object(
        hass.config_entries,
        "async_reload",
        AsyncMock(return_value=True),
    ):
        result = await migrate_legacy_plenticore_entry(
            hass,
            target_entry_id=target_entry.entry_id,
            source_entry_id=source_entry.entry_id,
            remove_source_entry=True,
        )

    assert result.removed_source_entry is True
    assert mock_update_device.call_args.kwargs["remove_config_entry_id"] == source_entry.entry_id
    migrated = entity_registry.async_get(old_entity.entity_id)
    assert migrated is not None
    assert migrated.unique_id == "plain_unique_id"
    assert source_entry.entry_id not in device_registry.async_get(old_device.id).config_entries

    cleanup_target = MockConfigEntry(domain=DOMAIN, title="cleanup-target", data={})
    cleanup_target.add_to_hass(hass)
    cleanup_source = MockConfigEntry(domain=LEGACY_DOMAIN, title="cleanup-source", data={})
    cleanup_source.add_to_hass(hass)
    cleanup_entity = entity_registry.async_get_or_create(
        "sensor",
        LEGACY_DOMAIN,
        f"{cleanup_source.entry_id}_cleanup",
        config_entry=cleanup_source,
        suggested_object_id="cleanup_legacy",
    )

    with patch.object(
        hass.config_entries,
        "async_reload",
        AsyncMock(return_value=True),
    ):
        cleanup = await finalize_legacy_cleanup(
            hass,
            target_entry_id=cleanup_target.entry_id,
            source_entry_id=cleanup_source.entry_id,
        )

    assert cleanup.removed_legacy_entities == 1
    assert entity_registry.async_get(cleanup_entity.entity_id) is None


async def test_migrate_legacy_entry_prefers_host_match_when_multiple_sources(hass):
    """When multiple legacy entries exist, matching host is selected automatically."""
    target_entry = MockConfigEntry(
        domain=DOMAIN,
        title="kore-target",
        data={CONF_HOST: "10.0.0.11", CONF_PASSWORD: "new-password"},
    )
    source_match = MockConfigEntry(
        domain=LEGACY_DOMAIN,
        title="legacy-match",
        data={CONF_HOST: "10.0.0.11", CONF_PASSWORD: "legacy-password"},
    )
    source_other = MockConfigEntry(
        domain=LEGACY_DOMAIN,
        title="legacy-other",
        data={CONF_HOST: "10.0.0.44", CONF_PASSWORD: "other-password"},
    )
    target_entry.add_to_hass(hass)
    source_match.add_to_hass(hass)
    source_other.add_to_hass(hass)

    with patch.object(
        hass.config_entries,
        "async_reload",
        AsyncMock(return_value=True),
    ):
        result = await migrate_legacy_plenticore_entry(
            hass,
            target_entry_id=target_entry.entry_id,
        )

    assert result.source_entry_id == source_match.entry_id
    assert hass.config_entries.async_get_entry(source_match.entry_id) is not None
    assert hass.config_entries.async_get_entry(source_other.entry_id) is not None


async def test_legacy_migration_validation_and_no_unique_id_paths(hass):
    """Validation branches and no-unique-id entities are handled explicitly."""
    with pytest.raises(HomeAssistantError, match="Target entry 'missing' not found"):
        await adopt_legacy_entity_ids(hass, "missing", dry_run=True)
    with pytest.raises(HomeAssistantError, match="Target entry 'missing' not found"):
        await migrate_legacy_plenticore_entry(hass, "missing")
    with pytest.raises(HomeAssistantError, match="Target entry 'missing' not found"):
        await finalize_legacy_cleanup(hass, "missing")

    wrong_target = MockConfigEntry(domain=LEGACY_DOMAIN, title="wrong", data={})
    wrong_target.add_to_hass(hass)
    with pytest.raises(HomeAssistantError, match="is not a 'kostal_kore' entry"):
        await adopt_legacy_entity_ids(hass, wrong_target.entry_id, dry_run=True)
    with pytest.raises(HomeAssistantError, match="is not a 'kostal_kore' entry"):
        await migrate_legacy_plenticore_entry(hass, wrong_target.entry_id)
    with pytest.raises(HomeAssistantError, match="is not a 'kostal_kore' entry"):
        await finalize_legacy_cleanup(hass, wrong_target.entry_id)

    target_entry = MockConfigEntry(
        domain=DOMAIN,
        title="target-no-uid",
        data={CONF_HOST: "10.0.0.77", CONF_PASSWORD: "pw"},
    )
    source_entry = MockConfigEntry(
        domain=LEGACY_DOMAIN,
        title="source-no-uid",
        data={CONF_HOST: "10.0.0.77", CONF_PASSWORD: "pw"},
    )
    target_entry.add_to_hass(hass)
    source_entry.add_to_hass(hass)

    entity_registry = er.async_get(hass)
    no_uid_entity = entity_registry.async_get_or_create(
        "sensor",
        LEGACY_DOMAIN,
        None,
        config_entry=source_entry,
        suggested_object_id="legacy_without_uid",
    )

    with patch(
        "custom_components.kostal_kore.legacy_migration._select_source_entry",
        return_value=target_entry,
    ):
        with pytest.raises(HomeAssistantError, match="Source and target entry are identical"):
            await adopt_legacy_entity_ids(
                hass,
                target_entry_id=target_entry.entry_id,
                source_entry_id=source_entry.entry_id,
                dry_run=True,
            )
        with pytest.raises(HomeAssistantError, match="Source and target entry are identical"):
            await migrate_legacy_plenticore_entry(
                hass,
                target_entry_id=target_entry.entry_id,
                source_entry_id=source_entry.entry_id,
            )

    with patch.object(
        hass.config_entries,
        "async_reload",
        AsyncMock(return_value=True),
    ):
        adopt_result = await adopt_legacy_entity_ids(
            hass,
            target_entry_id=target_entry.entry_id,
            source_entry_id=source_entry.entry_id,
            dry_run=False,
        )
    assert adopt_result.migrated_entities == 1
    migrated = entity_registry.async_get(no_uid_entity.entity_id)
    assert migrated is not None
    assert migrated.config_entry_id == target_entry.entry_id

    cleanup_target = MockConfigEntry(
        domain=DOMAIN,
        title="cleanup-target",
        data={CONF_HOST: "10.0.0.88", CONF_PASSWORD: "pw"},
    )
    cleanup_source = MockConfigEntry(
        domain=LEGACY_DOMAIN,
        title="cleanup-source",
        data={CONF_HOST: "10.0.0.88", CONF_PASSWORD: "pw"},
    )
    cleanup_target.add_to_hass(hass)
    cleanup_source.add_to_hass(hass)
    cleanup_device_registry = dr.async_get(hass)
    cleanup_device_registry.async_get_or_create(
        config_entry_id=cleanup_source.entry_id,
        identifiers={(LEGACY_DOMAIN, "SER-CLEAN-NONE")},
        manufacturer="Kostal",
        name="Cleanup legacy",
    )

    with (
        patch.object(
            cleanup_device_registry,
            "async_update_device",
            return_value=None,
        ),
        patch.object(
            hass.config_entries,
            "async_reload",
            AsyncMock(return_value=True),
        ),
    ):
        cleanup_result = await finalize_legacy_cleanup(
            hass,
            cleanup_target.entry_id,
            source_entry_id=cleanup_source.entry_id,
        )
    assert cleanup_result.detached_legacy_devices == 0


def test_select_source_entry_additional_edge_cases(hass):
    """Source selection handles explicit misses, implicit single-entry fallback and ambiguity."""
    target_entry = MockConfigEntry(
        domain=DOMAIN,
        title="target-select",
        data={CONF_HOST: "10.0.0.50", CONF_PASSWORD: "pw"},
    )
    target_entry.add_to_hass(hass)

    legacy_only = MockConfigEntry(
        domain=LEGACY_DOMAIN,
        title="legacy-only",
        data={CONF_HOST: "10.0.0.99", CONF_PASSWORD: "pw"},
    )
    legacy_only.add_to_hass(hass)
    assert _select_source_entry(hass, target_entry, None).entry_id == legacy_only.entry_id

    with pytest.raises(HomeAssistantError, match="not found in domain"):
        _select_source_entry(hass, target_entry, "missing-source")

    second_legacy = MockConfigEntry(
        domain=LEGACY_DOMAIN,
        title="legacy-second",
        data={CONF_HOST: "10.0.0.60", CONF_PASSWORD: "pw"},
    )
    second_legacy.add_to_hass(hass)
    with pytest.raises(HomeAssistantError, match="Multiple legacy entries found"):
        _select_source_entry(hass, target_entry, None)

    blank_host_target = MockConfigEntry(
        domain=DOMAIN,
        title="blank-host-target",
        data={CONF_HOST: "", CONF_PASSWORD: "pw"},
    )
    blank_host_target.add_to_hass(hass)
    only_legacy = MockConfigEntry(
        domain=LEGACY_DOMAIN,
        title="legacy-blank-host",
        data={CONF_HOST: "10.0.0.70", CONF_PASSWORD: "pw"},
    )
    only_legacy.add_to_hass(hass)
    with patch.object(hass.config_entries, "async_entries", return_value=[only_legacy]):
        assert _select_source_entry(hass, blank_host_target, None).entry_id == only_legacy.entry_id


async def test_migrate_legacy_entry_errors_when_multiple_sources_ambiguous(hass):
    """Migration requires explicit source entry if multiple legacy entries remain ambiguous."""
    target_entry = MockConfigEntry(
        domain=DOMAIN,
        title="kore-target",
        data={CONF_HOST: "10.0.0.99", CONF_PASSWORD: "new-password"},
    )
    source_a = MockConfigEntry(
        domain=LEGACY_DOMAIN,
        title="legacy-a",
        data={CONF_HOST: "10.0.0.11", CONF_PASSWORD: "a"},
    )
    source_b = MockConfigEntry(
        domain=LEGACY_DOMAIN,
        title="legacy-b",
        data={CONF_HOST: "10.0.0.44", CONF_PASSWORD: "b"},
    )
    target_entry.add_to_hass(hass)
    source_a.add_to_hass(hass)
    source_b.add_to_hass(hass)

    with pytest.raises(
        HomeAssistantError,
        match="Multiple legacy entries found",
    ):
        await migrate_legacy_plenticore_entry(
            hass,
            target_entry_id=target_entry.entry_id,
        )


def test_select_source_entry_uses_single_legacy_when_target_host_has_no_match(hass):
    """A single legacy entry should still be selected when the target host is set but unmatched."""
    target_entry = MockConfigEntry(
        domain=DOMAIN,
        title="kore-target",
        data={CONF_HOST: "10.0.0.77", CONF_PASSWORD: "pw"},
    )
    legacy_only = MockConfigEntry(
        domain=LEGACY_DOMAIN,
        title="legacy-only",
        data={CONF_HOST: "10.0.0.11", CONF_PASSWORD: "pw"},
    )
    target_entry.add_to_hass(hass)
    legacy_only.add_to_hass(hass)

    assert _select_source_entry(hass, target_entry, None).entry_id == legacy_only.entry_id


async def test_discover_duplicate_pairs_and_adopt_entity_ids(hass):
    """Adopt mode should keep old IDs and remove duplicate new entities."""
    target_entry = MockConfigEntry(
        domain=DOMAIN,
        title="kore-target",
        data={CONF_HOST: "10.0.0.11", CONF_PASSWORD: "new"},
    )
    source_entry = MockConfigEntry(
        domain=LEGACY_DOMAIN,
        title="legacy-source",
        data={CONF_HOST: "10.0.0.11", CONF_PASSWORD: "old"},
    )
    target_entry.add_to_hass(hass)
    source_entry.add_to_hass(hass)

    entity_registry = er.async_get(hass)
    old_entity = entity_registry.async_get_or_create(
        "sensor",
        LEGACY_DOMAIN,
        f"{source_entry.entry_id}_devices:local_Ac_P",
        config_entry=source_entry,
        suggested_object_id="legacy_ac_power",
    )
    duplicate_target_entity = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{target_entry.entry_id}_devices:local_Ac_P",
        config_entry=target_entry,
        suggested_object_id="kore_ac_power",
    )

    device_registry = dr.async_get(hass)
    old_device = device_registry.async_get_or_create(
        config_entry_id=source_entry.entry_id,
        identifiers={(LEGACY_DOMAIN, "SER-ADOPT-1")},
        manufacturer="Kostal",
        name="Legacy inverter",
    )

    pairs = discover_legacy_duplicate_entity_pairs(
        hass,
        target_entry_id=target_entry.entry_id,
        source_entry_id=source_entry.entry_id,
    )
    assert len(pairs) == 1
    assert pairs[0].old_entity_id == old_entity.entity_id
    assert pairs[0].new_entity_id == duplicate_target_entity.entity_id

    preview = await adopt_legacy_entity_ids(
        hass,
        target_entry_id=target_entry.entry_id,
        source_entry_id=source_entry.entry_id,
        dry_run=True,
    )
    await hass.async_block_till_done()
    assert preview.dry_run is True
    assert preview.migrated_entities == 1
    assert preview.migrated_devices == 1
    assert preview.removed_target_duplicates == 1
    assert entity_registry.async_get(duplicate_target_entity.entity_id) is not None

    with patch.object(
        hass.config_entries,
        "async_reload",
        AsyncMock(return_value=True),
    ) as mock_reload:
        applied = await adopt_legacy_entity_ids(
            hass,
            target_entry_id=target_entry.entry_id,
            source_entry_id=source_entry.entry_id,
            dry_run=False,
        )
    await hass.async_block_till_done()
    assert applied.dry_run is False
    assert applied.migrated_entities == 1
    assert applied.removed_target_duplicates == 1
    mock_reload.assert_awaited_once_with(target_entry.entry_id)

    migrated = entity_registry.async_get(old_entity.entity_id)
    assert migrated is not None
    assert migrated.config_entry_id == target_entry.entry_id
    assert migrated.unique_id == f"{target_entry.entry_id}_devices:local_Ac_P"
    assert entity_registry.async_get(duplicate_target_entity.entity_id) is None

    migrated_device = device_registry.async_get(old_device.id)
    assert migrated_device is not None
    assert target_entry.entry_id in migrated_device.config_entries


async def test_adopt_entity_ids_keeps_truthy_unique_id_without_duplicate_target(hass):
    """Adopt mode should still migrate entities when rewritten IDs have no duplicate target."""
    target_entry = MockConfigEntry(
        domain=DOMAIN,
        title="kore-target",
        data={CONF_HOST: "10.0.0.21", CONF_PASSWORD: "new"},
    )
    source_entry = MockConfigEntry(
        domain=LEGACY_DOMAIN,
        title="legacy-source",
        data={CONF_HOST: "10.0.0.21", CONF_PASSWORD: "old"},
    )
    target_entry.add_to_hass(hass)
    source_entry.add_to_hass(hass)

    entity_registry = er.async_get(hass)
    source_entity = entity_registry.async_get_or_create(
        "sensor",
        LEGACY_DOMAIN,
        f"{source_entry.entry_id}_devices:local_Uac",
        config_entry=source_entry,
        suggested_object_id="legacy_uac",
    )

    with patch.object(
        hass.config_entries,
        "async_reload",
        AsyncMock(return_value=True),
    ):
        result = await adopt_legacy_entity_ids(
            hass,
            target_entry_id=target_entry.entry_id,
            source_entry_id=source_entry.entry_id,
            dry_run=False,
        )

    migrated = entity_registry.async_get(source_entity.entity_id)
    assert result.removed_target_duplicates == 0
    assert migrated is not None
    assert migrated.config_entry_id == target_entry.entry_id
    assert migrated.unique_id == f"{target_entry.entry_id}_devices:local_Uac"


def test_discover_duplicate_pairs_skips_missing_unique_ids_and_same_entity_id(hass):
    """Duplicate discovery should ignore source rows without unique IDs and self-matches."""
    target_entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: "10.0.0.11"})
    source_entry = MockConfigEntry(domain=LEGACY_DOMAIN, data={CONF_HOST: "10.0.0.11"})
    target_entry.add_to_hass(hass)
    source_entry.add_to_hass(hass)

    entity_registry = MagicMock()
    source_entities = [
        SimpleNamespace(unique_id=None, entity_id="sensor.no_uid"),
        SimpleNamespace(unique_id=f"{source_entry.entry_id}_foo", entity_id="sensor.same"),
    ]
    target_entities = [
        SimpleNamespace(unique_id=f"{target_entry.entry_id}_foo", entity_id="sensor.same"),
    ]

    with (
        patch("custom_components.kostal_kore.legacy_migration.er.async_get", return_value=entity_registry),
        patch(
            "custom_components.kostal_kore.legacy_migration.er.async_entries_for_config_entry",
            side_effect=[source_entities, target_entities],
        ),
    ):
        pairs = discover_legacy_duplicate_entity_pairs(
            hass,
            target_entry_id=target_entry.entry_id,
            source_entry_id=source_entry.entry_id,
        )

    assert pairs == []


async def test_migration_and_cleanup_cover_remaining_no_unique_and_device_none_paths(hass):
    """Migration helpers should tolerate source rows without unique IDs and devices that do not update."""
    target_entry = MockConfigEntry(domain=DOMAIN, title="target", data={CONF_HOST: "10.0.0.11"})
    source_entry = MockConfigEntry(domain=LEGACY_DOMAIN, title="legacy", data={CONF_HOST: "10.0.0.11"})
    target_entry.add_to_hass(hass)
    source_entry.add_to_hass(hass)

    fake_source_entity = SimpleNamespace(unique_id=None, entity_id="sensor.legacy_no_uid")
    fake_source_device = SimpleNamespace(id="device-1")
    entity_registry = MagicMock()
    device_registry = MagicMock()
    device_registry.async_update_device.return_value = None

    with (
        patch("custom_components.kostal_kore.legacy_migration.er.async_get", return_value=entity_registry),
        patch(
            "custom_components.kostal_kore.legacy_migration.er.async_entries_for_config_entry",
            side_effect=[[fake_source_entity], [], [fake_source_entity]],
        ),
        patch("custom_components.kostal_kore.legacy_migration.dr.async_get", return_value=device_registry),
        patch(
            "custom_components.kostal_kore.legacy_migration.dr.async_entries_for_config_entry",
            side_effect=[[fake_source_device], [fake_source_device]],
        ),
        patch.object(hass.config_entries, "async_reload", AsyncMock(return_value=True)),
    ):
        migrated = await migrate_legacy_plenticore_entry(
            hass,
            target_entry_id=target_entry.entry_id,
            source_entry_id=source_entry.entry_id,
            remove_source_entry=False,
        )
        cleaned = await finalize_legacy_cleanup(
            hass,
            target_entry_id=target_entry.entry_id,
            source_entry_id=source_entry.entry_id,
        )

    entity_registry.async_update_entity.assert_called_once_with(
        "sensor.legacy_no_uid",
        config_entry_id=target_entry.entry_id,
    )
    assert migrated.migrated_entities == 1
    assert migrated.migrated_devices == 0
    entity_registry.async_remove.assert_called_once_with("sensor.legacy_no_uid")
    assert cleaned.detached_legacy_devices == 0


async def test_finalize_cleanup_counts_detached_devices_when_registry_updates(hass):
    """Cleanup should count detached legacy devices when the device registry reports success."""
    target_entry = MockConfigEntry(domain=DOMAIN, title="target", data={CONF_HOST: "10.0.0.31"})
    source_entry = MockConfigEntry(domain=LEGACY_DOMAIN, title="legacy", data={CONF_HOST: "10.0.0.31"})
    target_entry.add_to_hass(hass)
    source_entry.add_to_hass(hass)

    entity_registry = MagicMock()
    device_registry = MagicMock()
    fake_device = SimpleNamespace(id="device-detach-1")
    device_registry.async_update_device.return_value = object()

    with (
        patch("custom_components.kostal_kore.legacy_migration.er.async_get", return_value=entity_registry),
        patch(
            "custom_components.kostal_kore.legacy_migration.er.async_entries_for_config_entry",
            return_value=[],
        ),
        patch("custom_components.kostal_kore.legacy_migration.dr.async_get", return_value=device_registry),
        patch(
            "custom_components.kostal_kore.legacy_migration.dr.async_entries_for_config_entry",
            return_value=[fake_device],
        ),
        patch.object(
            hass.config_entries,
            "async_reload",
            AsyncMock(return_value=True),
        ),
    ):
        cleanup_result = await finalize_legacy_cleanup(
            hass,
            target_entry.entry_id,
            source_entry_id=source_entry.entry_id,
        )

    assert cleanup_result.detached_legacy_devices == 1
