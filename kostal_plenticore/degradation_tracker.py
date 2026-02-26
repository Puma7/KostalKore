"""Persistent degradation tracking for long-term trend detection.

Detects slow degradation that the inverter itself cannot see:
- Isolation resistance slowly dropping over months (cable aging)
- Battery capacity gradually declining (cell degradation)
- Controller running hotter over time (dust in ventilation)
- DC string output declining (panel degradation, connector corrosion)

Persistence: Uses HA's RestoreEntity mechanism to survive restarts.
Data is stored as daily snapshots (min/max/avg per day) to keep
storage bounded while enabling weeks/months of trend analysis.

Degradation rate: Calculated as change per 30 days using linear
regression over the available daily snapshots.

Baseline: The first 7 days of data establish the baseline. All
subsequent measurements are compared against this baseline.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Final

_LOGGER: Final = logging.getLogger(__name__)

MAX_DAILY_SNAPSHOTS: Final[int] = 365
BASELINE_DAYS: Final[int] = 7
SECONDS_PER_DAY: Final[float] = 86400.0


@dataclass
class DailySnapshot:
    """Aggregated data for one day."""

    day: int
    min_val: float
    max_val: float
    sum_val: float
    count: int

    @property
    def avg(self) -> float:
        return self.sum_val / self.count if self.count > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"day": self.day, "min": self.min_val, "max": self.max_val, "sum": self.sum_val, "count": self.count}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> DailySnapshot:
        return DailySnapshot(day=d["day"], min_val=d["min"], max_val=d["max"], sum_val=d["sum"], count=d["count"])


@dataclass
class TrackedParameter:
    """Long-term degradation tracking for a single parameter."""

    name: str
    unit: str
    snapshots: list[DailySnapshot] = field(default_factory=list)
    _current_day: int = 0
    _current_snapshot: DailySnapshot | None = None

    def record(self, value: float, now: float | None = None) -> None:
        """Record a new measurement."""
        if now is None:
            now = time.time()
        day = int(now / SECONDS_PER_DAY)

        if self._current_snapshot is None or self._current_day != day:
            if self._current_snapshot is not None:
                self.snapshots.append(self._current_snapshot)
                if len(self.snapshots) > MAX_DAILY_SNAPSHOTS:
                    self.snapshots = self.snapshots[-MAX_DAILY_SNAPSHOTS:]
            self._current_day = day
            self._current_snapshot = DailySnapshot(day=day, min_val=value, max_val=value, sum_val=value, count=1)
        else:
            self._current_snapshot.min_val = min(self._current_snapshot.min_val, value)
            self._current_snapshot.max_val = max(self._current_snapshot.max_val, value)
            self._current_snapshot.sum_val += value
            self._current_snapshot.count += 1

    @property
    def days_tracked(self) -> int:
        return len(self.snapshots) + (1 if self._current_snapshot else 0)

    @property
    def baseline_avg(self) -> float | None:
        """Average of the first BASELINE_DAYS days."""
        baseline = self.snapshots[:BASELINE_DAYS]
        if len(baseline) < BASELINE_DAYS:
            return None
        return sum(s.avg for s in baseline) / len(baseline)

    @property
    def current_avg(self) -> float | None:
        """Average of the last 7 days."""
        recent = self.snapshots[-7:]
        if not recent:
            if self._current_snapshot and self._current_snapshot.count > 0:
                return self._current_snapshot.avg
            return None
        return sum(s.avg for s in recent) / len(recent)

    @property
    def baseline_deviation_pct(self) -> float | None:
        """Percentage change from baseline to current."""
        base = self.baseline_avg
        curr = self.current_avg
        if base is None or curr is None or base == 0:
            return None
        return ((curr - base) / abs(base)) * 100.0

    @property
    def degradation_rate_per_month(self) -> float | None:
        """Rate of change per 30 days using linear regression."""
        all_snaps = list(self.snapshots)
        if self._current_snapshot:
            all_snaps.append(self._current_snapshot)
        if len(all_snaps) < 7:
            return None

        n = len(all_snaps)
        x = [float(s.day - all_snaps[0].day) for s in all_snaps]
        y = [s.avg for s in all_snaps]

        x_mean = sum(x) / n
        y_mean = sum(y) / n

        numerator = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, y))
        denominator = sum((xi - x_mean) ** 2 for xi in x)

        if denominator == 0:
            return None

        slope_per_day = numerator / denominator
        return slope_per_day * 30.0

    @property
    def trend_description(self) -> str:
        """Human-readable trend description."""
        rate = self.degradation_rate_per_month
        if rate is None:
            return "Noch nicht genug Daten (mind. 7 Tage nötig)"
        dev = self.baseline_deviation_pct
        abs_rate = abs(rate)
        unit = self.unit

        if abs_rate < 0.1:
            return "Stabil – keine signifikante Veränderung"

        direction = "steigend" if rate > 0 else "fallend"
        desc = f"{direction} ({rate:+.1f} {unit}/Monat)"

        if dev is not None and abs(dev) > 5:
            desc += f", {dev:+.1f}% seit Baseline"

        return desc

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "unit": self.unit,
            "snapshots": [s.to_dict() for s in self.snapshots],
            "current_day": self._current_day,
            "current_snapshot": self._current_snapshot.to_dict() if self._current_snapshot else None,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> TrackedParameter:
        tp = TrackedParameter(name=d["name"], unit=d["unit"])
        tp.snapshots = [DailySnapshot.from_dict(s) for s in d.get("snapshots", [])]
        tp._current_day = d.get("current_day", 0)
        cs = d.get("current_snapshot")
        if cs:
            tp._current_snapshot = DailySnapshot.from_dict(cs)
        return tp


class DegradationTracker:
    """Tracks long-term degradation across restarts.

    Data is persisted via to_dict()/from_dict() and stored in
    HA's RestoreEntity extra_stored_data mechanism.
    """

    def __init__(self) -> None:
        self.isolation = TrackedParameter("Isolationswiderstand", "kΩ")
        self.battery_soh = TrackedParameter("Batterie SoH", "%")
        self.battery_capacity = TrackedParameter("Batterie Kapazität", "Wh")
        self.battery_temp_avg = TrackedParameter("Batterie Ø-Temperatur", "°C")
        self.controller_temp_avg = TrackedParameter("Controller Ø-Temperatur", "°C")
        self.dc1_peak_power = TrackedParameter("DC1 Spitzenleistung", "W")
        self.dc2_peak_power = TrackedParameter("DC2 Spitzenleistung", "W")
        self.daily_yield = TrackedParameter("Tagesertrag", "Wh")

    @property
    def all_parameters(self) -> dict[str, TrackedParameter]:
        return {
            "isolation": self.isolation,
            "battery_soh": self.battery_soh,
            "battery_capacity": self.battery_capacity,
            "battery_temp": self.battery_temp_avg,
            "controller_temp": self.controller_temp_avg,
            "dc1_peak": self.dc1_peak_power,
            "dc2_peak": self.dc2_peak_power,
            "daily_yield": self.daily_yield,
        }

    def update_from_modbus(self, data: dict[str, Any]) -> None:
        """Feed Modbus data into the degradation tracker."""
        now = time.time()

        iso = data.get("isolation_resistance")
        if iso is not None:
            try:
                self.isolation.record(float(iso) / 1000.0, now)
            except (TypeError, ValueError):
                pass

        bat_temp = data.get("battery_temperature")
        if bat_temp is not None:
            try:
                self.battery_temp_avg.record(float(bat_temp), now)
            except (TypeError, ValueError):
                pass

        ctrl_temp = data.get("controller_temp")
        if ctrl_temp is not None:
            try:
                self.controller_temp_avg.record(float(ctrl_temp), now)
            except (TypeError, ValueError):
                pass

        for i, tracker in [(1, self.dc1_peak_power), (2, self.dc2_peak_power)]:
            dc_power = data.get(f"dc{i}_power")
            if dc_power is not None:
                try:
                    val = float(dc_power)
                    if val > 100:
                        tracker.record(val, now)
                except (TypeError, ValueError):
                    pass

        dy = data.get("daily_yield")
        if dy is not None:
            try:
                val = float(dy)
                if val > 0:
                    self.daily_yield.record(val, now)
            except (TypeError, ValueError):
                pass

        bat_cap = data.get("battery_work_capacity")
        if bat_cap is not None:
            try:
                self.battery_capacity.record(float(bat_cap), now)
            except (TypeError, ValueError):
                pass

    def update_battery_soh(self, soh: float) -> None:
        self.battery_soh.record(soh, time.time())

    def get_alerts(self) -> list[dict[str, str]]:
        """Return alerts for parameters with significant degradation."""
        alerts: list[dict[str, str]] = []

        for name, param in self.all_parameters.items():
            rate = param.degradation_rate_per_month
            dev = param.baseline_deviation_pct
            if rate is None:
                continue

            if name == "isolation" and rate < -50:
                alerts.append({
                    "parameter": param.name,
                    "severity": "warnung",
                    "message": f"Isolationswiderstand sinkt um {abs(rate):.0f} kΩ/Monat. "
                               "DC-Verkabelung und Stecker bei nächster Wartung prüfen.",
                    "rate": f"{rate:+.1f} {param.unit}/Monat",
                })

            elif name == "battery_soh" and rate < -0.5:
                alerts.append({
                    "parameter": param.name,
                    "severity": "warnung",
                    "message": f"Batterie-SoH sinkt um {abs(rate):.1f}%/Monat. "
                               "Installateur konsultieren wenn Trend anhält.",
                    "rate": f"{rate:+.1f} {param.unit}/Monat",
                })

            elif name == "controller_temp" and rate > 1.0:
                alerts.append({
                    "parameter": param.name,
                    "severity": "hinweis",
                    "message": f"Controller-Temperatur steigt um {rate:.1f}°C/Monat. "
                               "Lüfter und Belüftung prüfen.",
                    "rate": f"{rate:+.1f} {param.unit}/Monat",
                })

            elif name in ("dc1_peak", "dc2_peak") and dev is not None and dev < -10:
                alerts.append({
                    "parameter": param.name,
                    "severity": "hinweis",
                    "message": f"{param.name} ist {abs(dev):.0f}% unter Baseline. "
                               "Module reinigen oder auf Degradation prüfen.",
                    "rate": f"{rate:+.1f} {param.unit}/Monat",
                })

        return alerts

    def to_dict(self) -> dict[str, Any]:
        return {name: param.to_dict() for name, param in self.all_parameters.items()}

    def restore_from_dict(self, data: dict[str, Any]) -> None:
        for name, param in self.all_parameters.items():
            if name in data:
                try:
                    restored = TrackedParameter.from_dict(data[name])
                    param.snapshots = restored.snapshots
                    param._current_day = restored._current_day
                    param._current_snapshot = restored._current_snapshot
                    _LOGGER.debug(
                        "Restored %d snapshots for %s", len(param.snapshots), param.name
                    )
                except Exception as err:
                    _LOGGER.debug("Could not restore %s: %s", name, err)
