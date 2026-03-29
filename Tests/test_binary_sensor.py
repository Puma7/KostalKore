"""Tests for binary_sensor platform setup."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.kostal_kore import binary_sensor as binary_sensor_platform
from custom_components.kostal_kore.const import CONF_MODBUS_ENABLED, DOMAIN

from pytest_homeassistant_custom_component.common import MockConfigEntry


def _make_entry(modbus_enabled: bool = True) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="kore",
        data={"host": "10.0.0.10", "password": "pw"},
        options={CONF_MODBUS_ENABLED: modbus_enabled},
    )
    entry.runtime_data = SimpleNamespace(
        device_info=DeviceInfo(identifiers={(DOMAIN, "SERIAL-BINARY")})
    )
    return entry


async def test_async_setup_entry_skips_when_modbus_disabled(hass) -> None:
    """Platform should do nothing when Modbus is disabled."""
    entry = _make_entry(modbus_enabled=False)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "health_monitor": object(),
        "fire_safety": object(),
    }

    added: list[object] = []
    await binary_sensor_platform.async_setup_entry(
        hass,
        entry,
        lambda entities: added.extend(entities),
    )

    assert added == []


async def test_async_setup_entry_adds_fire_safety_without_health_monitor(hass) -> None:
    """Fire-safety binary sensors should still load without a health monitor."""
    entry = _make_entry()
    fire_entities = [object()]
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "fire_safety": object(),
    }

    added: list[object] = []
    with patch(
        "custom_components.kostal_kore.fire_safety_entities.create_fire_safety_binary_sensors",
        return_value=fire_entities,
    ) as create_fire:
        await binary_sensor_platform.async_setup_entry(
            hass,
            entry,
            lambda entities: added.extend(entities),
        )

    create_fire.assert_called_once()
    assert added == fire_entities


async def test_async_setup_entry_adds_health_and_fire_safety_entities(hass) -> None:
    """Platform should aggregate both health and fire-safety entities."""
    entry = _make_entry()
    health_entities = [object()]
    fire_entities = [object(), object()]
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "health_monitor": object(),
        "fire_safety": object(),
    }

    added: list[object] = []
    with (
        patch(
            "custom_components.kostal_kore.health_binary_sensor.create_health_binary_sensors",
            return_value=health_entities,
        ) as create_health,
        patch(
            "custom_components.kostal_kore.fire_safety_entities.create_fire_safety_binary_sensors",
            return_value=fire_entities,
        ) as create_fire,
    ):
        await binary_sensor_platform.async_setup_entry(
            hass,
            entry,
            lambda entities: added.extend(entities),
        )

    create_health.assert_called_once()
    create_fire.assert_called_once()
    assert added == health_entities + fire_entities


async def test_async_setup_entry_skips_when_no_monitors_exist(hass) -> None:
    """Platform should not add entities when no monitor backends are present."""
    entry = _make_entry()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {}

    added: list[object] = []
    await binary_sensor_platform.async_setup_entry(
        hass,
        entry,
        lambda entities: added.extend(entities),
    )

    assert added == []
