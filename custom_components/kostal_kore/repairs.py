"""Repair issues for Kostal Plenticore integration.

Every issue is informational (``is_fixable=False``): this module defines no
``async_create_fix_flow``. A rejected password is not a repair issue; it
raises ``ConfigEntryAuthFailed`` and Home Assistant starts the reauth flow.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN


def _issue_id(suffix: str, *, entry_id: str = "") -> str:
    """Build a stable issue ID, scoped to a config entry when provided."""
    if entry_id:
        return f"{DOMAIN}_{entry_id}_{suffix}"
    return f"{DOMAIN}_{suffix}"


def create_api_unreachable_issue(hass: HomeAssistant, *, entry_id: str = "") -> None:
    """Create an API unreachable repair issue."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id("api_unreachable", entry_id=entry_id),
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="api_unreachable",
    )


def create_inverter_busy_issue(hass: HomeAssistant, *, entry_id: str = "") -> None:
    """Create an inverter busy repair issue."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id("inverter_busy", entry_id=entry_id),
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="inverter_busy",
    )


def create_installer_required_issue(hass: HomeAssistant, *, entry_id: str = "") -> None:
    """Create a repair issue when installer/service code is required."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id("installer_required", entry_id=entry_id),
        # Not fixable here: this module has no fix flow (async_create_fix_flow),
        # and the service code is entered with Reconfigure.
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="installer_required",
    )


def create_battery_capacity_unit_migration_issue(   # NEU
    hass: HomeAssistant, *, entry_id: str
) -> None:
    """Inform user that Battery Work Capacity unit changed from Ah to Wh."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id("battery_capacity_unit_migration", entry_id=entry_id),
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="battery_capacity_unit_migration",
    )


def clear_issue(hass: HomeAssistant, suffix: str, *, entry_id: str = "") -> None:
    """Clear a repair issue if present."""
    ir.async_delete_issue(hass, DOMAIN, _issue_id(suffix, entry_id=entry_id))
