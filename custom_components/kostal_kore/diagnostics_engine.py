"""Smart diagnostics engine with per-area assessments and actionable advice.

Provides human-readable diagnostic status per subsystem area. Each area
gets an independent diagnosis with:
- Status: ok / hinweis / warnung / kritisch
- Plain-language description of what was detected
- Actionable recommendation (what to check, who to call)
- Raw values for professionals

Areas:
1. DC Solar (PV strings, panels, MC4 connectors, cables)
2. AC Grid (phases, voltage, frequency, power factor)
3. Battery (temperature, SoH, cycles, voltage, capacity)
4. Inverter (controller temp, state, errors, worktime)
5. Safety (isolation, fire risk, communication)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Final

from .health_monitor import HealthLevel, InverterHealthMonitor
from .fire_safety import FireRiskLevel, FireSafetyMonitor

_LOGGER: Final = logging.getLogger(__name__)


class DiagStatus:
    """Diagnostic status levels."""

    OK = "ok"
    HINWEIS = "hinweis"
    WARNUNG = "warnung"
    KRITISCH = "kritisch"


@dataclass
class AreaDiagnosis:
    """Diagnosis result for one subsystem area."""

    area: str
    status: str
    title: str
    detail: str
    action: str
    raw_values: dict[str, Any]


class DiagnosticsEngine:
    """Generates per-area diagnoses from health and safety monitors."""

    def __init__(
        self,
        health: InverterHealthMonitor,
        safety: FireSafetyMonitor,
    ) -> None:
        self._health = health
        self._safety = safety

    def diagnose_all(self) -> dict[str, AreaDiagnosis]:
        """Run all area diagnostics and return results."""
        return {
            "dc_solar": self.diagnose_dc_solar(),
            "ac_grid": self.diagnose_ac_grid(),
            "battery": self.diagnose_battery(),
            "inverter": self.diagnose_inverter(),
            "safety": self.diagnose_safety(),
        }

    # ------------------------------------------------------------------
    # DC Solar (Strings, Panels, Kabel, MC4-Stecker)
    # ------------------------------------------------------------------

    def diagnose_dc_solar(self) -> AreaDiagnosis:
        h = self._health
        raw: dict[str, Any] = {}

        max_pv_dc = 2 if h._num_bidirectional >= 1 else 3
        for i in range(1, max_pv_dc + 1):
            p = getattr(h, f"dc{i}_power").current
            v = getattr(h, f"dc{i}_voltage").current
            if p is not None:
                raw[f"dc{i}_power_w"] = round(p, 0)
            if v is not None:
                raw[f"dc{i}_voltage_v"] = round(v, 1)

        imbalance = h.dc_string_imbalance
        if imbalance is not None:
            raw["imbalance_pct"] = round(imbalance, 1)

        dc_arc_alerts = [
            a for a in self._safety.active_alerts
            if a.category in ("dc_arc_indicator", "dc_imbalance")
        ]

        if dc_arc_alerts:
            worst = dc_arc_alerts[0]
            if worst.risk_level in (FireRiskLevel.HIGH, FireRiskLevel.EMERGENCY):
                return AreaDiagnosis(
                    "dc_solar", DiagStatus.KRITISCH,
                    "Möglicher Kabelfehler im DC-Bereich",
                    f"Auffällige Leistungsschwankungen an einem DC-String. {worst.detail}",
                    "Bitte ALLE MC4-Stecker, Kabelverbindungen und den Anschlusskasten prüfen. "
                    "Auf Brandspuren, lose Verbindungen oder Tierbiss achten. "
                    "Im Zweifelsfall Solarteur kontaktieren.",
                    raw,
                )
            return AreaDiagnosis(
                "dc_solar", DiagStatus.WARNUNG,
                "DC-String Leistungsunterschied auffällig",
                f"Ein DC-String liefert deutlich weniger als die anderen. {worst.detail}",
                "Mögliche Ursachen: Verschattung, verschmutzte Module, lockerer MC4-Stecker, "
                "beschädigtes Kabel. Bitte String-Anschlüsse und Module visuell prüfen.",
                raw,
            )

        if imbalance is not None and imbalance > 40:
            return AreaDiagnosis(
                "dc_solar", DiagStatus.HINWEIS,
                "DC-Strings leicht ungleichmäßig",
                f"Leistungsunterschied zwischen den Strings: {imbalance:.0f}%.",
                "Prüfen ob ein String verschattet oder verschmutzt ist. "
                "Bei gleichmäßiger Besonnung alle MC4-Stecker auf festen Sitz prüfen.",
                raw,
            )

        return AreaDiagnosis(
            "dc_solar", DiagStatus.OK,
            "DC-Solaranlage arbeitet normal",
            "Alle DC-Strings liefern gleichmäßige Leistung.",
            "Keine Aktion erforderlich.",
            raw,
        )

    # ------------------------------------------------------------------
    # AC Grid (Netz, Phasen, Spannung, Frequenz)
    # ------------------------------------------------------------------

    def diagnose_ac_grid(self) -> AreaDiagnosis:
        h = self._health
        raw: dict[str, Any] = {}

        for i in range(1, 4):
            v = getattr(h, f"phase{i}_voltage").current
            if v is not None:
                raw[f"phase{i}_voltage_v"] = round(v, 1)

        freq = h.grid_frequency.current
        if freq is not None:
            raw["frequency_hz"] = round(freq, 2)

        phi = h.cos_phi.current
        if phi is not None:
            raw["cos_phi"] = round(phi, 3)

        phase_imb = h.phase_voltage_imbalance
        if phase_imb is not None:
            raw["phase_imbalance_pct"] = round(phase_imb, 1)

        nominal_freq = _detect_nominal_frequency_hz(freq)
        phase_samples = [
            v
            for v in (
                h.phase1_voltage.current,
                h.phase2_voltage.current,
                h.phase3_voltage.current,
            )
            if v is not None
        ]
        nominal_voltage = _detect_nominal_phase_voltage_v(phase_samples)
        if nominal_voltage <= 130.0:
            normal_low, normal_high = 108.0, 132.0
            warning_low, warning_high = 102.0, 138.0
        else:
            normal_low, normal_high = 207.0, 253.0
            warning_low, warning_high = 195.0, 260.0

        if freq is not None and abs(freq - nominal_freq) > 1.0:
            return AreaDiagnosis(
                "ac_grid", DiagStatus.KRITISCH,
                "Netzfrequenz stark abweichend",
                (
                    f"Netzfrequenz: {freq:.2f} Hz "
                    f"(normal: {nominal_freq:.1f} Hz). Mögliche Netzstörung."
                ),
                "Netzversorger kontaktieren. Wechselrichter sollte sich automatisch vom Netz trennen.",
                raw,
            )

        voltage_issues = []
        for i in range(1, 4):
            v = getattr(h, f"phase{i}_voltage").current
            if v is not None:
                if v > normal_high or v < normal_low:
                    voltage_issues.append((i, v))

        if voltage_issues:
            phases = ", ".join(f"L{i}: {v:.0f}V" for i, v in voltage_issues)
            if any(v > warning_high or v < warning_low for _, v in voltage_issues):
                return AreaDiagnosis(
                    "ac_grid", DiagStatus.WARNUNG,
                    "Netzspannung außerhalb des Normalbereichs",
                    (
                        f"Auffällige Spannungswerte: {phases}. "
                        f"Normbereich: {normal_low:.0f}-{normal_high:.0f}V."
                    ),
                    "Netzversorger informieren. Ggf. Überspannungsschutz prüfen. "
                    "Bei dauerhafter Abweichung Elektriker hinzuziehen.",
                    raw,
                )
            return AreaDiagnosis(
                "ac_grid", DiagStatus.HINWEIS,
                "Netzspannung leicht auffällig",
                f"Spannungswerte am Rand des Normalbereichs: {phases}.",
                "Situation beobachten. Bei häufigem Auftreten Netzversorger kontaktieren.",
                raw,
            )

        return AreaDiagnosis(
            "ac_grid", DiagStatus.OK,
            "Netzanbindung normal",
            "Spannung, Frequenz und Leistungsfaktor im Normalbereich.",
            "Keine Aktion erforderlich.",
            raw,
        )

    # ------------------------------------------------------------------
    # Battery
    # ------------------------------------------------------------------

    def diagnose_battery(self) -> AreaDiagnosis:
        h = self._health
        raw: dict[str, Any] = {}

        temp = h.battery_temp.current
        soh = h.battery_soh.current
        cycles = h.battery_cycles.current
        voltage = h.battery_voltage.current

        if temp is not None:
            raw["temperature_c"] = round(temp, 1)
        if soh is not None:
            raw["soh_pct"] = round(soh, 1)
        if cycles is not None:
            raw["cycles"] = round(cycles, 0)
        if voltage is not None:
            raw["voltage_v"] = round(voltage, 1)

        raw["temp_trend"] = h.battery_temp.trend
        raw["soh_trend"] = h.battery_soh.trend

        bat_fire_alerts = [
            a for a in self._safety.active_alerts
            if a.category in ("battery_thermal", "battery_voltage_anomaly")
        ]

        if bat_fire_alerts and any(a.risk_level in (FireRiskLevel.HIGH, FireRiskLevel.EMERGENCY) for a in bat_fire_alerts):
            return AreaDiagnosis(
                "battery", DiagStatus.KRITISCH,
                "Batterie zeigt kritische Werte",
                f"Temperatur: {temp:.1f}°C. " + bat_fire_alerts[0].detail,
                "SOFORT: Batterieladung/-entladung stoppen wenn möglich. "
                "Raum belüften. Bei Rauchentwicklung Gebäude verlassen und Feuerwehr rufen. "
                "KEIN Wasser auf Lithium-Batterien!",
                raw,
            )

        if temp is not None and temp > 42:
            return AreaDiagnosis(
                "battery", DiagStatus.WARNUNG,
                "Batterietemperatur erhöht",
                f"Batterietemperatur: {temp:.1f}°C (normal: unter 35°C).",
                "Batteriebelüftung prüfen. Ladeleistung ggf. reduzieren. "
                "Wenn Temperatur weiter steigt, Installateur kontaktieren.",
                raw,
            )

        if soh is not None and soh < 80:
            return AreaDiagnosis(
                "battery", DiagStatus.WARNUNG,
                "Batterie-Gesundheit nachlassend",
                f"State of Health: {soh:.0f}% (neu: 100%). Die Batterie verliert an Kapazität.",
                "Ladezyklen beobachten. Bei SoH unter 70% Batterietausch mit Installateur besprechen.",
                raw,
            )

        if soh is not None and soh < 90:
            return AreaDiagnosis(
                "battery", DiagStatus.HINWEIS,
                "Batterie zeigt normale Alterung",
                f"State of Health: {soh:.0f}%. Leichter Kapazitätsverlust ist normal.",
                "Keine Aktion erforderlich. SoH-Trend langfristig beobachten.",
                raw,
            )

        return AreaDiagnosis(
            "battery", DiagStatus.OK,
            "Batterie arbeitet normal",
            "Temperatur, Gesundheit und Spannung im Normalbereich.",
            "Keine Aktion erforderlich.",
            raw,
        )

    # ------------------------------------------------------------------
    # Inverter
    # ------------------------------------------------------------------

    def diagnose_inverter(self) -> AreaDiagnosis:
        h = self._health
        raw: dict[str, Any] = {}

        ctrl_temp = h.controller_temp.current
        if ctrl_temp is not None:
            raw["controller_temp_c"] = round(ctrl_temp, 1)
            raw["controller_temp_peak"] = h.controller_temp.max_value

        errors = h.active_error_count.current
        warnings = h.active_warning_count.current
        if errors is not None:
            raw["active_errors"] = int(errors)
        if warnings is not None:
            raw["active_warnings"] = int(warnings)

        raw["state_changes"] = h.state_change_count
        raw["comm_reliability_pct"] = round(h.communication_reliability, 1)
        raw["error_rate_per_hour"] = round(h.error_rate_per_hour, 1)

        if ctrl_temp is not None and ctrl_temp > 78:
            return AreaDiagnosis(
                "inverter", DiagStatus.KRITISCH,
                "Wechselrichter überhitzt",
                f"Controller-Temperatur: {ctrl_temp:.1f}°C (max: 80°C).",
                "Sofort Belüftung des Wechselrichters prüfen. Lüftungsschlitze frei? "
                "Direkte Sonneneinstrahlung auf das Gerät? Leistung wird automatisch gedrosselt.",
                raw,
            )

        if errors is not None and errors > 0:
            return AreaDiagnosis(
                "inverter", DiagStatus.WARNUNG,
                f"Wechselrichter meldet {int(errors)} aktive Fehler",
                f"{int(errors)} Fehler und {int(warnings or 0)} Warnungen aktiv.",
                "Fehlerspeicher im Wechselrichter-Webinterface prüfen. "
                "Bei wiederkehrenden Fehlern Installateur kontaktieren.",
                raw,
            )

        if ctrl_temp is not None and ctrl_temp > 65:
            return AreaDiagnosis(
                "inverter", DiagStatus.HINWEIS,
                "Wechselrichter-Temperatur leicht erhöht",
                f"Controller: {ctrl_temp:.1f}°C. Im oberen Normalbereich.",
                "Belüftung prüfen. An heißen Sommertagen normal.",
                raw,
            )

        if h.communication_reliability < 90:
            return AreaDiagnosis(
                "inverter", DiagStatus.HINWEIS,
                "Kommunikationsqualität eingeschränkt",
                f"Nur {h.communication_reliability:.0f}% der Modbus-Abfragen erfolgreich.",
                "Netzwerkverbindung zum Wechselrichter prüfen. LAN-Kabel und Switch kontrollieren.",
                raw,
            )

        return AreaDiagnosis(
            "inverter", DiagStatus.OK,
            "Wechselrichter arbeitet normal",
            "Temperatur, Fehlerstatus und Kommunikation im Normalbereich.",
            "Keine Aktion erforderlich.",
            raw,
        )

    # ------------------------------------------------------------------
    # Safety (Gesamtsicherheit)
    # ------------------------------------------------------------------

    def diagnose_safety(self) -> AreaDiagnosis:
        h = self._health
        s = self._safety
        raw: dict[str, Any] = {}

        iso = h.isolation.current
        if iso is not None:
            raw["isolation_kohm"] = round(iso, 0)
            raw["isolation_trend"] = h.isolation.trend

        raw["fire_risk_level"] = s.current_risk_level
        raw["active_safety_alerts"] = s.alert_count

        iso_alerts = [a for a in s.active_alerts if a.category == "isolation"]

        if s.current_risk_level == FireRiskLevel.EMERGENCY:
            return AreaDiagnosis(
                "safety", DiagStatus.KRITISCH,
                "SICHERHEITSWARNUNG: Sofortiges Handeln erforderlich",
                f"Risikostufe: NOTFALL. {s.active_alerts[0].detail if s.active_alerts else ''}",
                s.active_alerts[0].action if s.active_alerts else "Anlage prüfen und ggf. abschalten.",
                raw,
            )

        if iso_alerts:
            worst = iso_alerts[0]
            if worst.risk_level == FireRiskLevel.HIGH:
                return AreaDiagnosis(
                    "safety", DiagStatus.KRITISCH,
                    "Isolationsfehler erkannt",
                    f"Isolationswiderstand: {iso:.0f} kΩ (sicher: >500kΩ). "
                    "Mögliche Ursachen: beschädigtes Kabel, Wasser im Anschlusskasten, Tierbiss.",
                    "DC-Kabel und Stecker auf Beschädigungen prüfen. Anschlusskasten öffnen und "
                    "auf Feuchtigkeit, Korrosion oder Bissspuren kontrollieren. "
                    "Solarteur oder Elektriker hinzuziehen.",
                    raw,
                )

        if iso is not None and iso < 800:
            return AreaDiagnosis(
                "safety", DiagStatus.HINWEIS,
                "Isolationswiderstand leicht unter Optimalwert",
                f"Isolationswiderstand: {iso:.0f} kΩ. Sicher, aber nicht optimal (>1000kΩ ideal).",
                "Bei nächster Wartung DC-Kabel und Stecker prüfen lassen.",
                raw,
            )

        return AreaDiagnosis(
            "safety", DiagStatus.OK,
            "Sicherheitssysteme normal",
            "Isolationswiderstand, Brandschutzüberwachung und Kommunikation in Ordnung.",
            "Keine Aktion erforderlich.",
            raw,
        )


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
