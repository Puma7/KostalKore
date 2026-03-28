"""Tests for text platform setup and behavior."""

from __future__ import annotations

from types import SimpleNamespace

from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.kostal_kore import text as text_platform
from custom_components.kostal_kore.const import (
    DATA_KEY_LEGACY_CLEANUP_CODE_INPUT,
    DOMAIN,
)

from pytest_homeassistant_custom_component.common import MockConfigEntry


async def test_setup_entry_adds_cleanup_confirmation_text_entity(hass):
    """Text platform should expose code textbox for guarded cleanup."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="kore",
        data={"host": "10.0.0.11", "password": "pw"},
    )
    entry.runtime_data = SimpleNamespace(
        device_info=DeviceInfo(identifiers={(DOMAIN, "SERIAL-TEXT")})
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {}

    added = []
    await text_platform.async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    assert len(added) == 1
    assert added[0].unique_id.endswith("_legacy_cleanup_confirmation_code")


async def test_cleanup_confirmation_text_normalizes_and_persists_value(hass):
    """Entered confirmation code should be stored uppercase in entry store."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="kore",
        data={"host": "10.0.0.11", "password": "pw"},
    )
    entry.runtime_data = SimpleNamespace(
        device_info=DeviceInfo(identifiers={(DOMAIN, "SERIAL-TEXT-2")})
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {}

    entity = text_platform.LegacyCleanupConfirmationCodeText(entry)
    entity.hass = hass
    entity.entity_id = "text.legacy_cleanup_confirmation_code_test"

    await entity.async_set_value("  ab12cd  ")

    assert (
        hass.data[DOMAIN][entry.entry_id][DATA_KEY_LEGACY_CLEANUP_CODE_INPUT]
        == "AB12CD"
    )
    assert entity.native_value == "******"


async def test_cleanup_confirmation_text_returns_empty_without_hass() -> None:
    """native_value should gracefully return empty string when hass is not set."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="kore",
        data={"host": "10.0.0.11", "password": "pw"},
    )
    entry.runtime_data = SimpleNamespace(
        device_info=DeviceInfo(identifiers={(DOMAIN, "SERIAL-TEXT-3")})
    )
    entity = text_platform.LegacyCleanupConfirmationCodeText(entry)
    assert entity.native_value == ""


async def test_cleanup_confirmation_text_set_value_missing_entry_store(hass):
    """async_set_value should no-op when entry_store is None (covers text.py:51)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="kore",
        data={"host": "10.0.0.11", "password": "pw"},
    )
    entry.runtime_data = SimpleNamespace(
        device_info=DeviceInfo(identifiers={(DOMAIN, "SERIAL-TEXT-5")})
    )
    # Set DOMAIN data but WITHOUT this entry's store
    hass.data.setdefault(DOMAIN, {})

    entity = text_platform.LegacyCleanupConfirmationCodeText(entry)
    entity.hass = hass
    entity.entity_id = "text.legacy_cleanup_confirmation_code_test"

    # Should not raise — entry_store is None, so early return
    await entity.async_set_value("test")

    # Verify nothing was stored
    assert entry.entry_id not in hass.data.get(DOMAIN, {})


async def test_cleanup_confirmation_text_set_value_no_hass_or_entity_id(hass):
    """async_set_value should no-op without hass and skip state write without entity_id."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="kore",
        data={"host": "10.0.0.11", "password": "pw"},
    )
    entry.runtime_data = SimpleNamespace(
        device_info=DeviceInfo(identifiers={(DOMAIN, "SERIAL-TEXT-4")})
    )

    entity = text_platform.LegacyCleanupConfirmationCodeText(entry)
    # no hass: should not raise and should not create data
    await entity.async_set_value("abc")

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {}
    entity.hass = hass
    # no entity_id branch should still persist normalized value
    await entity.async_set_value(" z9 ")
    assert hass.data[DOMAIN][entry.entry_id][DATA_KEY_LEGACY_CLEANUP_CODE_INPUT] == "Z9"
