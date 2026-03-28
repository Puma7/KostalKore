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
        ("info", "[OK]"),
        ("warning", "[WARN]"),
        ("error", "[ERR]"),
        ("something_else", "[INFO]"),
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


async def test_notify_modbus_probe_helpers_are_entry_scoped() -> None:
    """Probe notifications should not collide across config entries."""
    hass = _make_hass()
    with (
        patch("custom_components.kostal_kore.notifications.dismiss", new=AsyncMock()) as dismiss_mock,
        patch("custom_components.kostal_kore.notifications.notify", new=AsyncMock()) as notify_mock,
    ):
        await notifications.notify_modbus_probe_success(hass, entry_id="entry1")
        await notifications.notify_modbus_probe_failed(hass, entry_id="entry2")

    dismiss_mock.assert_has_awaits(
        [
            call(hass, "entry1_modbus_write_failed"),
            call(hass, "entry2_modbus_write_ok"),
        ]
    )
    notify_mock.assert_has_awaits(
        [
            call(hass, "entry1_modbus_write_ok", "Modbus connection active", ANY, level="info"),
            call(hass, "entry2_modbus_write_failed", "Modbus battery control not enabled", ANY, level="warning"),
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
                "Safety warning: Battery hot",
                "**Risk level:** HIGH\n\n**Details:** detail\n\n**Recommended action:** action",
                level="error",
            ),
            call(
                hass,
                "safety_monitor",
                "Safety warning: Watch",
                "**Risk level:** MONITOR\n\n**Details:** detail\n\n**Recommended action:** action",
                level="warning",
            ),
        ]
    )


async def test_notify_safety_clear_dismisses_legacy_and_category_ids() -> None:
    """Safety clear should remove both old and category-scoped notifications."""
    hass = _make_hass()

    with patch("custom_components.kostal_kore.notifications.dismiss", new=AsyncMock()) as dismiss_mock:
        await notifications.notify_safety_clear(hass, entry_id="entry1")

    expected = []
    for risk_level in notifications.SAFETY_RISK_LEVELS:
        expected.append(call(hass, f"entry1_safety_{risk_level}"))
        for category in notifications.SAFETY_ALERT_CATEGORIES:
            expected.append(call(hass, f"entry1_safety_{category}_{risk_level}"))

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
