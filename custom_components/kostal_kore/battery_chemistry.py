"""Battery chemistry detection and per-chemistry health thresholds.

Detects the battery type from Modbus register 588 and provides
chemistry-specific temperature thresholds for health and longevity.

Supported chemistries:
- LFP (LiFePO4): BYD, Pyontech, VARTA, most safe
- NMC (Li-ion NMC): LG, BMZ, most common but less heat-tolerant
- Unknown: conservative defaults

Threshold philosophy:
- OPTIMAL: temperature range for maximum battery lifespan (>15 years)
- ACCEPTABLE: normal operation, slight lifespan impact
- WARNING: lifespan degradation accelerated, user should act
- CRITICAL: safety risk, immediate action needed
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .modbus_registers import BATTERY_TYPES


@dataclass(frozen=True)
class BatteryThresholds:
    """Temperature thresholds for a specific battery chemistry."""

    chemistry: str
    chemistry_full: str

    temp_optimal_max: float
    temp_acceptable_max: float
    temp_warning_max: float
    temp_critical_max: float

    temp_optimal_min: float
    temp_acceptable_min: float

    cycles_good: int
    cycles_warning: int
    cycles_critical: int

    soh_info: float
    soh_warning: float
    soh_critical: float

    longevity_tip: str


LFP_THRESHOLDS: Final = BatteryThresholds(
    chemistry="LFP",
    chemistry_full="Lithium Iron Phosphate (LiFePO4)",
    temp_optimal_max=30.0,
    temp_acceptable_max=40.0,
    temp_warning_max=50.0,
    temp_critical_max=60.0,
    temp_optimal_min=10.0,
    temp_acceptable_min=0.0,
    cycles_good=4000,
    cycles_warning=6000,
    cycles_critical=8000,
    soh_info=90.0,
    soh_warning=80.0,
    soh_critical=60.0,
    longevity_tip=(
        "LFP-Batterien sind sehr langlebig (6000+ Zyklen). "
        "Optimale Temperatur: 15-30°C. "
        "Vermeiden Sie dauerhafte Temperaturen über 35°C. "
        "Kühler Aufstellort (Keller, Garage) verlängert die Lebensdauer erheblich."
    ),
)

NMC_THRESHOLDS: Final = BatteryThresholds(
    chemistry="NMC",
    chemistry_full="Lithium Nickel Manganese Cobalt (NMC)",
    temp_optimal_max=25.0,
    temp_acceptable_max=35.0,
    temp_warning_max=45.0,
    temp_critical_max=55.0,
    temp_optimal_min=10.0,
    temp_acceptable_min=0.0,
    cycles_good=3000,
    cycles_warning=4000,
    cycles_critical=6000,
    soh_info=90.0,
    soh_warning=80.0,
    soh_critical=60.0,
    longevity_tip=(
        "NMC-Batterien sind hitzeempfindlicher als LFP. "
        "Optimale Temperatur: 15-25°C. "
        "Jedes Grad über 25°C beschleunigt die Alterung. "
        "Kühler, gut belüfteter Aufstellort ist besonders wichtig."
    ),
)

CONSERVATIVE_THRESHOLDS: Final = BatteryThresholds(
    chemistry="Unknown",
    chemistry_full="Unknown Chemistry (conservative limits)",
    temp_optimal_max=25.0,
    temp_acceptable_max=35.0,
    temp_warning_max=45.0,
    temp_critical_max=55.0,
    temp_optimal_min=10.0,
    temp_acceptable_min=0.0,
    cycles_good=2500,
    cycles_warning=4000,
    cycles_critical=6000,
    soh_info=90.0,
    soh_warning=80.0,
    soh_critical=60.0,
    longevity_tip=(
        "Batteriechemie nicht erkannt -- konservative Grenzwerte aktiv. "
        "Generell gilt: kühlerer Aufstellort = längere Lebensdauer. "
        "Optimale Temperatur: 15-25°C."
    ),
)

NO_BATTERY_THRESHOLDS: Final = BatteryThresholds(
    chemistry="none",
    chemistry_full="No battery installed",
    temp_optimal_max=0.0,
    temp_acceptable_max=0.0,
    temp_warning_max=0.0,
    temp_critical_max=0.0,
    temp_optimal_min=0.0,
    temp_acceptable_min=0.0,
    cycles_good=0,
    cycles_warning=0,
    cycles_critical=0,
    soh_info=0.0,
    soh_warning=0.0,
    soh_critical=0.0,
    longevity_tip="Keine Batterie installiert.",
)

_TYPE_TO_CHEMISTRY: Final[dict[int, str]] = {
    0x0004: "LFP",
    0x0200: "LFP",
    0x2000: "LFP",
    0x0040: "NMC",
    0x0008: "NMC",
    0x0010: "NMC",
    0x1000: "LFP",
    0x4000: "LFP",
    0x0002: "NMC",
    0x0400: "LFP",
}


def detect_chemistry(battery_type_code: int | None) -> BatteryThresholds:
    """Detect battery chemistry from Modbus register 588 value.

    Returns NO_BATTERY_THRESHOLDS for 0x0000 ('No battery'),
    CONSERVATIVE_THRESHOLDS for None (register not read yet).
    """
    if battery_type_code is None:
        return CONSERVATIVE_THRESHOLDS
    if battery_type_code == 0:
        return NO_BATTERY_THRESHOLDS

    chem = _TYPE_TO_CHEMISTRY.get(battery_type_code)
    if chem == "LFP":
        return LFP_THRESHOLDS
    if chem == "NMC":
        return NMC_THRESHOLDS
    return CONSERVATIVE_THRESHOLDS


def get_battery_brand(battery_type_code: int | None) -> str:
    """Get human-readable battery brand from type code."""
    if battery_type_code is None:
        return "Unknown"
    return BATTERY_TYPES.get(battery_type_code, f"Unknown (0x{battery_type_code:04X})")
