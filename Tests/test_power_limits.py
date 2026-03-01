"""Tests for inverter-size-aware power limit helpers."""

from __future__ import annotations

from types import SimpleNamespace

from kostal_plenticore.power_limits import (
    DEFAULT_CONTROL_LIMIT_W,
    clamp_control_power_w,
    default_feed_in_limit_w,
    get_device_power_limit_w,
)


def _coord(limit: object) -> SimpleNamespace:
    return SimpleNamespace(device_info_data={"inverter_max_power": limit})


def test_get_device_power_limit_uses_inverter_value() -> None:
    assert get_device_power_limit_w(_coord(5500)) == 5500.0


def test_get_device_power_limit_uses_fallback_if_invalid() -> None:
    assert (
        get_device_power_limit_w(_coord("invalid"), fallback_w=DEFAULT_CONTROL_LIMIT_W)
        == DEFAULT_CONTROL_LIMIT_W
    )


def test_get_device_power_limit_clamps_to_hard_max() -> None:
    assert get_device_power_limit_w(_coord(100000)) == 20000.0


def test_clamp_control_power_respects_device_limit() -> None:
    assert clamp_control_power_w(7000.0, device_limit_w=5500.0) == 5500.0
    assert clamp_control_power_w(20.0, device_limit_w=5500.0) == 100.0


def test_default_feed_in_limit_uses_ratio() -> None:
    assert default_feed_in_limit_w(10000.0) == 6000.0
