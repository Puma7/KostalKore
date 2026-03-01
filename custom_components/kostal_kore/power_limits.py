"""Helpers for inverter-size-aware power limits."""

from __future__ import annotations

import math
from typing import Any, Final

MIN_CONTROL_LIMIT_W: Final[float] = 100.0
DEFAULT_CONTROL_LIMIT_W: Final[float] = 5000.0
HARD_MAX_CONTROL_LIMIT_W: Final[float] = 20_000.0
DEFAULT_FEED_IN_RATIO: Final[float] = 0.60


def _to_finite_positive(value: Any) -> float | None:
    """Convert value to a finite positive float."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result) or result <= 0:
        return None
    return result


def _normalize_power_limit(limit_w: float) -> float:
    """Clamp a power limit to safe global bounds."""
    return max(MIN_CONTROL_LIMIT_W, min(HARD_MAX_CONTROL_LIMIT_W, float(limit_w)))


def get_device_power_limit_w(
    coordinator: Any,
    *,
    fallback_w: float = DEFAULT_CONTROL_LIMIT_W,
) -> float:
    """Return inverter max power from device info (with safe fallback)."""
    raw_limit = None
    try:
        device_info = getattr(coordinator, "device_info_data", None) or {}
        raw_limit = device_info.get("inverter_max_power")
    except Exception:
        raw_limit = None

    parsed = _to_finite_positive(raw_limit)
    if parsed is None:
        return _normalize_power_limit(fallback_w)
    return _normalize_power_limit(parsed)


def clamp_control_power_w(value_w: float, *, device_limit_w: float) -> float:
    """Clamp requested control power to inverter-specific range."""
    return max(
        MIN_CONTROL_LIMIT_W,
        min(_normalize_power_limit(device_limit_w), float(value_w)),
    )


def default_feed_in_limit_w(device_limit_w: float) -> float:
    """Return a conservative default feed-in limit based on inverter size."""
    base_limit = _normalize_power_limit(device_limit_w) * DEFAULT_FEED_IN_RATIO
    # Use 50W steps to keep values user-friendly in HA number entities.
    rounded = round(base_limit / 50.0) * 50.0
    return max(MIN_CONTROL_LIMIT_W, rounded)
