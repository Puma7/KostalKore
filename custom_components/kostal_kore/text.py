"""Text entities for KOSTAL KORE integration."""

from __future__ import annotations

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant

from .const import (
    AddConfigEntryEntitiesCallback,
    DATA_KEY_LEGACY_CLEANUP_CODE_INPUT,
    DOMAIN,
)
from .coordinator import PlenticoreConfigEntry


class LegacyCleanupConfirmationCodeText(TextEntity):
    """Text box used to confirm destructive legacy cleanup."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Legacy Cleanup Confirmation Code"
    _attr_icon = "mdi:form-textbox-password"
    _attr_mode = TextMode.PASSWORD
    _attr_native_min = 0
    _attr_native_max = 32

    def __init__(self, entry: PlenticoreConfigEntry) -> None:
        """Initialize text entity."""
        self._entry_id = entry.entry_id
        self._attr_unique_id = f"{entry.entry_id}_legacy_cleanup_confirmation_code"
        self._attr_device_info = entry.runtime_data.device_info

    @property
    def native_value(self) -> str:
        """Return masked placeholder -- the real code is only in hass.data, not entity state."""
        if self.hass is None:
            return ""
        entry_store = self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        code = entry_store.get(DATA_KEY_LEGACY_CLEANUP_CODE_INPUT, "")
        # Return length indicator instead of actual code to avoid leaking
        # the OTP into recorder history, templates, and automations.
        return "*" * len(code) if code else ""

    async def async_set_value(self, value: str) -> None:
        """Store user entered confirmation code."""
        if self.hass is None:
            return
        entry_store = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if entry_store is None:
            return
        entry_store[DATA_KEY_LEGACY_CLEANUP_CODE_INPUT] = value.strip().upper()
        if self.entity_id is not None:
            self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PlenticoreConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up text entities for the integration."""
    entry_store = hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})
    entry_store.setdefault(DATA_KEY_LEGACY_CLEANUP_CODE_INPUT, "")
    async_add_entities([LegacyCleanupConfirmationCodeText(entry)])
