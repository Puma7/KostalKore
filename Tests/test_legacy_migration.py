"""Tests for one-click legacy migration from kostal_plenticore."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

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
        )
    await hass.async_block_till_done()

    assert result.source_entry_id == source_entry.entry_id
    assert result.target_entry_id == target_entry.entry_id
    assert result.migrated_entities >= 1
    assert result.migrated_devices >= 1
    assert result.removed_target_duplicates == 1
    assert result.removed_source_entry is True
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
    assert source_entry.entry_id not in migrated_device.config_entries
    assert hass.config_entries.async_get_entry(source_entry.entry_id) is None


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
    assert hass.config_entries.async_get_entry(source_match.entry_id) is None
    assert hass.config_entries.async_get_entry(source_other.entry_id) is not None


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
