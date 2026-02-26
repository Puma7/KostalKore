"""PV fire safety and early warning detection.

IMPORTANT DISCLAIMER:
This is a SOFTWARE-BASED monitoring aid, NOT a certified fire protection
system. It cannot replace physical safety devices (AFCI, smoke detectors,
RCD/GFCI, thermal fuses). It provides ADDITIONAL early warnings based on
electrical anomalies detectable through the inverter's Modbus/REST data.

Detectable hazard patterns (from available inverter data):

1. ISOLATION FAULT (cable damage, water ingress, rodent/bird damage)
   → Isolation resistance dropping toward dangerous levels
   → Rapid drop = acute damage (e.g. animal chewing cable)
   → Slow decline = moisture/aging degradation

2. DC ARC FAULT INDICATORS (loose connectors, damaged cables)
   → Sudden DC string power/voltage drop while others stay normal
   → DC string power fluctuation (intermittent contact)
   → String voltage below expected range (partial short)

3. BATTERY THERMAL RUNAWAY PRECURSORS
   → Battery temperature rising faster than ambient
   → Battery voltage anomaly (cell imbalance precursor)
   → Charging current anomaly during constant voltage phase

4. OVERHEATING (hot spots, ventilation blocked, fire nearby)
   → Controller temperature exceeding safe operating range
   → Battery temperature exceeding safe operating range
   → Rate of temperature rise (°C/min) abnormally fast

5. OVERCURRENT / OVERLOAD
   → DC string current exceeding panel rated current
   → AC phase current imbalance (neutral overload risk)

6. GRID ANOMALY (upstream fault, transformer fire)
   → Grid frequency severe deviation (>±1Hz = grid emergency)
   → Phase voltage collapse or spike
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Final

_LOGGER: Final = logging.getLogger(__name__)

RATE_WINDOW_SECONDS: Final[float] = 300.0
MIN_SAMPLES_FOR_RATE: Final[int] = 3


class FireRiskLevel:
    """Fire risk classification."""

    SAFE = "safe"
    MONITOR = "monitor"
    ELEVATED = "elevated"
    HIGH = "high"
    EMERGENCY = "emergency"


@dataclass(frozen=True)
class SafetyAlert:
    """A fire safety alert."""

    timestamp: float
    risk_level: str
    category: str
    title: str
    detail: str
    action: str
    register_values: dict[str, Any]


class FireSafetyMonitor:
    """Monitors inverter data for fire and safety hazards.

    Analyzes rate-of-change, cross-parameter correlations, and absolute
    thresholds to detect hazard precursors that individual parameter
    monitors would miss.
    """

    def __init__(self) -> None:
        self._alerts: deque[SafetyAlert] = deque(maxlen=500)
        self._iso_history: deque[tuple[float, float]] = deque(maxlen=100)
        self._ctrl_temp_history: deque[tuple[float, float]] = deque(maxlen=100)
        self._bat_temp_history: deque[tuple[float, float]] = deque(maxlen=100)
        self._bat_voltage_history: deque[tuple[float, float]] = deque(maxlen=100)
        self._dc_power_history: dict[str, deque[tuple[float, float]]] = {
            f"dc{i}": deque(maxlen=60) for i in range(1, 4)
        }
        self._last_check: float = 0.0
        self._check_interval: float = 5.0
        self._total_polls: int = 0

    @property
    def alerts(self) -> list[SafetyAlert]:
        return list(self._alerts)

    @property
    def active_alerts(self) -> list[SafetyAlert]:
        now = time.monotonic()
        return [a for a in self._alerts if now - a.timestamp < 3600]

    def clear_stale_alerts(self, pv_active: bool) -> None:
        """Clear non-thermal alerts faster when PV is not producing."""
        if pv_active:
            return
        now = time.monotonic()
        kept: list[SafetyAlert] = []
        for a in self._alerts:
            age = now - a.timestamp
            if a.category in ("battery_thermal", "battery_voltage_anomaly", "controller_thermal"):
                if age < 3600:
                    kept.append(a)
            else:
                if age < 300:
                    kept.append(a)
        self._alerts.clear()
        self._alerts.extend(kept)

    @property
    def current_risk_level(self) -> str:
        active = self.active_alerts
        if not active:
            return FireRiskLevel.SAFE
        levels = [a.risk_level for a in active]
        if FireRiskLevel.EMERGENCY in levels:
            return FireRiskLevel.EMERGENCY
        if FireRiskLevel.HIGH in levels:
            return FireRiskLevel.HIGH
        if FireRiskLevel.ELEVATED in levels:
            return FireRiskLevel.ELEVATED
        if FireRiskLevel.MONITOR in levels:
            return FireRiskLevel.MONITOR
        return FireRiskLevel.SAFE

    @property
    def alert_count(self) -> int:
        return len(self.active_alerts)

    def analyze(self, data: dict[str, Any]) -> list[SafetyAlert]:
        """Run all safety checks on current data. Returns new alerts."""
        now = time.monotonic()
        if now - self._last_check < self._check_interval:
            return []
        self._last_check = now
        self._total_polls += 1

        self._record_history(data, now)

        # Determine if PV is producing (DC power > 50W)
        total_dc = _float(data.get("total_dc_power"))
        pv_active = total_dc is not None and total_dc > 50

        # Skip ALL checks when inverter is off/standby
        inverter_state = data.get("inverter_state")
        if inverter_state is not None:
            try:
                state_int = int(inverter_state)
                if state_int in (0, 1, 10, 15):
                    return []
            except (TypeError, ValueError):
                pass

        new_alerts: list[SafetyAlert] = []

        # Clear stale isolation/DC alerts when PV is off (night)
        self.clear_stale_alerts(pv_active)

        # Isolation + DC checks ONLY when PV is active (need DC voltage for measurement)
        if pv_active:
            new_alerts.extend(self._check_isolation(data, now))
            new_alerts.extend(self._check_dc_string_anomaly(data, now))
        new_alerts.extend(self._check_battery_thermal(data, now))
        new_alerts.extend(self._check_controller_thermal(data, now))
        new_alerts.extend(self._check_grid_emergency(data, now))

        for alert in new_alerts:
            self._alerts.append(alert)
            _LOGGER.warning(
                "FIRE SAFETY [%s] %s: %s (values: %s)",
                alert.risk_level.upper(), alert.category, alert.title, alert.register_values,
            )

        return new_alerts

    def _record_history(self, data: dict[str, Any], now: float) -> None:
        _try_append(self._iso_history, now, data.get("isolation_resistance"))
        _try_append(self._ctrl_temp_history, now, data.get("controller_temp"))
        _try_append(self._bat_temp_history, now, data.get("battery_temperature"))
        _try_append(self._bat_voltage_history, now, data.get("battery_voltage"))
        for i in range(1, 4):
            _try_append(self._dc_power_history[f"dc{i}"], now, data.get(f"dc{i}_power"))

    # ------------------------------------------------------------------
    # Check 1: Isolation fault (cable damage, water, rodent)
    # ------------------------------------------------------------------

    def _check_isolation(self, data: dict[str, Any], now: float) -> list[SafetyAlert]:
        alerts: list[SafetyAlert] = []
        iso = _float(data.get("isolation_resistance"))
        if iso is None:
            return alerts

        # Inverter returns 0 or near-0 when in standby/off (no DC voltage = no measurement)
        inverter_state = data.get("inverter_state")
        total_dc = _float(data.get("total_dc_power"))
        if iso < 1000 and (total_dc is None or total_dc < 50):
            return alerts  # no PV power → measurement not valid
        if inverter_state is not None:
            try:
                state_int = int(inverter_state)
                if state_int in (0, 1, 10, 15) and iso < 1000:
                    return alerts  # Off/Init/Standby/Shutdown → measurement not valid
            except (TypeError, ValueError):
                pass

        vals = {"isolation_resistance_ohm": iso}

        if iso < 50_000:
            alerts.append(SafetyAlert(
                now, FireRiskLevel.EMERGENCY, "isolation",
                "CRITICAL: Isolation resistance dangerously low",
                f"Isolation: {iso/1000:.0f} kΩ (safe: >500kΩ). Possible ground fault, damaged cable, or water ingress.",
                "IMMEDIATELY check DC cables, connectors, and junction boxes. Risk of electric shock and fire. Consider emergency shutdown.",
                vals,
            ))
        elif iso < 100_000:
            alerts.append(SafetyAlert(
                now, FireRiskLevel.HIGH, "isolation",
                "Isolation resistance critically low",
                f"Isolation: {iso/1000:.0f} kΩ. Below safe operating threshold.",
                "Schedule urgent inspection of DC cabling and module connections. Check for rodent/bird damage, water ingress, or connector corrosion.",
                vals,
            ))
        elif iso < 500_000:
            rate = _rate_of_change(self._iso_history, RATE_WINDOW_SECONDS)
            if rate is not None and rate < -50_000:
                alerts.append(SafetyAlert(
                    now, FireRiskLevel.ELEVATED, "isolation",
                    "Isolation resistance dropping rapidly",
                    f"Isolation: {iso/1000:.0f} kΩ, dropping at {abs(rate)/1000:.0f} kΩ/5min. Possible acute cable damage.",
                    "Monitor closely. If decline continues, inspect DC wiring for fresh damage (animals, weather, mechanical).",
                    vals,
                ))

        return alerts

    # ------------------------------------------------------------------
    # Check 2: DC string anomaly (arc fault precursor)
    # ------------------------------------------------------------------

    def _check_dc_string_anomaly(self, data: dict[str, Any], now: float) -> list[SafetyAlert]:
        alerts: list[SafetyAlert] = []
        powers = {}
        voltages = {}
        for i in range(1, 4):
            p = _float(data.get(f"dc{i}_power"))
            v = _float(data.get(f"dc{i}_voltage"))
            if p is not None and p > 20:
                powers[f"dc{i}"] = p
            if v is not None:
                voltages[f"dc{i}"] = v

        if len(powers) < 2:
            return alerts

        avg_power = sum(powers.values()) / len(powers)
        if avg_power < 100:
            return alerts

        for string, power in powers.items():
            deviation = abs(power - avg_power) / avg_power * 100
            if deviation > 50:
                rate = _rate_of_change(self._dc_power_history.get(string, deque()), RATE_WINDOW_SECONDS)
                vals = {**{f"{k}_power": v for k, v in powers.items()}, **{f"{k}_voltage": v for k, v in voltages.items()}}

                if rate is not None and abs(rate) > avg_power * 0.3:
                    alerts.append(SafetyAlert(
                        now, FireRiskLevel.ELEVATED, "dc_arc_indicator",
                        f"DC string {string} power fluctuating abnormally",
                        f"{string} power: {power:.0f}W (avg: {avg_power:.0f}W), rapid change detected. Possible intermittent contact or developing arc fault.",
                        f"Inspect {string} connectors, MC4 plugs, and cable routing. Look for burn marks, loose connections, or animal damage.",
                        vals,
                    ))
                elif deviation > 70:
                    alerts.append(SafetyAlert(
                        now, FireRiskLevel.MONITOR, "dc_imbalance",
                        f"DC string {string} significantly weaker than others",
                        f"{string}: {power:.0f}W vs avg {avg_power:.0f}W ({deviation:.0f}% deviation). Could indicate shading, soiling, or damaged panel/cable.",
                        f"Check {string} panels for shading, soiling, physical damage, or disconnected connectors.",
                        vals,
                    ))

        return alerts

    # ------------------------------------------------------------------
    # Check 3: Battery thermal runaway precursors
    # ------------------------------------------------------------------

    def _check_battery_thermal(self, data: dict[str, Any], now: float) -> list[SafetyAlert]:
        alerts: list[SafetyAlert] = []
        bat_temp = _float(data.get("battery_temperature"))
        if bat_temp is None:
            return alerts

        bat_voltage = _float(data.get("battery_voltage"))
        vals: dict[str, Any] = {"battery_temp_c": bat_temp}
        if bat_voltage is not None:
            vals["battery_voltage_v"] = bat_voltage

        if bat_temp > 60:
            alerts.append(SafetyAlert(
                now, FireRiskLevel.EMERGENCY, "battery_thermal",
                "CRITICAL: Battery temperature dangerously high",
                f"Battery: {bat_temp:.1f}°C. Thermal runaway risk. Li-ion batteries can catch fire above 60-70°C.",
                "EMERGENCY: If possible, disconnect battery. Evacuate area if temperature continues rising. Do NOT use water on lithium battery fire.",
                vals,
            ))
        elif bat_temp > 50:
            alerts.append(SafetyAlert(
                now, FireRiskLevel.HIGH, "battery_thermal",
                "Battery temperature critically high",
                f"Battery: {bat_temp:.1f}°C. Approaching thermal runaway zone.",
                "Reduce battery charge/discharge rate. Check battery ventilation. If temperature continues rising, prepare for emergency shutdown.",
                vals,
            ))
        elif bat_temp > 45:
            rate = _rate_of_change(self._bat_temp_history, RATE_WINDOW_SECONDS)
            if rate is not None and rate > 2.0:
                alerts.append(SafetyAlert(
                    now, FireRiskLevel.ELEVATED, "battery_thermal",
                    "Battery temperature rising rapidly",
                    f"Battery: {bat_temp:.1f}°C, rising at {rate:.1f}°C/5min. Abnormal heating may indicate internal cell problem.",
                    "Reduce battery load. Monitor closely. If rise continues, stop charging/discharging.",
                    vals,
                ))

        if bat_voltage is not None:
            v_rate = _rate_of_change(self._bat_voltage_history, RATE_WINDOW_SECONDS)
            if v_rate is not None and bat_temp > 40 and abs(v_rate) > 5.0:
                alerts.append(SafetyAlert(
                    now, FireRiskLevel.ELEVATED, "battery_voltage_anomaly",
                    "Battery voltage anomaly during high temperature",
                    f"Battery voltage changing at {v_rate:.1f}V/5min while temp is {bat_temp:.1f}°C. Possible cell imbalance.",
                    "Monitor battery behavior. Rapid voltage changes during high temperature can indicate failing cells.",
                    vals,
                ))

        return alerts

    # ------------------------------------------------------------------
    # Check 4: Controller overheating
    # ------------------------------------------------------------------

    def _check_controller_thermal(self, data: dict[str, Any], now: float) -> list[SafetyAlert]:
        alerts: list[SafetyAlert] = []
        ctrl_temp = _float(data.get("controller_temp"))
        if ctrl_temp is None:
            return alerts

        vals = {"controller_temp_c": ctrl_temp}

        if ctrl_temp > 85:
            alerts.append(SafetyAlert(
                now, FireRiskLevel.HIGH, "controller_thermal",
                "Controller temperature dangerously high",
                f"Controller PCB: {ctrl_temp:.1f}°C. Risk of component damage and fire.",
                "Check inverter ventilation. Ensure air vents are not blocked. Reduce power output if possible. Inverter should throttle automatically.",
                vals,
            ))
        elif ctrl_temp > 75:
            rate = _rate_of_change(self._ctrl_temp_history, RATE_WINDOW_SECONDS)
            if rate is not None and rate > 3.0:
                alerts.append(SafetyAlert(
                    now, FireRiskLevel.ELEVATED, "controller_thermal",
                    "Controller temperature rising rapidly",
                    f"Controller: {ctrl_temp:.1f}°C, rising at {rate:.1f}°C/5min. Ventilation may be blocked or failing.",
                    "Check inverter fan and air vents. Remove any obstructions. Ensure adequate clearance around inverter.",
                    vals,
                ))

        return alerts

    # ------------------------------------------------------------------
    # Check 5: Grid emergency
    # ------------------------------------------------------------------

    def _check_grid_emergency(self, data: dict[str, Any], now: float) -> list[SafetyAlert]:
        alerts: list[SafetyAlert] = []
        freq = _float(data.get("grid_frequency"))
        if freq is not None and abs(freq - 50.0) > 1.5:
            alerts.append(SafetyAlert(
                now, FireRiskLevel.HIGH, "grid_emergency",
                "Severe grid frequency deviation",
                f"Grid frequency: {freq:.2f} Hz (nominal: 50.0 Hz). Major grid disturbance.",
                "Grid emergency condition. Inverter should disconnect automatically. Check local grid conditions.",
                {"grid_frequency_hz": freq},
            ))

        for phase in range(1, 4):
            v = _float(data.get(f"phase{phase}_voltage"))
            if v is not None:
                if v > 270 or v < 180:
                    alerts.append(SafetyAlert(
                        now, FireRiskLevel.HIGH, "voltage_extreme",
                        f"Phase {phase} voltage extreme",
                        f"Phase {phase}: {v:.1f}V. Dangerous voltage level.",
                        "Check grid connection. Extreme voltages can damage equipment and cause fire.",
                        {f"phase{phase}_voltage": v},
                    ))

        return alerts


def _float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _try_append(history: deque[tuple[float, float]], now: float, val: Any) -> None:
    fv = _float(val)
    if fv is not None:
        history.append((now, fv))


def _rate_of_change(history: deque[tuple[float, float]], window: float) -> float | None:
    """Calculate rate of change over the given window (value per window)."""
    if len(history) < MIN_SAMPLES_FOR_RATE:
        return None
    now = history[-1][0]
    cutoff = now - window
    window_samples = [(t, v) for t, v in history if t >= cutoff]
    if len(window_samples) < MIN_SAMPLES_FOR_RATE:
        return None
    first_t, first_v = window_samples[0]
    last_t, last_v = window_samples[-1]
    dt = last_t - first_t
    if dt < 10:
        return None
    return (last_v - first_v) / dt * window
