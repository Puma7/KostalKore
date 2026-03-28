"""Inverter health monitoring and anomaly detection.

Tracks long-term trends of ALL available health-relevant parameters from
both Modbus registers and REST API sensors. Uses a 3-level threshold
system: INFO → WARNING → CRITICAL.

Monitored parameter categories:
1. Electrical Safety: isolation resistance, PSSB fuse state
2. Thermal: controller temperature, battery temperature
3. Battery Health: SoH, cycles, capacity loss, voltage
4. Grid Quality: frequency stability, cos phi, voltage per phase
5. Communication: Modbus error rate, poll success rate
6. Inverter Status: state tracking, error/warning counters, worktime
7. Power Quality: DC string balance, AC phase balance
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Any, Final

from .helper import normalize_isolation_resistance_ohm

_LOGGER: Final = logging.getLogger(__name__)

MAX_HISTORY_SIZE: Final[int] = 2000


def _safe_float(value: Any) -> float | None:
    """Convert value to float with graceful fallback."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _detect_nominal_frequency_hz(freq_hz: float | None) -> float:
    """Infer nominal grid frequency (50/60Hz)."""
    if freq_hz is None:
        return 50.0
    return 60.0 if freq_hz >= 55.0 else 50.0


def _detect_nominal_phase_voltage_v(voltages: list[float]) -> float:
    """Infer nominal phase voltage profile (120V or 230V)."""
    active = [v for v in voltages if v > 80.0]
    if not active:
        return 230.0
    avg = sum(active) / len(active)
    return 120.0 if avg < 180.0 else 230.0


class HealthLevel(StrEnum):
    """Health assessment levels (3 tiers + unknown)."""

    GOOD = "good"
    INFO = "info"
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
    """Tracks min/max/avg/trend for a single parameter with 3-level thresholds."""

    name: str
    unit: str
    samples: deque[HealthSample] = field(default_factory=lambda: deque(maxlen=MAX_HISTORY_SIZE))
    info_low: float | None = None
    info_high: float | None = None
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
        """Assess current health level (3-tier: info → warning → critical)."""
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
        if self.info_low is not None and val < self.info_low:
            return HealthLevel.INFO
        if self.info_high is not None and val > self.info_high:
            return HealthLevel.INFO
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

    def __init__(self, *, num_bidirectional: int = 0) -> None:
        self._num_bidirectional: int = num_bidirectional
        # --- Electrical Safety ---
        self.isolation = ParameterTracker(
            name="Isolation Resistance", unit="Ω",
            info_low=1_000_000.0, warning_low=500_000.0, critical_low=100_000.0,
        )

        # --- Thermal ---
        self.controller_temp = ParameterTracker(
            name="Controller Temperature", unit="°C",
            info_high=62.0, warning_high=70.0, critical_high=80.0,
        )
        self.battery_temp = ParameterTracker(
            name="Battery Temperature", unit="°C",
            info_high=38.0, warning_high=45.0, critical_high=55.0,
        )

        # --- Battery Health ---
        self.battery_soh = ParameterTracker(
            name="Battery State of Health", unit="%",
            info_low=90.0, warning_low=80.0, critical_low=60.0,
        )
        self.battery_cycles = ParameterTracker(
            name="Battery Cycles", unit="cycles",
            info_high=3000.0, warning_high=5000.0, critical_high=8000.0,
        )
        self.battery_voltage = ParameterTracker(
            name="Battery Voltage", unit="V",
        )
        self.battery_capacity_wh = ParameterTracker(
            name="Battery Work Capacity", unit="Wh",
        )

        # --- Grid Quality ---
        self.grid_frequency = ParameterTracker(
            name="Grid Frequency", unit="Hz",
            info_low=49.7, info_high=50.3,
            warning_low=49.5, warning_high=50.5,
            critical_low=49.0, critical_high=51.0,
        )
        self.phase1_voltage = ParameterTracker(
            name="Phase 1 Voltage", unit="V",
            info_low=207.0, info_high=253.0,
            warning_low=195.0, warning_high=255.0,
            critical_low=185.0, critical_high=265.0,
        )
        self.phase2_voltage = ParameterTracker(
            name="Phase 2 Voltage", unit="V",
            info_low=207.0, info_high=253.0,
            warning_low=195.0, warning_high=255.0,
            critical_low=185.0, critical_high=265.0,
        )
        self.phase3_voltage = ParameterTracker(
            name="Phase 3 Voltage", unit="V",
            info_low=207.0, info_high=253.0,
            warning_low=195.0, warning_high=255.0,
            critical_low=185.0, critical_high=265.0,
        )
        self.cos_phi = ParameterTracker(
            name="Power Factor (cos φ)", unit="",
        )
        self.pm_cos_phi = ParameterTracker(
            name="Power Factor Powermeter", unit="",
        )

        # --- DC String Health ---
        self.dc1_voltage = ParameterTracker(name="DC1 Voltage", unit="V")
        self.dc2_voltage = ParameterTracker(name="DC2 Voltage", unit="V")
        self.dc3_voltage = ParameterTracker(name="DC3 Voltage", unit="V")
        self.dc1_power = ParameterTracker(name="DC1 Power", unit="W")
        self.dc2_power = ParameterTracker(name="DC2 Power", unit="W")
        self.dc3_power = ParameterTracker(name="DC3 Power", unit="W")

        # --- Inverter Status ---
        self.power_limit_evu = ParameterTracker(
            name="Power Limit EVU", unit="%",
        )
        self.active_error_count = ParameterTracker(
            name="Active Error Count", unit="",
            info_high=0.0, warning_high=1.0, critical_high=5.0,
        )
        self.active_warning_count = ParameterTracker(
            name="Active Warning Count", unit="",
            info_high=0.0, warning_high=3.0, critical_high=10.0,
        )

        # --- Event / Communication ---
        self._events: deque[HealthEvent] = deque(maxlen=MAX_HISTORY_SIZE)
        self._error_timestamps: deque[float] = deque(maxlen=MAX_HISTORY_SIZE)
        self._total_polls: int = 0
        self._failed_polls: int = 0
        self._inverter_state_changes: deque[HealthEvent] = deque(maxlen=100)
        self._last_inverter_state: int | None = None

    @property
    def all_trackers(self) -> dict[str, ParameterTracker]:
        return {
            "isolation_resistance": self.isolation,
            "controller_temperature": self.controller_temp,
            "battery_temperature": self.battery_temp,
            "battery_soh": self.battery_soh,
            "battery_cycles": self.battery_cycles,
            "battery_voltage": self.battery_voltage,
            "battery_capacity": self.battery_capacity_wh,
            "grid_frequency": self.grid_frequency,
            "phase1_voltage": self.phase1_voltage,
            "phase2_voltage": self.phase2_voltage,
            "phase3_voltage": self.phase3_voltage,
            "cos_phi": self.cos_phi,
            "pm_cos_phi": self.pm_cos_phi,
            "dc1_voltage": self.dc1_voltage,
            "dc2_voltage": self.dc2_voltage,
            "dc3_voltage": self.dc3_voltage,
            "dc1_power": self.dc1_power,
            "dc2_power": self.dc2_power,
            "dc3_power": self.dc3_power,
            "power_limit_evu": self.power_limit_evu,
            "active_errors": self.active_error_count,
            "active_warnings": self.active_warning_count,
        }

    # ------------------------------------------------------------------
    # Data ingestion
    # ------------------------------------------------------------------

    def update_from_modbus(self, data: dict[str, Any]) -> None:
        """Feed Modbus register data into the health monitor."""
        self._total_polls += 1
        self._apply_grid_profile(data)
        total_dc = _safe_float(data.get("total_dc_power"))
        pv_active = total_dc is not None and total_dc > 50
        inverter_state_raw = _safe_float(data.get("inverter_state"))
        inverter_state = (
            int(inverter_state_raw) if inverter_state_raw is not None else None
        )

        _map: dict[str, ParameterTracker] = {
            "isolation_resistance": self.isolation,
            "controller_temp": self.controller_temp,
            "battery_temperature": self.battery_temp,
            "battery_voltage": self.battery_voltage,
            "battery_cycles": self.battery_cycles,
            "grid_frequency": self.grid_frequency,
            "phase1_voltage": self.phase1_voltage,
            "phase2_voltage": self.phase2_voltage,
            "phase3_voltage": self.phase3_voltage,
            "cos_phi": self.cos_phi,
            "pm_cos_phi": self.pm_cos_phi,
            "dc1_voltage": self.dc1_voltage,
            "dc2_voltage": self.dc2_voltage,
            "dc3_voltage": self.dc3_voltage,
            "dc1_power": self.dc1_power,
            "dc2_power": self.dc2_power,
            "dc3_power": self.dc3_power,
            "power_limit_evu": self.power_limit_evu,
            "battery_work_capacity": self.battery_capacity_wh,
        }
        for key, tracker in _map.items():
            val = data.get(key)
            if val is not None:
                try:
                    fval = float(val)
                    if key == "isolation_resistance":
                        # Only record isolation when PV is active to avoid
                        # mixed-unit day/night values that flip between Ω
                        # and kΩ in HA long-term statistics.
                        if not pv_active:
                            continue
                        normalized_ohm = normalize_isolation_resistance_ohm(
                            val,
                            pv_active=pv_active,
                            inverter_state=inverter_state,
                        )
                        if normalized_ohm is None:
                            continue
                        fval = normalized_ohm
                    tracker.record(fval)
                except (TypeError, ValueError):
                    pass

    def _apply_grid_profile(self, data: dict[str, Any]) -> None:
        """Adapt frequency/voltage thresholds for 50/60Hz and 120/230V grids."""
        freq = _safe_float(data.get("grid_frequency"))
        nominal_freq = _detect_nominal_frequency_hz(freq)
        self.grid_frequency.info_low = nominal_freq - 0.3
        self.grid_frequency.info_high = nominal_freq + 0.3
        self.grid_frequency.warning_low = nominal_freq - 0.5
        self.grid_frequency.warning_high = nominal_freq + 0.5
        self.grid_frequency.critical_low = nominal_freq - 1.0
        self.grid_frequency.critical_high = nominal_freq + 1.0

        phase_values = [
            v
            for v in (
                _safe_float(data.get("phase1_voltage")),
                _safe_float(data.get("phase2_voltage")),
                _safe_float(data.get("phase3_voltage")),
            )
            if v is not None
        ]
        nominal_voltage = _detect_nominal_phase_voltage_v(phase_values)
        if nominal_voltage <= 130.0:
            info_low, info_high = 108.0, 132.0
            warning_low, warning_high = 102.0, 138.0
            critical_low, critical_high = 95.0, 145.0
        else:
            info_low, info_high = 207.0, 253.0
            warning_low, warning_high = 195.0, 255.0
            critical_low, critical_high = 185.0, 265.0

        for phase_tracker in (
            self.phase1_voltage,
            self.phase2_voltage,
            self.phase3_voltage,
        ):
            phase_tracker.info_low = info_low
            phase_tracker.info_high = info_high
            phase_tracker.warning_low = warning_low
            phase_tracker.warning_high = warning_high
            phase_tracker.critical_low = critical_low
            phase_tracker.critical_high = critical_high

        state = data.get("inverter_state")
        if state is not None:
            try:
                state_int = int(state)
                if self._last_inverter_state is not None and state_int != self._last_inverter_state:
                    self._inverter_state_changes.append(HealthEvent(
                        timestamp=time.monotonic(),
                        category="state_change",
                        message=f"Inverter state: {self._last_inverter_state} → {state_int}",
                        level=HealthLevel.INFO,
                        value=float(state_int),
                    ))
                self._last_inverter_state = state_int
            except (TypeError, ValueError):
                pass

    def update_battery_soh(self, soh: float) -> None:
        """Update battery state of health (from REST API sensor).

        A value of 0% is treated as not available, since many inverters
        report 0 when the actual SoH reading is unsupported.
        """
        if soh <= 0:
            return
        self.battery_soh.record(soh)

    def update_error_counts(self, errors: int, warnings: int) -> None:
        """Update active error/warning counters (from REST API sensors)."""
        self.active_error_count.record(float(errors))
        self.active_warning_count.record(float(warnings))

    def record_error(self, category: str, message: str) -> None:
        """Record a health-relevant error event."""
        now = time.monotonic()
        self._error_timestamps.append(now)
        self._failed_polls += 1
        self._events.append(HealthEvent(
            timestamp=now, category=category, message=message, level=HealthLevel.WARNING,
        ))

    def record_event(self, category: str, message: str, level: HealthLevel, value: float | None = None) -> None:
        """Record a general health event."""
        self._events.append(HealthEvent(
            timestamp=time.monotonic(), category=category, message=message, level=level, value=value,
        ))

    # ------------------------------------------------------------------
    # Computed metrics
    # ------------------------------------------------------------------

    @property
    def error_rate_per_hour(self) -> float:
        now = time.monotonic()
        one_hour_ago = now - 3600
        return float(sum(1 for t in self._error_timestamps if t > one_hour_ago))

    @property
    def communication_reliability(self) -> float:
        if self._total_polls == 0:
            return 100.0
        return ((self._total_polls - self._failed_polls) / self._total_polls) * 100.0

    @property
    def dc_string_imbalance(self) -> float | None:
        """Calculate DC string power imbalance as percentage.

        High imbalance may indicate shading, soiling, or defective panels.
        Excludes DC3 when it is used as battery I/O (num_bidirectional >= 1).
        """
        trackers = [self.dc1_power, self.dc2_power]
        if self._num_bidirectional < 1:
            trackers.append(self.dc3_power)

        powers = [t.current for t in trackers]
        active = [p for p in powers if p is not None and p > 50]
        if len(active) < 2:
            return None
        avg = sum(active) / len(active)
        if avg < 50:
            return None
        max_dev = max(abs(p - avg) for p in active)
        return (max_dev / avg) * 100.0

    @property
    def phase_voltage_imbalance(self) -> float | None:
        """Calculate AC phase voltage imbalance as percentage."""
        voltages = [
            self.phase1_voltage.current,
            self.phase2_voltage.current,
            self.phase3_voltage.current,
        ]
        active = [v for v in voltages if v is not None and v > 100]
        if len(active) < 2:
            return None
        avg = sum(active) / len(active)
        max_dev = max(abs(v - avg) for v in active)
        return (max_dev / avg) * 100.0

    @property
    def recent_events(self) -> list[HealthEvent]:
        return list(self._events)[-20:]

    @property
    def event_count(self) -> int:
        return len(self._events)

    @property
    def state_change_count(self) -> int:
        return len(self._inverter_state_changes)

    # ------------------------------------------------------------------
    # Overall health assessment
    # ------------------------------------------------------------------

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
        if any(l == HealthLevel.INFO for l in levels):
            return HealthLevel.INFO
        if self.error_rate_per_hour > 5.0:
            return HealthLevel.WARNING
        return HealthLevel.GOOD

    @property
    def health_score(self) -> int:
        """Return a 0-100 health score."""
        score = 100
        for tracker in self.all_trackers.values():
            if tracker.level == HealthLevel.CRITICAL:
                score -= 20
            elif tracker.level == HealthLevel.WARNING:
                score -= 8
            elif tracker.level == HealthLevel.INFO:
                score -= 3
        if self.error_rate_per_hour > 5.0:
            score -= 10
        if self.communication_reliability < 95:
            score -= 10
        imb = self.dc_string_imbalance
        if imb is not None and imb > 30:
            score -= 5
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
            "state_changes": self.state_change_count,
            "dc_string_imbalance": round(self.dc_string_imbalance, 1) if self.dc_string_imbalance is not None else None,
            "phase_voltage_imbalance": round(self.phase_voltage_imbalance, 1) if self.phase_voltage_imbalance is not None else None,
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
