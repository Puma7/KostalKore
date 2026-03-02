"""Tests for guarded migration service helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant

from custom_components.kostal_kore.const import (
    DOMAIN,
    SERVICE_ADOPT_LEGACY_ENTITY_IDS,
    SERVICE_COPY_LEGACY_HISTORY,
)
from custom_components.kostal_kore.legacy_migration import LEGACY_DOMAIN, LegacyAdoptResult
from custom_components.kostal_kore.migration_services import (
    async_register_migration_services,
    async_unregister_migration_services_if_unused,
)

from pytest_homeassistant_custom_component.common import MockConfigEntry


async def test_register_unregister_migration_services(hass: HomeAssistant) -> None:
    """Services are registered once and removable when no entries exist."""
    async_register_migration_services(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_ADOPT_LEGACY_ENTITY_IDS)
    assert hass.services.has_service(DOMAIN, SERVICE_COPY_LEGACY_HISTORY)

    async_unregister_migration_services_if_unused(hass)
    assert not hass.services.has_service(DOMAIN, SERVICE_ADOPT_LEGACY_ENTITY_IDS)
    assert not hass.services.has_service(DOMAIN, SERVICE_COPY_LEGACY_HISTORY)


async def test_unregister_migration_services_ignores_unloading_entry_id(
    hass: HomeAssistant,
) -> None:
    """Services should be removable even if unloading entry still exists in domain store."""
    async_register_migration_services(hass)
    hass.data.setdefault(DOMAIN, {})["entry-a"] = {"mock": True}

    async_unregister_migration_services_if_unused(hass, unloading_entry_id="entry-a")
    assert not hass.services.has_service(DOMAIN, SERVICE_ADOPT_LEGACY_ENTITY_IDS)
    assert not hass.services.has_service(DOMAIN, SERVICE_COPY_LEGACY_HISTORY)


async def test_unregister_migration_services_keeps_services_with_other_entries(
    hass: HomeAssistant,
) -> None:
    """Services must stay registered while other KORE runtime entries are active."""
    async_register_migration_services(hass)
    hass.data.setdefault(DOMAIN, {})["entry-a"] = {"mock": True}
    hass.data.setdefault(DOMAIN, {})["entry-b"] = {"mock": True}

    async_unregister_migration_services_if_unused(hass, unloading_entry_id="entry-a")
    assert hass.services.has_service(DOMAIN, SERVICE_ADOPT_LEGACY_ENTITY_IDS)
    assert hass.services.has_service(DOMAIN, SERVICE_COPY_LEGACY_HISTORY)


async def test_adopt_service_dry_run_calls_adopt(hass: HomeAssistant) -> None:
    """Dry-run adopt should execute immediately without confirmation challenge."""
    target_entry = MockConfigEntry(domain=DOMAIN, data={"host": "10.0.0.11"})
    source_entry = MockConfigEntry(domain=LEGACY_DOMAIN, data={"host": "10.0.0.11"})
    target_entry.add_to_hass(hass)
    source_entry.add_to_hass(hass)
    async_register_migration_services(hass)

    with (
        patch(
            "custom_components.kostal_kore.migration_services.adopt_legacy_entity_ids",
            AsyncMock(
                return_value=LegacyAdoptResult(
                    source_entry_id=source_entry.entry_id,
                    target_entry_id=target_entry.entry_id,
                    dry_run=True,
                    migrated_entities=3,
                    migrated_devices=1,
                    removed_target_duplicates=2,
                )
            ),
        ) as mock_adopt,
        patch(
            "custom_components.kostal_kore.migration_services._notify",
            AsyncMock(),
        ) as mock_notify,
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_ADOPT_LEGACY_ENTITY_IDS,
            {
                "target_entry_id": target_entry.entry_id,
                "source_entry_id": source_entry.entry_id,
                "dry_run": True,
            },
            blocking=True,
        )

    mock_adopt.assert_awaited_once()
    mock_notify.assert_awaited()


async def test_copy_service_apply_requires_guard_challenge(hass: HomeAssistant) -> None:
    """First non-dry-run copy call should only arm challenge and stop."""
    target_entry = MockConfigEntry(domain=DOMAIN, data={"host": "10.0.0.11"})
    source_entry = MockConfigEntry(domain=LEGACY_DOMAIN, data={"host": "10.0.0.11"})
    target_entry.add_to_hass(hass)
    source_entry.add_to_hass(hass)
    async_register_migration_services(hass)

    with (
        patch(
            "custom_components.kostal_kore.migration_services._copy_legacy_history",
            AsyncMock(),
        ) as mock_copy,
        patch(
            "custom_components.kostal_kore.migration_services._notify",
            AsyncMock(),
        ) as mock_notify,
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_COPY_LEGACY_HISTORY,
            {
                "target_entry_id": target_entry.entry_id,
                "source_entry_id": source_entry.entry_id,
                "dry_run": False,
                "include_auto_map": False,
                "entity_map": [
                    {
                        "old_entity_id": "sensor.kostal_old_grid_power",
                        "new_entity_id": "sensor.kostal_new_grid_power",
                    }
                ],
            },
            blocking=True,
        )

    mock_copy.assert_not_awaited()
    mock_notify.assert_awaited()
