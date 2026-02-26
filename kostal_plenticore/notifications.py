"""Centralized notification system for the Kostal Plenticore integration.

All warnings, errors, and important status changes are pushed as
HA Persistent Notifications so the user sees them immediately --
not just in the logs.

Notification levels:
- info: Grünes ✓ -- positive Statusmeldung (z.B. Probe erfolgreich)
- warning: Gelbes ⚠ -- Handlung empfohlen (z.B. Modbus nicht aktiviert)
- error: Rotes ✗ -- sofortige Aufmerksamkeit nötig (z.B. Sicherheitswarnung)
"""

from __future__ import annotations

import logging
from typing import Any, Final

from homeassistant.core import HomeAssistant

_LOGGER: Final = logging.getLogger(__name__)

NOTIFICATION_PREFIX: Final[str] = "kostal_plenticore"


async def notify(
    hass: HomeAssistant,
    notification_id: str,
    title: str,
    message: str,
    level: str = "info",
) -> None:
    """Create a persistent notification in HA.

    Args:
        hass: Home Assistant instance
        notification_id: unique ID (prevents duplicates, allows dismissal)
        title: notification title
        message: notification body (supports markdown)
        level: "info", "warning", or "error" (affects icon prefix)
    """
    prefix = {"info": "✓", "warning": "⚠️", "error": "✗"}.get(level, "ℹ️")
    full_id = f"{NOTIFICATION_PREFIX}_{notification_id}"

    try:
        await hass.services.async_call(
            "persistent_notification", "create",
            {
                "title": f"{prefix} Kostal Plenticore: {title}",
                "message": message,
                "notification_id": full_id,
            },
        )
    except Exception as err:
        _LOGGER.debug("Could not create notification %s: %s", full_id, err)


async def dismiss(hass: HomeAssistant, notification_id: str) -> None:
    """Dismiss a previously created notification."""
    full_id = f"{NOTIFICATION_PREFIX}_{notification_id}"
    try:
        await hass.services.async_call(
            "persistent_notification", "dismiss",
            {"notification_id": full_id},
        )
    except Exception:
        pass


async def notify_modbus_probe_success(hass: HomeAssistant) -> None:
    """Notify that Modbus write access is confirmed working."""
    await dismiss(hass, "modbus_write_failed")
    await notify(
        hass, "modbus_write_ok",
        "Modbus-Batteriesteuerung aktiv",
        "Externe Batteriesteuerung über Modbus TCP ist aktiv und funktioniert.\n\n"
        "Du kannst jetzt die Batterie-Ladeleistung, Min/Max SoC und G3-Limits "
        "direkt über Home Assistant steuern.",
        level="info",
    )


async def notify_modbus_probe_failed(hass: HomeAssistant) -> None:
    """Notify that Modbus write access is NOT working."""
    await dismiss(hass, "modbus_write_ok")
    await notify(
        hass, "modbus_write_failed",
        "Modbus-Batteriesteuerung nicht aktiviert",
        "Die externe Batteriesteuerung über Modbus TCP ist **nicht aktiviert**.\n\n"
        "**So aktivierst du sie:**\n"
        "1. Öffne das Inverter-WebUI: `http://INVERTER-IP`\n"
        "2. Logge dich mit dem **Installateur/Service-Code** ein\n"
        "3. Gehe zu **Service → Batterie-Einstellungen**\n"
        "4. Wähle **Extern über Protokoll (Modbus TCP)**\n"
        "5. Speichern\n\n"
        "Ohne diese Einstellung funktioniert das Lesen von Daten, "
        "aber **Schreib-Befehle** (Ladeleistung setzen, SoC-Limits) werden ignoriert.",
        level="warning",
    )


async def notify_safety_alert(hass: HomeAssistant, risk_level: str, title: str, detail: str, action: str) -> None:
    """Push a fire safety alert as notification."""
    level = "error" if risk_level in ("high", "emergency") else "warning"
    await notify(
        hass, f"safety_{risk_level}",
        f"Sicherheitswarnung: {title}",
        f"**Risikostufe:** {risk_level.upper()}\n\n"
        f"**Details:** {detail}\n\n"
        f"**Empfohlene Maßnahme:** {action}",
        level=level,
    )


async def notify_safety_clear(hass: HomeAssistant) -> None:
    """Dismiss safety alerts when system returns to safe state."""
    for level in ("monitor", "elevated", "high", "emergency"):
        await dismiss(hass, f"safety_{level}")


async def notify_diagnosis(hass: HomeAssistant, area: str, status: str, title: str, detail: str, action: str) -> None:
    """Push a diagnostic finding as notification (only for warnung/kritisch)."""
    if status not in ("warnung", "kritisch"):
        await dismiss(hass, f"diag_{area}")
        return
    level = "error" if status == "kritisch" else "warning"
    await notify(
        hass, f"diag_{area}",
        title,
        f"**Bereich:** {area}\n\n"
        f"**Details:** {detail}\n\n"
        f"**Empfehlung:** {action}",
        level=level,
    )
