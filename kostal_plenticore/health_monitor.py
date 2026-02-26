"""Inverter health monitoring and anomaly detection.

Tracks long-term trends of critical inverter parameters and generates
health scores and warnings. Data persists across restarts via HA's
restore mechanism.

Monitored parameters:
- Isolation resistance (Ohm) — declining trend indicates cable/module degradation
- Controller temperature (°C) — overheating detection
- Battery health (SoH %, cycles, capacity loss)
- Error/warning event frequency
- Communication reliability (Modbus error rate)
- Grid frequency stability
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

_LOGGER: Final = logging.getLogger(__name__)

MAX_HISTORY_SIZE: Final[int] = 1000
ISOLATION_WARNING_OHMS: Final[float] = 500_000.0
ISOLATION_CRITICAL_OHMS: Final[float] = 100_000.0
TEMP_WARNING_CELSIUS: Final[float] = 65.0
TEMP_CRITICAL_CELSIUS: Final[float] = 75.0
SOH_WARNING_PERCENT: Final[float] = 80.0
SOH_CRITICAL_PERCENT: Final[float] = 60.0
ERROR_RATE_WARNING_PER_HOUR: Final[float] = 5.0
GRID_FREQ_NOMINAL: Final[float] = 50.0
GRID_FREQ_TOLERANCE: Final[float] = 0.5


class HealthLevel(StrEnum):
    """Health assessment levels."""

    EXCELLENT = "excellent"
    GOOD = "good"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


@dataclass
class HealthEvent:
    """A single health-relevant event."""

    timestamp: float
    category: str
    message: str
    level: HealthLevel
    value: float | None = None


@dataclass
class HealthSample:
    """A timestamped sample of a monitored value."""

    timestamp: float
    value: float


@dataclass
class ParameterTracker:
    """Tracks min/max/avg/trend for a single parameter."""

    name: str
    unit: str
    samples: deque[HealthSample] = field(default_factory=lambda: deque(maxlen=MAX_HISTORY_SIZE))
    warning_low: float | None = None
    warning_high: float | None = None
    critical_low: float | None = None
    critical_high: float | None = None

    def record(self, value: float) -> None:
        self.samples.append(HealthSample(timestamp=time.monotonic(), value=value))

    @property
    def current(self) -> float | None:
        return self.samples[-1].value if self.samples else None

    @property
    def min_value(self) -> float | None:
        return min(s.value for s in self.samples) if self.samples else None

    @property
    def max_value(self) -> float | None:
        return max(s.value for s in self.samples) if self.samples else None

    @property
    def avg_value(self) -> float | None:
        if not self.samples:
            return None
        return sum(s.value for s in self.samples) / len(self.samples)

    @property
    def sample_count(self) -> int:
        return len(self.samples)

    @property
    def level(self) -> HealthLevel:
        """Assess current health level based on thresholds."""
        val = self.current
        if val is None:
            return HealthLevel.UNKNOWN
        if self.critical_low is not None and val < self.critical_low:
            return HealthLevel.CRITICAL
        if self.critical_high is not None and val > self.critical_high:
            return HealthLevel.CRITICAL
        if self.warning_low is not None and val < self.warning_low:
            return HealthLevel.WARNING
        if self.warning_high is not None and val > self.warning_high:
            return HealthLevel.WARNING
        return HealthLevel.GOOD

    @property
    def trend(self) -> str:
        """Calculate trend direction from recent samples."""
        if len(self.samples) < 10:
            return "insufficient_data"
        recent = list(self.samples)
        half = len(recent) // 2
        first_half_avg = sum(s.value for s in recent[:half]) / half
        second_half_avg = sum(s.value for s in recent[half:]) / (len(recent) - half)
        diff = second_half_avg - first_half_avg
        pct = abs(diff / first_half_avg) * 100 if first_half_avg != 0 else 0
        if pct < 1:
            return "stable"
        return "rising" if diff > 0 else "falling"


class InverterHealthMonitor:
    """Central health monitoring engine.

    Collects data from Modbus registers and REST API sensors,
    tracks trends, and generates health assessments.
    """

    def __init__(self) -> None:
        self.isolation = ParameterTracker(
            name="Isolation Resistance", unit="Ohm",
            warning_low=ISOLATION_WARNING_OHMS,
            critical_low=ISOLATION_CRITICAL_OHMS,
        )
        self.controller_temp = ParameterTracker(
            name="Controller Temperature", unit="°C",
            warning_high=TEMP_WARNING_CELSIUS,
            critical_high=TEMP_CRITICAL_CELSIUS,
        )
        self.battery_soh = ParameterTracker(
            name="Battery State of Health", unit="%",
            warning_low=SOH_WARNING_PERCENT,
            critical_low=SOH_CRITICAL_PERCENT,
        )
        self.battery_temp = ParameterTracker(
            name="Battery Temperature", unit="°C",
            warning_high=45.0,
            critical_high=55.0,
        )
        self.battery_cycles = ParameterTracker(
            name="Battery Cycles", unit="cycles",
        )
        self.grid_frequency = ParameterTracker(
            name="Grid Frequency", unit="Hz",
            warning_low=GRID_FREQ_NOMINAL - GRID_FREQ_TOLERANCE,
            warning_high=GRID_FREQ_NOMINAL + GRID_FREQ_TOLERANCE,
            critical_low=GRID_FREQ_NOMINAL - 1.0,
            critical_high=GRID_FREQ_NOMINAL + 1.0,
        )
        self._events: deque[HealthEvent] = deque(maxlen=MAX_HISTORY_SIZE)
        self._error_timestamps: deque[float] = deque(maxlen=MAX_HISTORY_SIZE)
        self._total_polls: int = 0
        self._failed_polls: int = 0

    @property
    def all_trackers(self) -> dict[str, ParameterTracker]:
        return {
            "isolation_resistance": self.isolation,
            "controller_temperature": self.controller_temp,
            "battery_soh": self.battery_soh,
            "battery_temperature": self.battery_temp,
            "battery_cycles": self.battery_cycles,
            "grid_frequency": self.grid_frequency,
        }

    def update_from_modbus(self, data: dict[str, Any]) -> None:
        """Feed Modbus register data into the health monitor."""
        self._total_polls += 1

        if (v := data.get("isolation_resistance")) is not None:
            try:
                self.isolation.record(float(v))
            except (TypeError, ValueError):
                pass

        if (v := data.get("controller_temp")) is not None:
            try:
                self.controller_temp.record(float(v))
            except (TypeError, ValueError):
                pass

        if (v := data.get("battery_temperature")) is not None:
            try:
                self.battery_temp.record(float(v))
            except (TypeError, ValueError):
                pass

        if (v := data.get("grid_frequency")) is not None:
            try:
                self.grid_frequency.record(float(v))
            except (TypeError, ValueError):
                pass

        if (v := data.get("battery_cycles")) is not None:
            try:
                self.battery_cycles.record(float(v))
            except (TypeError, ValueError):
                pass

    def update_battery_soh(self, soh: float) -> None:
        """Update battery state of health (from REST API sensor)."""
        self.battery_soh.record(soh)

    def record_error(self, category: str, message: str) -> None:
        """Record a health-relevant error event."""
        now = time.monotonic()
        self._error_timestamps.append(now)
        self._failed_polls += 1
        self._events.append(HealthEvent(
            timestamp=now,
            category=category,
            message=message,
            level=HealthLevel.WARNING,
        ))

    def record_event(self, category: str, message: str, level: HealthLevel, value: float | None = None) -> None:
        """Record a general health event."""
        self._events.append(HealthEvent(
            timestamp=time.monotonic(),
            category=category,
            message=message,
            level=level,
            value=value,
        ))

    @property
    def error_rate_per_hour(self) -> float:
        """Calculate error rate over the last hour."""
        now = time.monotonic()
        one_hour_ago = now - 3600
        recent = sum(1 for t in self._error_timestamps if t > one_hour_ago)
        return float(recent)

    @property
    def communication_reliability(self) -> float:
        """Return communication success rate as percentage."""
        if self._total_polls == 0:
            return 100.0
        return ((self._total_polls - self._failed_polls) / self._total_polls) * 100.0

    @property
    def recent_events(self) -> list[HealthEvent]:
        """Return the most recent health events."""
        return list(self._events)[-20:]

    @property
    def event_count(self) -> int:
        return len(self._events)

    @property
    def overall_health(self) -> HealthLevel:
        """Calculate overall system health from all trackers."""
        levels = [t.level for t in self.all_trackers.values() if t.current is not None]
        if not levels:
            return HealthLevel.UNKNOWN
        if any(l == HealthLevel.CRITICAL for l in levels):
            return HealthLevel.CRITICAL
        if any(l == HealthLevel.WARNING for l in levels):
            return HealthLevel.WARNING
        if self.error_rate_per_hour > ERROR_RATE_WARNING_PER_HOUR:
            return HealthLevel.WARNING
        return HealthLevel.GOOD

    @property
    def health_score(self) -> int:
        """Return a 0-100 health score."""
        score = 100
        for tracker in self.all_trackers.values():
            if tracker.level == HealthLevel.CRITICAL:
                score -= 25
            elif tracker.level == HealthLevel.WARNING:
                score -= 10
        if self.error_rate_per_hour > ERROR_RATE_WARNING_PER_HOUR:
            score -= 15
        if self.communication_reliability < 95:
            score -= 10
        return max(0, min(100, score))

    def get_health_summary(self) -> dict[str, Any]:
        """Return a complete health summary for diagnostics."""
        summary: dict[str, Any] = {
            "overall_health": self.overall_health.value,
            "health_score": self.health_score,
            "communication_reliability": round(self.communication_reliability, 1),
            "error_rate_per_hour": round(self.error_rate_per_hour, 1),
            "total_polls": self._total_polls,
            "failed_polls": self._failed_polls,
            "event_count": self.event_count,
            "trackers": {},
        }
        for name, tracker in self.all_trackers.items():
            summary["trackers"][name] = {
                "current": tracker.current,
                "min": tracker.min_value,
                "max": tracker.max_value,
                "avg": round(tracker.avg_value, 2) if tracker.avg_value is not None else None,
                "trend": tracker.trend,
                "level": tracker.level.value,
                "samples": tracker.sample_count,
            }
        return summary
