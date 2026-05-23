"""Tests for entity registry helpers (reload-loop prevention)."""

from __future__ import annotations

import logging
from traceback import FrameSummary
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_registry import RegistryEntryDisabler
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kostal_kore import entity_registry_helpers as erh
from dataclasses import replace

from custom_components.kostal_kore.const_ids import ModuleId, SettingId
from custom_components.kostal_kore.number import FORCE_CREATE_KEYS, NUMBER_SETTINGS_DATA
from custom_components.kostal_kore.select import SELECT_SETTINGS_DATA


def _mock_entry() -> MockConfigEntry:
    return MockConfigEntry(domain="kostal_kore", title="Test Inverter")


def test_update_disabled_by_if_changed_missing_entity() -> None:
    registry = MagicMock()
    registry.async_get.return_value = None
    assert (
        erh.update_disabled_by_if_changed(registry, "number.missing", disabled_by=None)
        is False
    )
    registry.async_update_entity.assert_not_called()


def test_update_disabled_by_if_changed_unchanged() -> None:
    registry = MagicMock()
    entry = MagicMock(disabled_by=RegistryEntryDisabler.INTEGRATION)
    registry.async_get.return_value = entry
    assert (
        erh.update_disabled_by_if_changed(
            registry,
            "number.test_entity",
            disabled_by=RegistryEntryDisabler.INTEGRATION,
        )
        is False
    )
    registry.async_update_entity.assert_not_called()


def test_resolve_expected_registry_entry_prefers_canonical() -> None:
    canonical = MagicMock(entity_id="number.canonical")
    legacy = MagicMock(entity_id="number.legacy")
    by_uid = {
        "entry_mod_Battery:MinSoc": legacy,
        "entry_mod_Battery:MinSocRel": canonical,
    }
    resolved = erh._resolve_expected_registry_entry(
        by_uid,
        "entry_mod_Battery:MinSocRel",
        {"entry_mod_Battery:MinSoc"},
    )
    assert resolved is canonical


def test_resolve_expected_registry_entry_sorted_fallback() -> None:
    first = MagicMock(entity_id="number.first")
    by_uid = {"uid_a": first}
    resolved = erh._resolve_expected_registry_entry(
        by_uid,
        "uid_missing",
        {"uid_b", "uid_a"},
    )
    assert resolved is first


def test_resolve_expected_registry_entry_none() -> None:
    assert (
        erh._resolve_expected_registry_entry({}, "missing", {"also_missing"})
        is None
    )


def test_update_disabled_by_if_changed_updates() -> None:
    registry = MagicMock()
    entry = MagicMock(disabled_by=RegistryEntryDisabler.INTEGRATION)
    registry.async_get.return_value = entry
    assert (
        erh.update_disabled_by_if_changed(
            registry, "number.test_entity", disabled_by=None
        )
        is True
    )
    registry.async_update_entity.assert_called_once_with(
        "number.test_entity", disabled_by=None
    )


@pytest.mark.asyncio
async def test_migrate_number_registry_expected_entry_and_duplicate(
    hass: HomeAssistant,
) -> None:
    entry = _mock_entry()
    entry.add_to_hass(hass)
    entity_registry = er.async_get(hass)
    force_desc = next(d for d in NUMBER_SETTINGS_DATA if d.data_id in FORCE_CREATE_KEYS)
    canonical_uid = f"{entry.entry_id}_{force_desc.module_id}_{force_desc.data_id}"

    expected = entity_registry.async_get_or_create(
        "number",
        "kostal_kore",
        canonical_uid,
        config_entry=entry,
        original_name=f"scb {force_desc.name}",
        disabled_by=RegistryEntryDisabler.INTEGRATION,
    )
    entity_registry.async_get_or_create(
        "number",
        "kostal_kore",
        f"{canonical_uid}_dup",
        config_entry=entry,
        original_name=f"scb {force_desc.name}",
    )

    with patch.object(erh, "update_disabled_by_if_changed", wraps=erh.update_disabled_by_if_changed) as upd:
        erh.migrate_number_registry_before_add(hass, entry)
        assert upd.call_count >= 1

    assert entity_registry.async_get(expected.entity_id) is not None


@pytest.mark.asyncio
async def test_migrate_number_registry_unique_id_migration(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    entry = _mock_entry()
    entry.add_to_hass(hass)
    entity_registry = er.async_get(hass)
    force_desc = next(d for d in NUMBER_SETTINGS_DATA if d.data_id in FORCE_CREATE_KEYS)
    canonical_uid = f"{entry.entry_id}_{force_desc.module_id}_{force_desc.data_id}"

    legacy_entity = entity_registry.async_get_or_create(
        "number",
        "kostal_kore",
        f"{canonical_uid}_legacy_only",
        config_entry=entry,
        original_name=f"migrate {force_desc.name}",
        disabled_by=RegistryEntryDisabler.INTEGRATION,
    )

    with caplog.at_level(logging.INFO):
        erh.migrate_number_registry_before_add(hass, entry)
    assert "Migrating number unique_id" in caplog.text

    migrated = entity_registry.async_get(legacy_entity.entity_id)
    assert migrated is not None
    assert migrated.unique_id == canonical_uid
    assert migrated.disabled_by is None


@pytest.mark.asyncio
async def test_migrate_number_registry_exception(hass: HomeAssistant) -> None:
    entry = _mock_entry()
    with patch(
        "homeassistant.helpers.entity_registry.async_get",
        side_effect=RuntimeError("boom"),
    ):
        erh.migrate_number_registry_before_add(hass, entry)


@pytest.mark.asyncio
async def test_ensure_critical_numbers_prefers_canonical_over_legacy_alias(
    hass: HomeAssistant,
) -> None:
    """Legacy MinSoc sorts before MinSocRel; canonical must still win."""
    entry = _mock_entry()
    entry.add_to_hass(hass)
    entity_registry = er.async_get(hass)
    soc_rel_desc = next(
        d for d in NUMBER_SETTINGS_DATA if d.data_id == SettingId.BATTERY_MIN_SOC_REL
    )
    canonical_uid = (
        f"{entry.entry_id}_{ModuleId.DEVICES_LOCAL}_{SettingId.BATTERY_MIN_SOC_REL}"
    )
    legacy_uid = (
        f"{entry.entry_id}_{ModuleId.DEVICES_LOCAL}_{SettingId.BATTERY_MIN_SOC}"
    )
    assert legacy_uid < canonical_uid

    legacy_entity = entity_registry.async_get_or_create(
        "number",
        "kostal_kore",
        legacy_uid,
        config_entry=entry,
        original_name=f"scb {soc_rel_desc.name}",
        disabled_by=None,
    )
    canonical_entity = entity_registry.async_get_or_create(
        "number",
        "kostal_kore",
        canonical_uid,
        config_entry=entry,
        original_name=f"scb {soc_rel_desc.name}",
        disabled_by=None,
    )

    erh.ensure_critical_numbers_enabled(hass, entry)

    legacy_entry = entity_registry.async_get(legacy_entity.entity_id)
    canonical_entry = entity_registry.async_get(canonical_entity.entity_id)
    assert legacy_entry is not None
    assert canonical_entry is not None
    assert legacy_entry.disabled_by == RegistryEntryDisabler.INTEGRATION
    assert canonical_entry.disabled_by is None


@pytest.mark.asyncio
async def test_ensure_critical_numbers_disables_duplicates(hass: HomeAssistant) -> None:
    entry = _mock_entry()
    entry.add_to_hass(hass)
    entity_registry = er.async_get(hass)
    force_desc = next(d for d in NUMBER_SETTINGS_DATA if d.data_id in FORCE_CREATE_KEYS)
    canonical_uid = f"{entry.entry_id}_{force_desc.module_id}_{force_desc.data_id}"

    primary = entity_registry.async_get_or_create(
        "number",
        "kostal_kore",
        canonical_uid,
        config_entry=entry,
        original_name=f"scb {force_desc.name}",
    )
    duplicate = entity_registry.async_get_or_create(
        "number",
        "kostal_kore",
        f"{canonical_uid}_name_dup",
        config_entry=entry,
        original_name=f"scb {force_desc.name}",
        disabled_by=None,
    )

    erh.ensure_critical_numbers_enabled(hass, entry)

    dup_entry = entity_registry.async_get(duplicate.entity_id)
    assert dup_entry is not None
    assert dup_entry.disabled_by == RegistryEntryDisabler.INTEGRATION
    primary_entry = entity_registry.async_get(primary.entity_id)
    assert primary_entry is not None
    assert primary_entry.disabled_by is None


@pytest.mark.asyncio
async def test_ensure_critical_numbers_exception(hass: HomeAssistant) -> None:
    entry = _mock_entry()
    with patch(
        "homeassistant.helpers.entity_registry.async_get",
        side_effect=RuntimeError("fail"),
    ):
        erh.ensure_critical_numbers_enabled(hass, entry)


@pytest.mark.asyncio
async def test_migrate_select_registry_both_entries(hass: HomeAssistant) -> None:
    entry = _mock_entry()
    entry.add_to_hass(hass)
    entity_registry = er.async_get(hass)
    description = SELECT_SETTINGS_DATA[0]
    old_unique_id = f"{entry.entry_id}_{description.module_id}"
    new_unique_id = f"{entry.entry_id}_{description.module_id}_{description.key}"

    old_entry = entity_registry.async_get_or_create(
        "select",
        "kostal_kore",
        old_unique_id,
        config_entry=entry,
        disabled_by=RegistryEntryDisabler.INTEGRATION,
    )
    new_entry = entity_registry.async_get_or_create(
        "select",
        "kostal_kore",
        new_unique_id,
        config_entry=entry,
    )

    erh.migrate_select_registry_after_add(hass, entry)

    assert entity_registry.async_get(new_entry.entity_id) is None
    migrated = entity_registry.async_get(old_entry.entity_id)
    assert migrated is not None
    assert migrated.unique_id == new_unique_id
    assert migrated.disabled_by is None


@pytest.mark.asyncio
async def test_migrate_select_registry_old_only(hass: HomeAssistant) -> None:
    entry = _mock_entry()
    entry.add_to_hass(hass)
    entity_registry = er.async_get(hass)
    description = SELECT_SETTINGS_DATA[0]
    old_unique_id = f"{entry.entry_id}_{description.module_id}"
    new_unique_id = f"{entry.entry_id}_{description.module_id}_{description.key}"

    only_old = entity_registry.async_get_or_create(
        "select",
        "kostal_kore",
        old_unique_id,
        config_entry=entry,
        disabled_by=RegistryEntryDisabler.INTEGRATION,
    )
    erh.migrate_select_registry_after_add(hass, entry)
    migrated = entity_registry.async_get(only_old.entity_id)
    assert migrated is not None
    assert migrated.unique_id == new_unique_id


@pytest.mark.asyncio
async def test_migrate_select_update_failure(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    entry = _mock_entry()
    entry.add_to_hass(hass)
    entity_registry = er.async_get(hass)
    description = SELECT_SETTINGS_DATA[0]
    old_unique_id = f"{entry.entry_id}_{description.module_id}"
    new_unique_id = f"{entry.entry_id}_{description.module_id}_{description.key}"

    entity_registry.async_get_or_create(
        "select", "kostal_kore", old_unique_id, config_entry=entry
    )
    entity_registry.async_get_or_create(
        "select", "kostal_kore", new_unique_id, config_entry=entry
    )

    def flaky_update(entity_id: str, **kwargs: object) -> None:
        if kwargs.get("new_unique_id"):
            raise ValueError("cannot migrate")
        er.EntityRegistry.async_update_entity(entity_registry, entity_id, **kwargs)

    with (
        patch.object(entity_registry, "async_update_entity", side_effect=flaky_update),
        caplog.at_level(logging.WARNING),
    ):
        erh.migrate_select_registry_after_add(hass, entry)
    assert "Select migration: failed to update" in caplog.text


@pytest.mark.asyncio
async def test_migrate_select_registry_exception(hass: HomeAssistant) -> None:
    entry = _mock_entry()
    with patch(
        "homeassistant.helpers.entity_registry.async_get",
        side_effect=RuntimeError("err"),
    ):
        erh.migrate_select_registry_after_add(hass, entry)


def test_run_post_setup_entity_registry_maintenance(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    from custom_components.kostal_kore.const import DATA_KEY_FORCED_NUMBER_UNIQUE_IDS, DOMAIN

    entry = _mock_entry()
    entry.add_to_hass(hass)
    forced = {"Battery:MinSoc": {f"{entry.entry_id}_devices:local_Battery:MinSoc"}}
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        DATA_KEY_FORCED_NUMBER_UNIQUE_IDS: forced,
    }
    with (
        patch.object(erh, "ensure_critical_numbers_enabled") as ensure_mock,
        caplog.at_level(logging.DEBUG),
    ):
        erh.run_post_setup_entity_registry_maintenance(hass, entry)
    ensure_mock.assert_called_once_with(hass, entry, forced_unique_ids_by_data_id=forced)
    assert DATA_KEY_FORCED_NUMBER_UNIQUE_IDS not in hass.data[DOMAIN][entry.entry_id]
    assert "Post-setup entity registry maintenance completed" in caplog.text


def test_run_post_setup_without_entry_data(hass: HomeAssistant) -> None:
    entry = _mock_entry()
    with patch.object(erh, "ensure_critical_numbers_enabled") as ensure_mock:
        erh.run_post_setup_entity_registry_maintenance(hass, entry)
    ensure_mock.assert_called_once_with(hass, entry, forced_unique_ids_by_data_id=None)


def test_run_post_setup_ignores_invalid_stored_forced_map(hass: HomeAssistant) -> None:
    from custom_components.kostal_kore.const import DATA_KEY_FORCED_NUMBER_UNIQUE_IDS, DOMAIN

    entry = _mock_entry()
    entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        DATA_KEY_FORCED_NUMBER_UNIQUE_IDS: "not-a-dict",
    }
    with patch.object(erh, "ensure_critical_numbers_enabled") as ensure_mock:
        erh.run_post_setup_entity_registry_maintenance(hass, entry)
    ensure_mock.assert_called_once_with(hass, entry, forced_unique_ids_by_data_id=None)


@pytest.mark.asyncio
async def test_migrate_number_skips_invalid_name_types(hass: HomeAssistant) -> None:
    """Branches where original_name/name are not strings are skipped."""
    entry = _mock_entry()
    registry = MagicMock()
    bad_entry = MagicMock(
        domain="number",
        unique_id="orphan",
        original_name=123,
        entity_id="number.bad",
    )
    force_desc = next(d for d in NUMBER_SETTINGS_DATA if d.data_id in FORCE_CREATE_KEYS)
    registry.async_entries_for_config_entry.return_value = [bad_entry]
    with patch("homeassistant.helpers.entity_registry.async_get", return_value=registry):
        with patch(
            "custom_components.kostal_kore.number.NUMBER_SETTINGS_DATA",
            [force_desc],
        ):
            erh.migrate_number_registry_before_add(hass, entry)
    registry.async_update_entity.assert_not_called()


@pytest.mark.asyncio
async def test_log_unload_caller_ha_reload_warning(
    mock_config_entry: MockConfigEntry,
) -> None:
    import importlib

    kp_init = importlib.import_module("custom_components.kostal_kore.__init__")
    from custom_components.kostal_kore.startup_trace import SetupTrace

    trace = SetupTrace(mock_config_entry.entry_id, mock_config_entry.title)
    frames = [
        FrameSummary(
            "/usr/src/homeassistant/homeassistant/config_entries.py",
            100,
            "_async_handle_reload",
        ),
        FrameSummary(__file__, 50, "test_log_unload_caller_ha_reload_warning"),
    ]
    with (
        patch(
            "custom_components.kostal_kore.__init__.traceback.extract_stack",
            return_value=frames,
        ),
        patch.object(trace, "warning") as warn_mock,
    ):
        kp_init._log_unload_caller(trace, mock_config_entry)
    warn_mock.assert_called_once()
    assert "HA Core reload" in warn_mock.call_args[0][0]


@pytest.mark.asyncio
async def test_migrate_number_duplicate_debug_and_domain_skip(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Cover non-number skip, name mismatch, and duplicate debug branches."""
    entry = _mock_entry()
    entry.add_to_hass(hass)
    entity_registry = er.async_get(hass)
    force_desc = next(d for d in NUMBER_SETTINGS_DATA if d.data_id in FORCE_CREATE_KEYS)
    canonical_uid = f"{entry.entry_id}_{force_desc.module_id}_{force_desc.data_id}"

    entity_registry.async_get_or_create(
        "sensor",
        "kostal_kore",
        "sensor_noise",
        config_entry=entry,
    )
    entity_registry.async_get_or_create(
        "number",
        "kostal_kore",
        canonical_uid,
        config_entry=entry,
        original_name=f"scb {force_desc.name}",
    )
    entity_registry.async_get_or_create(
        "number",
        "kostal_kore",
        f"{canonical_uid}_wrong_suffix",
        config_entry=entry,
        original_name="wrong label",
    )
    entity_registry.async_get_or_create(
        "number",
        "kostal_kore",
        f"{canonical_uid}_dup2",
        config_entry=entry,
        original_name=f"scb {force_desc.name}",
    )

    with caplog.at_level(logging.DEBUG):
        erh.migrate_number_registry_before_add(hass, entry)
    assert "Found duplicate number entity" in caplog.text


@pytest.mark.asyncio
async def test_migrate_number_skips_non_string_description_name(
    hass: HomeAssistant,
) -> None:
    entry = _mock_entry()
    entry.add_to_hass(hass)
    entity_registry = er.async_get(hass)
    force_desc = next(d for d in NUMBER_SETTINGS_DATA if d.data_id in FORCE_CREATE_KEYS)
    bad_desc = replace(force_desc, name=123)  # type: ignore[arg-type]
    entity_registry.async_get_or_create(
        "number",
        "kostal_kore",
        "bad_name_desc_probe",
        config_entry=entry,
        original_name="any Battery min SoC",
    )
    with patch(
        "custom_components.kostal_kore.number.NUMBER_SETTINGS_DATA",
        [bad_desc],
    ):
        erh.migrate_number_registry_before_add(hass, entry)


@pytest.mark.asyncio
async def test_migrate_select_no_matching_entries(hass: HomeAssistant) -> None:
    entry = _mock_entry()
    entry.add_to_hass(hass)
    erh.migrate_select_registry_after_add(hass, entry)


@pytest.mark.asyncio
async def test_ensure_critical_skips_non_number_and_bad_names(
    hass: HomeAssistant,
) -> None:
    entry = _mock_entry()
    entry.add_to_hass(hass)
    entity_registry = er.async_get(hass)
    entity_registry.async_get_or_create(
        "sensor", "kostal_kore", "skip_sensor", config_entry=entry
    )
    bad = entity_registry.async_get_or_create(
        "number",
        "kostal_kore",
        "bad_name_number",
        config_entry=entry,
        original_name=999,  # type: ignore[arg-type]
    )
    assert bad is not None
    erh.ensure_critical_numbers_enabled(hass, entry)
