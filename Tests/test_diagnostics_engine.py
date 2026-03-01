"""Tests for DiagnosticsEngine per-area diagnoses."""

from __future__ import annotations

from kostal_plenticore.diagnostics_engine import (
    DiagnosticsEngine,
    DiagStatus,
)
from kostal_plenticore.health_monitor import InverterHealthMonitor
from kostal_plenticore.fire_safety import FireSafetyMonitor


def _make_engine() -> DiagnosticsEngine:
    return DiagnosticsEngine(InverterHealthMonitor(), FireSafetyMonitor())


class TestDCDiagnostics:

    def test_dc_ok_when_balanced(self) -> None:
        e = _make_engine()
        e._health.update_from_modbus({"dc1_power": 5000.0, "dc2_power": 4800.0})
        d = e.diagnose_dc_solar()
        assert d.status == DiagStatus.OK

    def test_dc_hinweis_on_imbalance(self) -> None:
        e = _make_engine()
        e._health.update_from_modbus({"dc1_power": 5000.0, "dc2_power": 5000.0, "dc3_power": 2000.0})
        d = e.diagnose_dc_solar()
        assert d.status == DiagStatus.HINWEIS
        assert "MC4" in d.action or "String" in d.action or "verschattet" in d.action

    def test_dc_warnung_on_arc_indicator(self) -> None:
        e = _make_engine()
        e._safety._check_interval = 0.0
        e._safety.analyze({"dc1_power": 5000.0, "dc2_power": 5000.0, "dc3_power": 500.0, "total_dc_power": 10500.0, "inverter_state": 6})
        d = e.diagnose_dc_solar()
        assert d.status in (DiagStatus.WARNUNG, DiagStatus.KRITISCH, DiagStatus.HINWEIS)

    def test_dc_ok_when_no_data(self) -> None:
        e = _make_engine()
        d = e.diagnose_dc_solar()
        assert d.status == DiagStatus.OK


class TestACDiagnostics:

    def test_ac_ok_normal(self) -> None:
        e = _make_engine()
        e._health.update_from_modbus({
            "phase1_voltage": 230.0, "phase2_voltage": 231.0,
            "phase3_voltage": 229.0, "grid_frequency": 50.0,
        })
        d = e.diagnose_ac_grid()
        assert d.status == DiagStatus.OK

    def test_ac_kritisch_frequency(self) -> None:
        e = _make_engine()
        e._health.update_from_modbus({"grid_frequency": 48.5})
        d = e.diagnose_ac_grid()
        assert d.status == DiagStatus.KRITISCH

    def test_ac_ok_60hz_profile(self) -> None:
        e = _make_engine()
        e._health.update_from_modbus({"grid_frequency": 60.1})
        d = e.diagnose_ac_grid()
        assert d.status == DiagStatus.OK

    def test_ac_warnung_voltage(self) -> None:
        e = _make_engine()
        e._health.update_from_modbus({"phase1_voltage": 262.0, "phase2_voltage": 230.0, "phase3_voltage": 230.0})
        d = e.diagnose_ac_grid()
        assert d.status == DiagStatus.WARNUNG

    def test_ac_hinweis_borderline_voltage(self) -> None:
        e = _make_engine()
        e._health.update_from_modbus({"phase1_voltage": 254.0, "phase2_voltage": 230.0, "phase3_voltage": 230.0})
        d = e.diagnose_ac_grid()
        assert d.status == DiagStatus.HINWEIS

    def test_ac_ok_120v_profile(self) -> None:
        e = _make_engine()
        e._health.update_from_modbus(
            {"phase1_voltage": 120.0, "phase2_voltage": 122.0, "phase3_voltage": 119.0}
        )
        d = e.diagnose_ac_grid()
        assert d.status == DiagStatus.OK


class TestBatteryDiagnostics:

    def test_battery_ok(self) -> None:
        e = _make_engine()
        e._health.update_from_modbus({"battery_temperature": 25.0})
        e._health.update_battery_soh(95.0)
        d = e.diagnose_battery()
        assert d.status == DiagStatus.OK

    def test_battery_warnung_temp(self) -> None:
        e = _make_engine()
        e._health.update_from_modbus({"battery_temperature": 43.0})
        d = e.diagnose_battery()
        assert d.status == DiagStatus.WARNUNG

    def test_battery_kritisch_fire(self) -> None:
        e = _make_engine()
        e._health.update_from_modbus({"battery_temperature": 62.0})
        e._safety._check_interval = 0.0
        e._safety.analyze({"battery_temperature": 62.0})
        d = e.diagnose_battery()
        assert d.status == DiagStatus.KRITISCH
        assert "Lithium" in d.action or "SOFORT" in d.action or "Feuerwehr" in d.action

    def test_battery_hinweis_soh(self) -> None:
        e = _make_engine()
        e._health.update_from_modbus({"battery_temperature": 25.0})
        e._health.update_battery_soh(88.0)
        d = e.diagnose_battery()
        assert d.status == DiagStatus.HINWEIS

    def test_battery_warnung_soh(self) -> None:
        e = _make_engine()
        e._health.update_from_modbus({"battery_temperature": 25.0})
        e._health.update_battery_soh(75.0)
        d = e.diagnose_battery()
        assert d.status == DiagStatus.WARNUNG


class TestInverterDiagnostics:

    def test_inverter_ok(self) -> None:
        e = _make_engine()
        e._health.update_from_modbus({"controller_temp": 45.0})
        e._health._total_polls = 100
        d = e.diagnose_inverter()
        assert d.status == DiagStatus.OK

    def test_inverter_kritisch_overheat(self) -> None:
        e = _make_engine()
        e._health.update_from_modbus({"controller_temp": 82.0})
        d = e.diagnose_inverter()
        assert d.status == DiagStatus.KRITISCH

    def test_inverter_warnung_errors(self) -> None:
        e = _make_engine()
        e._health.update_from_modbus({"controller_temp": 40.0})
        e._health.update_error_counts(3, 1)
        d = e.diagnose_inverter()
        assert d.status == DiagStatus.WARNUNG

    def test_inverter_hinweis_comm(self) -> None:
        e = _make_engine()
        e._health.update_from_modbus({"controller_temp": 40.0})
        e._health._total_polls = 100
        e._health._failed_polls = 15
        d = e.diagnose_inverter()
        assert d.status == DiagStatus.HINWEIS


class TestSafetyDiagnostics:

    def test_safety_ok(self) -> None:
        e = _make_engine()
        e._health.update_from_modbus({"isolation_resistance": 2000000.0})
        d = e.diagnose_safety()
        assert d.status == DiagStatus.OK

    def test_safety_kritisch_isolation(self) -> None:
        e = _make_engine()
        e._health.update_from_modbus({"isolation_resistance": 80000.0})
        e._safety._check_interval = 0.0
        e._safety.analyze({"isolation_resistance": 80000.0, "total_dc_power": 5000.0, "inverter_state": 6})
        d = e.diagnose_safety()
        assert d.status == DiagStatus.KRITISCH

    def test_safety_kritisch_emergency(self) -> None:
        e = _make_engine()
        e._safety._check_interval = 0.0
        e._safety.analyze({"isolation_resistance": 30000.0, "battery_temperature": 65.0})
        d = e.diagnose_safety()
        assert d.status == DiagStatus.KRITISCH

    def test_safety_hinweis_low_iso(self) -> None:
        e = _make_engine()
        e._health.update_from_modbus({"isolation_resistance": 700000.0})
        d = e.diagnose_safety()
        assert d.status == DiagStatus.HINWEIS


class TestDiagnoseAll:

    def test_diagnose_all_returns_five_areas(self) -> None:
        e = _make_engine()
        result = e.diagnose_all()
        assert set(result.keys()) == {"dc_solar", "ac_grid", "battery", "inverter", "safety"}
        for area, diag in result.items():
            assert diag.area == area
            assert diag.status in (DiagStatus.OK, DiagStatus.HINWEIS, DiagStatus.WARNUNG, DiagStatus.KRITISCH)
            assert diag.title
            assert diag.action
