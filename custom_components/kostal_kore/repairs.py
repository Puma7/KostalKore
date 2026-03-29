"""Repair issues for Kostal Plenticore integration."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN


def _issue_id(suffix: str, *, entry_id: str = "") -> str:
    """Build a stable issue ID, scoped to a config entry when provided."""
    if entry_id:
        return f"{DOMAIN}_{entry_id}_{suffix}"
    return f"{DOMAIN}_{suffix}"


def create_auth_failed_issue(hass: HomeAssistant, *, entry_id: str = "") -> None:
    """Create an authentication failure repair issue."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id("auth_failed", entry_id=entry_id),
        is_fixable=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key="auth_failed",
    )


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
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="installer_required",
    )


def clear_issue(hass: HomeAssistant, suffix: str, *, entry_id: str = "") -> None:
    """Clear a repair issue if present."""
    ir.async_delete_issue(hass, DOMAIN, _issue_id(suffix, entry_id=entry_id))
