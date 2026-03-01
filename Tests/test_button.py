"""Tests for button platform setup."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.kostal_kore import button as button_platform
from custom_components.kostal_kore.const import CONF_MODBUS_ENABLED, DOMAIN

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

    assert len(added) == 2
    assert added[0].unique_id.endswith("_import_legacy_plenticore_data")
    assert added[1].unique_id.endswith("_finalize_legacy_cleanup")


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

    assert len(added) == 4
    assert any(
        getattr(entity, "unique_id", "").endswith("_import_legacy_plenticore_data")
        for entity in added
    )
    assert any(
        getattr(entity, "unique_id", "").endswith("_finalize_legacy_cleanup")
        for entity in added
    )
