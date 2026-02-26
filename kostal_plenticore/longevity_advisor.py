"""Longevity advisor -- actionable tips to maximize system lifespan.

Analyzes long-term temperature, cycle, and usage patterns to provide
specific recommendations for extending equipment life. Tips are updated
based on accumulated data, not just current values.

Components monitored:
1. Battery: temperature history, cycle rate, depth of discharge
2. Inverter: controller temperature, workload patterns
3. PV Strings: degradation indicators
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Final

from .battery_chemistry import BatteryThresholds, detect_chemistry
from .health_monitor import InverterHealthMonitor

_LOGGER: Final = logging.getLogger(__name__)


@dataclass
class LongevityTip:
    """A single longevity recommendation."""

    component: str
    priority: str
    title: str
    detail: str
    action: str


class LongevityAdvisor:
    """Generates longevity tips based on accumulated health data."""

    def __init__(
        self,
        health: InverterHealthMonitor,
        battery_thresholds: BatteryThresholds,
    ) -> None:
        self._health = health
        self._bat_thresholds = battery_thresholds

    @property
    def battery_chemistry(self) -> str:
        return self._bat_thresholds.chemistry

    @property
    def battery_chemistry_full(self) -> str:
        return self._bat_thresholds.chemistry_full

    def get_tips(self) -> list[LongevityTip]:
        """Generate all applicable longevity tips."""
        tips: list[LongevityTip] = []
        tips.extend(self._battery_tips())
        tips.extend(self._inverter_tips())
        tips.extend(self._pv_tips())
        return tips

    def get_battery_temp_assessment(self) -> str:
        """Assess battery temperature for longevity."""
        temp = self._health.battery_temp.current
        bt = self._bat_thresholds
        if temp is None:
            return "Keine Temperaturdaten verfügbar."
        if temp <= bt.temp_optimal_max:
            return f"Optimal ({temp:.1f}°C ≤ {bt.temp_optimal_max}°C). Maximale Lebensdauer."
        if temp <= bt.temp_acceptable_max:
            return f"Akzeptabel ({temp:.1f}°C). Leichte Lebensdauereinbuße möglich."
        if temp <= bt.temp_warning_max:
            return f"Erhöht ({temp:.1f}°C > {bt.temp_acceptable_max}°C). Lebensdauer wird beeinträchtigt."
        return f"Kritisch ({temp:.1f}°C > {bt.temp_warning_max}°C). Erhebliche Alterung."

    def get_inverter_temp_assessment(self) -> str:
        """Assess inverter temperature for longevity."""
        temp = self._health.controller_temp.current
        if temp is None:
            return "Keine Temperaturdaten verfügbar."
        if temp <= 50:
            return f"Optimal ({temp:.1f}°C). Maximale Komponentenlebensdauer."
        if temp <= 65:
            return f"Normal ({temp:.1f}°C). Standardbetrieb."
        return f"Erhöht ({temp:.1f}°C). Belüftung prüfen für längere Lebensdauer."

    def _battery_tips(self) -> list[LongevityTip]:
        tips: list[LongevityTip] = []
        h = self._health
        bt = self._bat_thresholds

        avg_temp = h.battery_temp.avg_value
        if avg_temp is not None and avg_temp > bt.temp_optimal_max:
            tips.append(LongevityTip(
                "battery", "hoch",
                f"Batterie-Durchschnittstemperatur zu hoch ({avg_temp:.1f}°C)",
                f"Optimaler Bereich für {bt.chemistry}: unter {bt.temp_optimal_max}°C. "
                f"Aktuelle Durchschnittstemperatur: {avg_temp:.1f}°C.",
                "Aufstellort der Batterie überprüfen. Ein kühlerer Raum (Keller statt Dachboden) "
                "kann die Lebensdauer um Jahre verlängern. "
                f"{bt.longevity_tip}",
            ))

        max_temp = h.battery_temp.max_value
        if max_temp is not None and max_temp > bt.temp_acceptable_max:
            tips.append(LongevityTip(
                "battery", "mittel",
                f"Batterie erreicht regelmäßig {max_temp:.0f}°C",
                f"Spitzentemperatur überschreitet den akzeptablen Bereich ({bt.temp_acceptable_max}°C).",
                "Lüftung im Batterieraum verbessern. Ggf. zusätzliche Belüftung installieren "
                "oder Batterie von Wärmequellen entfernen.",
            ))

        cycles = h.battery_cycles.current
        if cycles is not None and cycles > bt.cycles_warning:
            tips.append(LongevityTip(
                "battery", "mittel",
                f"Batterie hat {int(cycles)} Zyklen erreicht",
                f"Erwartete Lebensdauer für {bt.chemistry}: {bt.cycles_good}-{bt.cycles_critical} Zyklen.",
                "Batterie-Kapazität beobachten. Bei deutlichem SoH-Rückgang Austausch planen.",
            ))

        soh = h.battery_soh.current
        if soh is not None:
            soh_trend = h.battery_soh.trend
            if soh < bt.soh_warning and soh_trend == "falling":
                tips.append(LongevityTip(
                    "battery", "hoch",
                    f"Batterie-Gesundheit fällt (SoH: {soh:.0f}%, Trend: {soh_trend})",
                    "Die Batteriekapazität sinkt schneller als erwartet.",
                    "Installateur konsultieren. Batteriestatus prüfen lassen. "
                    "Ggf. Zellspannungen und Balancing kontrollieren.",
                ))

        return tips

    def _inverter_tips(self) -> list[LongevityTip]:
        tips: list[LongevityTip] = []
        h = self._health

        avg_temp = h.controller_temp.avg_value
        if avg_temp is not None and avg_temp > 55:
            tips.append(LongevityTip(
                "inverter", "mittel",
                f"Wechselrichter-Durchschnittstemperatur: {avg_temp:.1f}°C",
                "Dauerhaft erhöhte Temperaturen verkürzen die Lebensdauer der Elektronik.",
                "Wechselrichter nicht in direkter Sonneneinstrahlung montieren. "
                "Lüftungsschlitze freihalten. Mindestabstände einhalten. "
                "Ggf. Montageort wechseln (Nordwand statt Südwand).",
            ))

        max_temp = h.controller_temp.max_value
        if max_temp is not None and max_temp > 70:
            tips.append(LongevityTip(
                "inverter", "hoch",
                f"Wechselrichter erreichte {max_temp:.0f}°C Spitzentemperatur",
                "Überhitzung führt zur automatischen Leistungsdrosselung und beschleunigt die Alterung.",
                "Belüftung verbessern. Lüfter und Kühlkörper auf Staub prüfen. "
                "Ggf. externe Belüftung installieren.",
            ))

        return tips

    def _pv_tips(self) -> list[LongevityTip]:
        tips: list[LongevityTip] = []
        h = self._health

        imbalance = h.dc_string_imbalance
        if imbalance is not None and imbalance > 20:
            tips.append(LongevityTip(
                "pv", "mittel",
                f"DC-Strings {imbalance:.0f}% ungleichmäßig",
                "Ungleichmäßige Leistung kann auf Verschmutzung, Verschattung oder Degradation hinweisen.",
                "Module reinigen. Verschattungsquellen entfernen (Bäume, Antennen). "
                "Bei anhaltender Differenz Modulzustand prüfen lassen.",
            ))

        iso = h.isolation.current
        iso_trend = h.isolation.trend
        if iso is not None and iso_trend == "falling" and iso < 1500:
            tips.append(LongevityTip(
                "pv", "hoch",
                f"Isolationswiderstand sinkt ({iso:.0f} kΩ, Trend: fallend)",
                "Sinkender Isolationswiderstand deutet auf Kabelalerung oder Feuchtigkeitseintritt hin.",
                "DC-Verkabelung bei nächster Wartung prüfen lassen. "
                "Anschlusskasten auf Feuchtigkeit kontrollieren. "
                "Kabeleinführungen und Tüllen auf Dichtigkeit prüfen.",
            ))

        return tips

    def get_summary(self) -> dict[str, Any]:
        """Return a complete longevity summary."""
        tips = self.get_tips()
        return {
            "battery_chemistry": self._bat_thresholds.chemistry,
            "battery_chemistry_full": self._bat_thresholds.chemistry_full,
            "battery_temp_assessment": self.get_battery_temp_assessment(),
            "inverter_temp_assessment": self.get_inverter_temp_assessment(),
            "tip_count": len(tips),
            "tips": [
                {
                    "component": t.component,
                    "priority": t.priority,
                    "title": t.title,
                    "action": t.action,
                }
                for t in tips
            ],
        }
