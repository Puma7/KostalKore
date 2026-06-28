"""Tests for button platform setup."""

from __future__ import annotations

import asyncio  # noqa: F401
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.helpers.device_registry import DeviceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kostal_kore import button as button_platform
from custom_components.kostal_kore.const import (
    CONF_MODBUS_ENABLED,
    DATA_KEY_LEGACY_CLEANUP_CODE_INPUT,
    DATA_KEY_LEGACY_CLEANUP_GUARD,
    DOMAIN,
)


async def test_setup_entry_adds_legacy_migration_button_without_modbus(hass):
    """Migration button is always added, even when Modbus is disabled."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="kore",
        data={"host": "10.0.0.11", "password": "pw"},
        options={CONF_MODBUS_ENABLED: False},
    )
    entry.runtime_data = SimpleNamespace(
        device_info=DeviceInfo(identifiers={(DOMAIN, "SERIAL-1")})
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {}

    added = []

    def _add_entities(entities):
        added.extend(entities)

    await button_platform.async_setup_entry(hass, entry, _add_entities)

    assert len(added) == 3
    assert added[0].unique_id.endswith("_import_legacy_plenticore_data")
    assert added[1].unique_id.endswith("_finalize_legacy_cleanup")
    assert added[2].unique_id.endswith("_system_health_check")


async def test_setup_entry_adds_modbus_buttons_when_available(hass):
    """When Modbus is enabled, migration + Modbus buttons are added together."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="kore",
        data={"host": "10.0.0.11", "password": "pw"},
        options={CONF_MODBUS_ENABLED: True},
    )
    entry.runtime_data = SimpleNamespace(
        device_info=DeviceInfo(identifiers={(DOMAIN, "SERIAL-2")})
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "modbus_coordinator": MagicMock(),
    }

    added = []

    def _add_entities(entities):
        added.extend(entities)

    with patch(
        "custom_components.kostal_kore.modbus_button.create_modbus_buttons",
        return_value=[MagicMock(name="modbus_button_1"), MagicMock(name="modbus_button_2")],
    ):
        await button_platform.async_setup_entry(hass, entry, _add_entities)

    assert len(added) == 5
    assert any(
        getattr(entity, "unique_id", "").endswith("_import_legacy_plenticore_data")
        for entity in added
    )
    assert any(
        getattr(entity, "unique_id", "").endswith("_finalize_legacy_cleanup")
        for entity in added
    )


async def test_finalize_cleanup_requires_code_and_double_confirmation(hass):
    """Finalize cleanup must require code entry + second final confirmation."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="kore",
        data={"host": "10.0.0.11", "password": "pw"},
        options={CONF_MODBUS_ENABLED: False},
    )
    entry.runtime_data = SimpleNamespace(
        device_info=DeviceInfo(identifiers={(DOMAIN, "SERIAL-3")})
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {}

    entity = button_platform.LegacyCleanupButton(entry)
    entity.hass = hass
    entity.entity_id = "button.finalize_legacy_cleanup_test"

    with patch(
        "homeassistant.core.ServiceRegistry.async_call",
        AsyncMock(return_value=None),
    ), patch(
        "custom_components.kostal_kore.button.LegacyCleanupButton._show_confirmation_step1",
        AsyncMock(return_value=None),
    ), patch(
        "custom_components.kostal_kore.button.LegacyCleanupButton._show_confirmation_step2",
        AsyncMock(return_value=None),
    ), patch(
        "custom_components.kostal_kore.button.LegacyCleanupButton._show_confirmation_mismatch",
        AsyncMock(return_value=None),
    ), patch(
        "custom_components.kostal_kore.button.LegacyCleanupButton._show_confirmation_expired",
        AsyncMock(return_value=None),
    ), patch(
        "custom_components.kostal_kore.button.finalize_legacy_cleanup",
        AsyncMock(
            return_value=SimpleNamespace(
                source_entry_id="legacy-1",
                removed_legacy_entities=10,
                detached_legacy_devices=1,
                removed_source_entry=True,
            )
        ),
    ) as mock_cleanup:
        # 1st press: creates challenge, must not execute cleanup.
        await entity.async_press()
        store = hass.data[DOMAIN][entry.entry_id]
        guard = store[DATA_KEY_LEGACY_CLEANUP_GUARD]
        assert guard["phase"] == 1
        assert isinstance(guard["code"], str) and guard["code"]
        assert mock_cleanup.await_count == 0

        # Paste code into text box value, then 2nd press arms final step.
        store[DATA_KEY_LEGACY_CLEANUP_CODE_INPUT] = guard["code"]
        await entity.async_press()
        guard = store[DATA_KEY_LEGACY_CLEANUP_GUARD]
        assert guard["phase"] == 2
        assert mock_cleanup.await_count == 0

        # 3rd press executes destructive cleanup.
        await entity.async_press()
        guard = store[DATA_KEY_LEGACY_CLEANUP_GUARD]
        assert guard["phase"] == 0
        assert store[DATA_KEY_LEGACY_CLEANUP_CODE_INPUT] == ""
        assert mock_cleanup.await_count == 1


async def test_legacy_migration_button_arms_then_executes(hass):
    """Legacy import requires a second press and reports success details."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="kore",
        data={"host": "10.0.0.11", "password": "pw"},
        options={CONF_MODBUS_ENABLED: False},
    )
    entry.runtime_data = SimpleNamespace(
        device_info=DeviceInfo(identifiers={(DOMAIN, "SERIAL-IMPORT")})
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {}

    entity = button_platform.LegacyMigrationButton(entry)
    entity.hass = hass
    entity.entity_id = "button.import_legacy_plenticore_data_test"

    result = SimpleNamespace(
        source_entry_id="legacy-source",
        migrated_entities=7,
        migrated_devices=2,
        removed_target_duplicates=1,
        removed_source_entry=False,
    )

    with (
        patch.object(entity, "async_write_ha_state"),
        patch("homeassistant.core.ServiceRegistry.async_call", AsyncMock(return_value=None)) as mock_call,
        patch(
            "custom_components.kostal_kore.button.migrate_legacy_plenticore_entry",
            AsyncMock(return_value=result),
        ) as mock_migrate,
    ):
        await entity.async_press()
        store = hass.data[DOMAIN][entry.entry_id]
        assert store["legacy_import_guard"]["armed_at"] > 0
        assert entity.extra_state_attributes["last_status"] == "awaiting_confirm"
        assert mock_migrate.await_count == 0

        await entity.async_press()
        assert "legacy_import_guard" not in store
        assert entity.extra_state_attributes["last_status"] == "ok"
        assert entity.extra_state_attributes["migrated_entities"] == 7
        assert mock_migrate.await_count == 1
        assert mock_call.await_count == 2


async def test_legacy_migration_button_reports_failure(hass):
    """Legacy import stores error details when execution fails."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="kore",
        data={"host": "10.0.0.11", "password": "pw"},
        options={CONF_MODBUS_ENABLED: False},
    )
    entry.runtime_data = SimpleNamespace(
        device_info=DeviceInfo(identifiers={(DOMAIN, "SERIAL-IMPORT-ERR")})
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "legacy_import_guard": {"armed_at": time.monotonic()},
    }

    entity = button_platform.LegacyMigrationButton(entry)
    entity.hass = hass
    entity.entity_id = "button.import_legacy_plenticore_data_error_test"

    with (
        patch.object(entity, "async_write_ha_state"),
        patch("homeassistant.core.ServiceRegistry.async_call", AsyncMock(return_value=None)),
        patch(
            "custom_components.kostal_kore.button.migrate_legacy_plenticore_entry",
            AsyncMock(side_effect=RuntimeError("migration exploded")),
        ),
    ):
        await entity.async_press()

    assert entity.extra_state_attributes["last_status"] == "error"
    assert "migration exploded" in entity.extra_state_attributes["error"]


async def test_finalize_cleanup_handles_edge_paths_and_reentrancy(hass):
    """Cleanup button covers notifications, expiry, mismatch, errors and reentrancy."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="kore",
        data={"host": "10.0.0.11", "password": "pw"},
        options={CONF_MODBUS_ENABLED: False},
    )
    entry.runtime_data = SimpleNamespace(
        device_info=DeviceInfo(identifiers={(DOMAIN, "SERIAL-CLEANUP-EDGE")})
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {}

    entity = button_platform.LegacyCleanupButton(entry)
    entity.hass = hass
    entity.entity_id = "button.finalize_legacy_cleanup_edge_test"

    with patch("homeassistant.core.ServiceRegistry.async_call", AsyncMock(return_value=None)) as mock_call:
        await entity._show_confirmation_step1("ABC123")
        await entity._show_confirmation_step2()
        await entity._show_confirmation_mismatch()
        await entity._show_confirmation_expired()
        assert mock_call.await_count == 4

    with patch.object(entity, "_handle_press", AsyncMock()) as mock_handle:
        await entity._press_lock.acquire()
        try:
            await entity.async_press()
        finally:
            entity._press_lock.release()
        mock_handle.assert_not_awaited()

    store = hass.data[DOMAIN][entry.entry_id]

    with (
        patch.object(entity, "async_write_ha_state"),
        patch.object(entity, "_show_confirmation_expired", AsyncMock(return_value=None)) as mock_expired,
    ):
        store[DATA_KEY_LEGACY_CLEANUP_GUARD] = {
            "phase": 2,
            "code": "ABC123",
            "expires_at": time.monotonic() - 1,
        }
        await entity.async_press()
        assert entity.extra_state_attributes["last_status"] == "expired"
        assert store[DATA_KEY_LEGACY_CLEANUP_GUARD]["phase"] == 0
        mock_expired.assert_awaited_once()

    with (
        patch.object(entity, "async_write_ha_state"),
        patch.object(entity, "_show_confirmation_expired", AsyncMock(return_value=None)) as mock_expired,
    ):
        store[DATA_KEY_LEGACY_CLEANUP_GUARD] = {
            "phase": 1,
            "code": "ABC123",
            "expires_at": time.monotonic() - 1,
        }
        await entity.async_press()
        assert entity.extra_state_attributes["last_status"] == "expired"
        mock_expired.assert_awaited_once()

    with (
        patch.object(entity, "async_write_ha_state"),
        patch.object(entity, "_show_confirmation_mismatch", AsyncMock(return_value=None)) as mock_mismatch,
    ):
        store[DATA_KEY_LEGACY_CLEANUP_GUARD] = {
            "phase": 1,
            "code": "RIGHT1",
            "expires_at": time.monotonic() + 60,
        }
        store[DATA_KEY_LEGACY_CLEANUP_CODE_INPUT] = "WRONG1"
        await entity.async_press()
        assert entity.extra_state_attributes["last_status"] == "awaiting_code"
        mock_mismatch.assert_awaited_once()

    with (
        patch.object(entity, "async_write_ha_state"),
        patch("homeassistant.core.ServiceRegistry.async_call", AsyncMock(return_value=None)),
        patch(
            "custom_components.kostal_kore.button.finalize_legacy_cleanup",
            AsyncMock(side_effect=RuntimeError("cleanup exploded")),
        ),
    ):
        store[DATA_KEY_LEGACY_CLEANUP_GUARD] = {
            "phase": 2,
            "code": "RIGHT1",
            "expires_at": time.monotonic() + 60,
        }
        await entity.async_press()

    assert entity.extra_state_attributes["last_status"] == "error"
    assert "cleanup exploded" in entity.extra_state_attributes["error"]


async def test_setup_entry_modbus_enabled_without_coordinator_still_adds_base_buttons(hass):
    """Modbus-enabled setup should still work when no coordinator runtime exists."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="kore",
        data={"host": "10.0.0.11", "password": "pw"},
        options={CONF_MODBUS_ENABLED: True},
    )
    entry.runtime_data = SimpleNamespace(
        device_info=DeviceInfo(identifiers={(DOMAIN, "SERIAL-NO-COORD")})
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {}

    added = []
    await button_platform.async_setup_entry(hass, entry, added.extend)

    assert len(added) == 3
