"""Tests for inverter-size-aware power limit helpers."""

from __future__ import annotations

from types import SimpleNamespace

from kostal_plenticore.power_limits import (
    DEFAULT_CONTROL_LIMIT_W,
    clamp_control_power_w,
    default_feed_in_limit_w,
    get_device_power_limit_w,
    is_device_power_limit_known,
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


def test_get_device_power_limit_handles_non_positive_and_missing_mapping() -> None:
    assert get_device_power_limit_w(_coord(0)) == DEFAULT_CONTROL_LIMIT_W
    assert get_device_power_limit_w(_coord(float("nan"))) == DEFAULT_CONTROL_LIMIT_W
    assert get_device_power_limit_w(SimpleNamespace(device_info_data=123)) == DEFAULT_CONTROL_LIMIT_W


def test_is_device_power_limit_known_detects_real_metadata() -> None:
    assert is_device_power_limit_known(_coord(5500)) is True
    assert is_device_power_limit_known(_coord("invalid")) is False
    assert is_device_power_limit_known(SimpleNamespace(device_info_data=None)) is False


def test_default_feed_in_limit_rounds_and_clamps_to_minimum() -> None:
    assert default_feed_in_limit_w(3333.0) == 2000.0
    assert default_feed_in_limit_w(10.0) == 100.0
