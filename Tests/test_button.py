"""Tests for button platform setup."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.kostal_kore import button as button_platform
from custom_components.kostal_kore.const import (
    CONF_MODBUS_ENABLED,
    DATA_KEY_LEGACY_CLEANUP_CODE_INPUT,
    DATA_KEY_LEGACY_CLEANUP_GUARD,
    DOMAIN,
)

from pytest_homeassistant_custom_component.common import MockConfigEntry


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
