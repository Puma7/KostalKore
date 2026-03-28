"""Targeted coverage tests for smaller Wave 2 files."""

from __future__ import annotations

import time
from types import SimpleNamespace

from kostal_plenticore.battery_chemistry import LFP_THRESHOLDS, NO_BATTERY_THRESHOLDS
from kostal_plenticore.diagnostics_engine import DiagStatus, DiagnosticsEngine
from kostal_plenticore.fire_safety import FireRiskLevel, FireSafetyMonitor, SafetyAlert
from kostal_plenticore.health_monitor import HealthLevel, InverterHealthMonitor, ParameterTracker
from kostal_plenticore.longevity_advisor import LongevityAdvisor
from kostal_plenticore.power_limits import (
    DEFAULT_CONTROL_LIMIT_W,
    get_device_power_limit_w,
    is_device_power_limit_known,
)


def _coord(limit: object) -> SimpleNamespace:
    return SimpleNamespace(device_info_data={"inverter_max_power": limit})


def _engine() -> DiagnosticsEngine:
    return DiagnosticsEngine(InverterHealthMonitor(), FireSafetyMonitor())


def test_power_limits_handle_missing_and_broken_metadata() -> None:
    """Power limit helpers should tolerate missing or malformed metadata."""
    assert get_device_power_limit_w(SimpleNamespace(device_info_data=None)) == DEFAULT_CONTROL_LIMIT_W
    assert get_device_power_limit_w(SimpleNamespace(device_info_data=1234)) == DEFAULT_CONTROL_LIMIT_W
    assert is_device_power_limit_known(SimpleNamespace(device_info_data=None)) is False
    assert is_device_power_limit_known(SimpleNamespace(device_info_data=1234)) is False
    assert is_device_power_limit_known(_coord("7000")) is True


def test_parameter_tracker_info_low_level() -> None:
    """ParameterTracker should expose INFO level for info_low threshold hits."""
    tracker = ParameterTracker(name="test", unit="V", info_low=10.0, warning_low=5.0)
    tracker.record(7.0)
    assert tracker.level == HealthLevel.INFO


def test_health_monitor_skips_invalid_isolation_and_zero_soh() -> None:
    """Health monitor should skip invalid isolation conversions and 0% SoH."""
    monitor = InverterHealthMonitor()
    monitor.update_from_modbus(
        {
            "isolation_resistance": float("nan"),
            "total_dc_power": 5000.0,
            "inverter_state": 0,
        }
    )
    monitor.update_from_modbus({"inverter_state": "not-an-int"})
    monitor.update_battery_soh(0.0)

    assert monitor.isolation.current is None
    assert monitor.battery_soh.current is None


def test_health_monitor_overall_health_info_and_health_score_deductions() -> None:
    """Overall health and score should reflect INFO-level trackers and imbalance."""
    monitor = InverterHealthMonitor()
    monitor.update_from_modbus(
        {
            "controller_temp": 64.0,
            "dc1_power": 5000.0,
            "dc2_power": 5000.0,
            "dc3_power": 3000.0,
            "total_dc_power": 13000.0,
        }
    )

    assert monitor.overall_health == HealthLevel.INFO
    assert monitor.health_score < 100


def test_diagnostics_engine_covers_remaining_small_branches() -> None:
    """Exercise smaller missing diagnosis branches across all areas."""
    engine = _engine()
    health = engine._health
    safety = engine._safety

    # DC critical path via high-risk manual alert.
    safety._alerts.append(
        SafetyAlert(
            timestamp=time.monotonic(),
            risk_level=FireRiskLevel.HIGH,
            category="dc_arc_indicator",
            title="Arc risk",
            detail="High risk detected",
            action="Check cables",
            register_values={},
        )
    )
    health.update_from_modbus({"dc1_power": 5000.0, "dc2_power": 1000.0, "dc1_voltage": 400.0})
    dc_diag = engine.diagnose_dc_solar()
    assert dc_diag.status == DiagStatus.KRITISCH

    # AC path with cos_phi in raw data.
    engine = _engine()
    engine._health.update_from_modbus(
        {
            "phase1_voltage": 230.0,
            "phase2_voltage": 231.0,
            "phase3_voltage": 229.0,
            "grid_frequency": 50.0,
            "cos_phi": 0.98,
        }
    )
    ac_diag = engine.diagnose_ac_grid()
    assert ac_diag.status == DiagStatus.OK
    assert "cos_phi" in ac_diag.raw_values

    # Battery path with cycles/voltage in raw data and unknown path.
    engine = _engine()
    battery_unknown = engine.diagnose_battery()
    assert battery_unknown.status == DiagStatus.UNBEKANNT
    engine._health.update_from_modbus({"battery_temperature": 25.0, "battery_cycles": 1200.0, "battery_voltage": 420.0})
    engine._health.update_battery_soh(95.0)
    battery_ok = engine.diagnose_battery()
    assert battery_ok.status == DiagStatus.OK
    assert "cycles" in battery_ok.raw_values
    assert "voltage_v" in battery_ok.raw_values

    # Inverter hint path for warm-but-not-critical temperature.
    engine = _engine()
    engine._health.update_from_modbus({"controller_temp": 68.0})
    inv_diag = engine.diagnose_inverter()
    assert inv_diag.status == DiagStatus.HINWEIS

    # Safety path: unknown and non-isolation HIGH.
    engine = _engine()
    assert engine.diagnose_safety().status == DiagStatus.UNBEKANNT
    engine = _engine()
    engine._health.update_from_modbus({"battery_temperature": 52.0})
    engine._safety._check_interval = 0.0
    engine._safety.analyze({"battery_temperature": 52.0})
    safety_diag = engine.diagnose_safety()
    assert safety_diag.status == DiagStatus.KRITISCH


def test_longevity_advisor_remaining_paths() -> None:
    """Cover remaining battery, inverter, and PV longevity branches."""
    no_battery = LongevityAdvisor(InverterHealthMonitor(), NO_BATTERY_THRESHOLDS)
    assert no_battery.battery_chemistry == "none"
    assert no_battery.battery_chemistry_full == NO_BATTERY_THRESHOLDS.chemistry_full
    assert no_battery.get_battery_temp_assessment() == "Keine Batterie installiert."
    assert no_battery.get_tips() == []

    battery_ok = InverterHealthMonitor()
    battery_ok.update_from_modbus({"battery_temperature": 35.0})
    assert "Akzeptabel" in LongevityAdvisor(battery_ok, LFP_THRESHOLDS).get_battery_temp_assessment()

    battery_warn = InverterHealthMonitor()
    battery_warn.update_from_modbus({"battery_temperature": 43.0})
    assert "43.0" in LongevityAdvisor(battery_warn, LFP_THRESHOLDS).get_battery_temp_assessment()

    battery_critical = InverterHealthMonitor()
    battery_critical.update_from_modbus({"battery_temperature": 60.0})
    assert "Kritisch" in LongevityAdvisor(battery_critical, LFP_THRESHOLDS).get_battery_temp_assessment()

    inverter_empty = InverterHealthMonitor()
    assert "Keine Temperaturdaten" in LongevityAdvisor(inverter_empty, LFP_THRESHOLDS).get_inverter_temp_assessment()

    inverter_normal = InverterHealthMonitor()
    inverter_normal.update_from_modbus({"controller_temp": 60.0})
    assert "Normal" in LongevityAdvisor(inverter_normal, LFP_THRESHOLDS).get_inverter_temp_assessment()

    inverter_hot = InverterHealthMonitor()
    inverter_hot.update_from_modbus({"controller_temp": 72.0})
    assert "72.0" in LongevityAdvisor(inverter_hot, LFP_THRESHOLDS).get_inverter_temp_assessment()

    # Trigger remaining tip branches: battery max temp, falling SoH, inverter peak, PV isolation.
    rich_health = InverterHealthMonitor()
    for temp in range(20, 45):
        rich_health.update_from_modbus({"battery_temperature": float(temp)})
    for soh in range(84, 73, -1):
        rich_health.update_battery_soh(float(soh))
    for temp in (45.0, 72.0, 75.0):
        rich_health.update_from_modbus({"controller_temp": temp})
    for iso in (
        2_000_000.0,
        1_950_000.0,
        1_900_000.0,
        1_850_000.0,
        1_800_000.0,
        1_750_000.0,
        1_700_000.0,
        1_650_000.0,
        1_600_000.0,
        1_500_000.0,
        1_400_000.0,
        1_200_000.0,
    ):
        rich_health.update_from_modbus({"isolation_resistance": iso, "total_dc_power": 5000.0})

    rich_advisor = LongevityAdvisor(rich_health, LFP_THRESHOLDS)
    tips = rich_advisor.get_tips()

    assert any("erreicht" in tip.title or "Temperatur" in tip.title for tip in tips)
    assert any("Gesundheit" in tip.title for tip in tips)
    assert any(tip.component == "inverter" and tip.priority == "hoch" for tip in tips)
    assert any("Isolationswiderstand" in tip.title for tip in tips)
