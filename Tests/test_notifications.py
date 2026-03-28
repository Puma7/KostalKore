"""Tests for notification helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, call, patch

import pytest

from custom_components.kostal_kore import notifications


def _make_hass(async_call: AsyncMock | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        services=SimpleNamespace(async_call=async_call or AsyncMock()),
    )


@pytest.mark.parametrize(
    ("level", "expected_prefix"),
    [
        ("info", "✓"),
        ("warning", "⚠️"),
        ("error", "✗"),
        ("something_else", "ℹ️"),
    ],
)
async def test_notify_uses_expected_prefix_and_id(level: str, expected_prefix: str) -> None:
    """notify should create a persistent notification with stable IDs."""
    async_call = AsyncMock()
    hass = _make_hass(async_call)

    await notifications.notify(hass, "abc", "Title", "Body", level=level)

    async_call.assert_awaited_once_with(
        "persistent_notification",
        "create",
        {
            "title": f"{expected_prefix} Kostal Plenticore: Title",
            "message": "Body",
            "notification_id": "kostal_kore_abc",
        },
    )


async def test_notify_swallows_delivery_errors() -> None:
    """notify should not raise when persistent_notification delivery fails."""
    hass = _make_hass(AsyncMock(side_effect=RuntimeError("boom")))

    await notifications.notify(hass, "abc", "Title", "Body")


async def test_dismiss_swallows_delivery_errors() -> None:
    """dismiss should not raise when persistent_notification delivery fails."""
    hass = _make_hass(AsyncMock(side_effect=RuntimeError("boom")))

    await notifications.dismiss(hass, "abc")


async def test_notify_modbus_probe_helpers() -> None:
    """Probe notifications should call notify/dismiss correctly."""
    hass = _make_hass()
    with (
        patch("custom_components.kostal_kore.notifications.dismiss", new=AsyncMock()) as dismiss_mock,
        patch("custom_components.kostal_kore.notifications.notify", new=AsyncMock()) as notify_mock,
    ):
        await notifications.notify_modbus_probe_success(hass)
        await notifications.notify_modbus_probe_failed(hass)

    dismiss_mock.assert_has_awaits(
        [
            call(hass, "modbus_write_failed"),
            call(hass, "modbus_write_ok"),
        ]
    )
    notify_mock.assert_has_awaits(
        [
            call(hass, "modbus_write_ok", "Modbus-Verbindung aktiv", ANY, level="info"),
            call(hass, "modbus_write_failed", "Modbus-Batteriesteuerung nicht aktiviert", ANY, level="warning"),
        ],
        any_order=False,
    )


async def test_notify_safety_alert_uses_entry_scope_and_category() -> None:
    """Safety alerts should be scoped and retain category in the ID."""
    hass = _make_hass()

    with patch("custom_components.kostal_kore.notifications.notify", new=AsyncMock()) as notify_mock:
        await notifications.notify_safety_alert(
            hass,
            "high",
            "Battery hot",
            "detail",
            "action",
            entry_id="entry1",
            category="battery_thermal",
        )
        await notifications.notify_safety_alert(
            hass,
            "monitor",
            "Watch",
            "detail",
            "action",
        )

    notify_mock.assert_has_awaits(
        [
            call(
                hass,
                "entry1_safety_battery_thermal_high",
                "Sicherheitswarnung: Battery hot",
                "**Risikostufe:** HIGH\n\n**Details:** detail\n\n**Empfohlene Maßnahme:** action",
                level="error",
            ),
            call(
                hass,
                "safety_monitor",
                "Sicherheitswarnung: Watch",
                "**Risikostufe:** MONITOR\n\n**Details:** detail\n\n**Empfohlene Maßnahme:** action",
                level="warning",
            ),
        ]
    )


async def test_notify_safety_clear_dismisses_all_risk_levels() -> None:
    """Safety clear should remove notifications for all risk levels."""
    hass = _make_hass()

    with patch("custom_components.kostal_kore.notifications.dismiss", new=AsyncMock()) as dismiss_mock:
        await notifications.notify_safety_clear(hass, entry_id="entry1")

    expected = [
        call(hass, "entry1_safety_monitor"),
        call(hass, "entry1_safety_elevated"),
        call(hass, "entry1_safety_high"),
        call(hass, "entry1_safety_emergency"),
    ]
    assert dismiss_mock.await_args_list == expected


async def test_notify_safety_clear_without_entry_id() -> None:
    """Safety clear without entry_id should use unscoped IDs."""
    hass = _make_hass()

    with patch("custom_components.kostal_kore.notifications.dismiss", new=AsyncMock()) as dismiss_mock:
        await notifications.notify_safety_clear(hass)

    expected = [
        call(hass, "safety_monitor"),
        call(hass, "safety_elevated"),
        call(hass, "safety_high"),
        call(hass, "safety_emergency"),
    ]
    assert dismiss_mock.await_args_list == expected


@pytest.mark.parametrize("status", ["ok", "warnung", "kritisch"])
async def test_notify_diagnosis_routes_statuses_correctly(status: str) -> None:
    """Diagnosis notifications should dismiss on OK and notify on warning/error."""
    hass = _make_hass()
    with (
        patch("custom_components.kostal_kore.notifications.dismiss", new=AsyncMock()) as dismiss_mock,
        patch("custom_components.kostal_kore.notifications.notify", new=AsyncMock()) as notify_mock,
    ):
        await notifications.notify_diagnosis(
            hass,
            "area",
            status,
            "Title",
            "Detail",
            "Action",
        )

    if status == "ok":
        dismiss_mock.assert_awaited_once_with(hass, "diag_area")
        notify_mock.assert_not_called()
    else:
        dismiss_mock.assert_not_called()
        notify_mock.assert_awaited_once()
        notification_id = notify_mock.await_args.args[1]
        assert notification_id == "diag_area"


async def test_notify_diagnosis_with_entry_id() -> None:
    """Diagnosis with entry_id should scope the notification ID."""
    hass = _make_hass()
    with (
        patch("custom_components.kostal_kore.notifications.dismiss", new=AsyncMock()) as dismiss_mock,
        patch("custom_components.kostal_kore.notifications.notify", new=AsyncMock()) as notify_mock,
    ):
        await notifications.notify_diagnosis(
            hass, "area", "ok", "Title", "Detail", "Action", entry_id="e1",
        )

    dismiss_mock.assert_awaited_once_with(hass, "e1_diag_area")
    notify_mock.assert_not_called()
