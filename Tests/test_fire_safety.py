"""Tests for FireSafetyMonitor."""

from __future__ import annotations

from kostal_plenticore.fire_safety import (
    FireRiskLevel,
    FireSafetyMonitor,
    _float,
    _rate_of_change,
)


class TestFireSafetyMonitor:

    def test_initial_state_safe(self) -> None:
        m = FireSafetyMonitor()
        assert m.current_risk_level == FireRiskLevel.SAFE
        assert m.alert_count == 0

    def test_isolation_emergency(self) -> None:
        m = FireSafetyMonitor()
        alerts = m.analyze({"isolation_resistance": 30000.0})
        assert len(alerts) == 1
        assert alerts[0].risk_level == FireRiskLevel.EMERGENCY
        assert alerts[0].category == "isolation"
        assert m.current_risk_level == FireRiskLevel.EMERGENCY

    def test_isolation_high(self) -> None:
        m = FireSafetyMonitor()
        alerts = m.analyze({"isolation_resistance": 80000.0})
        assert any(a.risk_level == FireRiskLevel.HIGH for a in alerts)

    def test_isolation_safe(self) -> None:
        m = FireSafetyMonitor()
        alerts = m.analyze({"isolation_resistance": 2000000.0})
        assert len(alerts) == 0

    def test_battery_emergency_temp(self) -> None:
        m = FireSafetyMonitor()
        alerts = m.analyze({"battery_temperature": 65.0})
        assert any(a.risk_level == FireRiskLevel.EMERGENCY for a in alerts)
        assert any("thermal runaway" in a.detail.lower() for a in alerts)

    def test_battery_high_temp(self) -> None:
        m = FireSafetyMonitor()
        alerts = m.analyze({"battery_temperature": 52.0})
        assert any(a.risk_level == FireRiskLevel.HIGH for a in alerts)

    def test_battery_safe_temp(self) -> None:
        m = FireSafetyMonitor()
        alerts = m.analyze({"battery_temperature": 25.0})
        bat_alerts = [a for a in alerts if "battery" in a.category]
        assert len(bat_alerts) == 0

    def test_controller_high_temp(self) -> None:
        m = FireSafetyMonitor()
        alerts = m.analyze({"controller_temp": 88.0})
        assert any(a.category == "controller_thermal" for a in alerts)

    def test_dc_string_imbalance_alert(self) -> None:
        m = FireSafetyMonitor()
        alerts = m.analyze({
            "dc1_power": 5000.0,
            "dc2_power": 5000.0,
            "dc3_power": 500.0,
        })
        dc_alerts = [a for a in alerts if "dc" in a.category]
        assert len(dc_alerts) >= 1

    def test_dc_strings_balanced_no_alert(self) -> None:
        m = FireSafetyMonitor()
        alerts = m.analyze({
            "dc1_power": 5000.0,
            "dc2_power": 4800.0,
            "dc3_power": 5100.0,
        })
        dc_alerts = [a for a in alerts if "dc" in a.category]
        assert len(dc_alerts) == 0

    def test_grid_frequency_emergency(self) -> None:
        m = FireSafetyMonitor()
        alerts = m.analyze({"grid_frequency": 47.0})
        assert any(a.category == "grid_emergency" for a in alerts)

    def test_phase_voltage_extreme(self) -> None:
        m = FireSafetyMonitor()
        alerts = m.analyze({"phase1_voltage": 280.0})
        assert any(a.category == "voltage_extreme" for a in alerts)

    def test_multiple_hazards(self) -> None:
        m = FireSafetyMonitor()
        alerts = m.analyze({
            "isolation_resistance": 40000.0,
            "battery_temperature": 62.0,
            "controller_temp": 90.0,
        })
        assert len(alerts) >= 3
        assert m.current_risk_level == FireRiskLevel.EMERGENCY

    def test_no_data_is_safe(self) -> None:
        m = FireSafetyMonitor()
        alerts = m.analyze({})
        assert len(alerts) == 0
        assert m.current_risk_level == FireRiskLevel.SAFE

    def test_rate_limiting(self) -> None:
        m = FireSafetyMonitor()
        m._check_interval = 0.0
        a1 = m.analyze({"isolation_resistance": 30000.0})
        m._check_interval = 9999.0
        a2 = m.analyze({"isolation_resistance": 30000.0})
        assert len(a1) > 0
        assert len(a2) == 0

    def test_active_alerts_expire(self) -> None:
        import time
        m = FireSafetyMonitor()
        m.analyze({"isolation_resistance": 30000.0})
        assert m.alert_count > 0
        for a in m._alerts:
            object.__setattr__(a, "timestamp", time.monotonic() - 7200)
        assert m.alert_count == 0
        assert m.current_risk_level == FireRiskLevel.SAFE


class TestHelpers:

    def test_float_none(self) -> None:
        assert _float(None) is None

    def test_float_string(self) -> None:
        assert _float("not_a_number") is None

    def test_float_valid(self) -> None:
        assert _float(42.5) == 42.5

    def test_rate_of_change_insufficient(self) -> None:
        from collections import deque
        h: deque[tuple[float, float]] = deque()
        assert _rate_of_change(h, 300.0) is None
