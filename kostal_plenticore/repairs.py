"""Repair issues for Kostal Plenticore integration."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN


def _issue_id(suffix: str) -> str:
    """Build a stable issue ID."""
    return f"{DOMAIN}_{suffix}"


def create_auth_failed_issue(hass: HomeAssistant) -> None:
    """Create an authentication failure repair issue."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id("auth_failed"),
        is_fixable=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key="auth_failed",
    )


def create_api_unreachable_issue(hass: HomeAssistant) -> None:
    """Create an API unreachable repair issue."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id("api_unreachable"),
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="api_unreachable",
    )


def create_inverter_busy_issue(hass: HomeAssistant) -> None:
    """Create an inverter busy repair issue."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id("inverter_busy"),
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="inverter_busy",
    )


def clear_issue(hass: HomeAssistant, suffix: str) -> None:
    """Clear a repair issue if present."""
    ir.async_delete_issue(hass, DOMAIN, _issue_id(suffix))
