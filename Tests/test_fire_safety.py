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
        alerts = m.analyze({"isolation_resistance": 30000.0, "total_dc_power": 5000.0, "inverter_state": 6})
        assert len(alerts) == 1
        assert alerts[0].risk_level == FireRiskLevel.EMERGENCY
        assert alerts[0].category == "isolation"
        assert m.current_risk_level == FireRiskLevel.EMERGENCY

    def test_isolation_high(self) -> None:
        m = FireSafetyMonitor()
        alerts = m.analyze({"isolation_resistance": 80000.0, "total_dc_power": 5000.0, "inverter_state": 6})
        assert any(a.risk_level == FireRiskLevel.HIGH for a in alerts)

    def test_isolation_safe(self) -> None:
        m = FireSafetyMonitor()
        alerts = m.analyze({"isolation_resistance": 2000000.0, "inverter_state": 6})
        assert len(alerts) == 0

    def test_isolation_ignored_at_night_standby(self) -> None:
        """Isolation = 0 at night/standby is NOT a fire risk."""
        m = FireSafetyMonitor()
        alerts = m.analyze({"isolation_resistance": 0.0, "inverter_state": 10, "total_dc_power": 0.0})
        assert len(alerts) == 0
        assert m.current_risk_level == FireRiskLevel.SAFE

    def test_isolation_ignored_when_off(self) -> None:
        m = FireSafetyMonitor()
        alerts = m.analyze({"isolation_resistance": 0.0, "inverter_state": 0})
        assert len(alerts) == 0

    def test_isolation_ignored_low_dc_no_state(self) -> None:
        m = FireSafetyMonitor()
        alerts = m.analyze({"isolation_resistance": 500.0, "total_dc_power": 10.0})
        iso_alerts = [a for a in alerts if a.category == "isolation"]
        assert len(iso_alerts) == 0

    def test_all_checks_skipped_in_standby(self) -> None:
        m = FireSafetyMonitor()
        alerts = m.analyze({
            "isolation_resistance": 0.0,
            "battery_temperature": 0.0,
            "controller_temp": 0.0,
            "inverter_state": 10,
        })
        assert len(alerts) == 0

    def test_isolation_skipped_when_no_pv_feedin_from_battery(self) -> None:
        """FeedIn state but no PV (battery discharge only) → skip isolation check."""
        m = FireSafetyMonitor()
        alerts = m.analyze({
            "isolation_resistance": 0.0,
            "total_dc_power": -0.09,
            "inverter_state": 6,
            "battery_temperature": 25.0,
        })
        iso_alerts = [a for a in alerts if a.category == "isolation"]
        assert len(iso_alerts) == 0

    def test_stale_alerts_cleared_at_night(self) -> None:
        """Non-thermal alerts expire after 5min when PV is off."""
        import time as _time
        m = FireSafetyMonitor()
        m._check_interval = 0
        m.analyze({
            "isolation_resistance": 30000.0,
            "total_dc_power": 5000.0,
            "inverter_state": 6,
        })
        assert m.alert_count > 0
        for a in m._alerts:
            object.__setattr__(a, "timestamp", _time.monotonic() - 400)
        m.clear_stale_alerts(pv_active=False)
        assert m.alert_count == 0

    def test_thermal_alerts_kept_at_night(self) -> None:
        """Battery thermal alerts are kept even at night."""
        import time as _time
        m = FireSafetyMonitor()
        m._check_interval = 0
        m.analyze({"battery_temperature": 65.0, "total_dc_power": 0, "inverter_state": 6})
        assert m.alert_count > 0
        for a in m._alerts:
            object.__setattr__(a, "timestamp", _time.monotonic() - 400)
        m.clear_stale_alerts(pv_active=False)
        assert m.alert_count > 0

    def test_battery_emergency_temp(self) -> None:
        m = FireSafetyMonitor()
        alerts = m.analyze({"battery_temperature": 65.0, "inverter_state": 6})
        assert any(a.risk_level == FireRiskLevel.EMERGENCY for a in alerts)
        assert any("thermal runaway" in a.detail.lower() for a in alerts)

    def test_battery_high_temp(self) -> None:
        m = FireSafetyMonitor()
        alerts = m.analyze({"battery_temperature": 52.0, "inverter_state": 6})
        assert any(a.risk_level == FireRiskLevel.HIGH for a in alerts)

    def test_battery_safe_temp(self) -> None:
        m = FireSafetyMonitor()
        alerts = m.analyze({"battery_temperature": 25.0, "inverter_state": 6})
        bat_alerts = [a for a in alerts if "battery" in a.category]
        assert len(bat_alerts) == 0

    def test_controller_high_temp(self) -> None:
        m = FireSafetyMonitor()
        alerts = m.analyze({"controller_temp": 88.0, "inverter_state": 6})
        assert any(a.category == "controller_thermal" for a in alerts)

    def test_dc_string_imbalance_alert(self) -> None:
        m = FireSafetyMonitor()
        alerts = m.analyze({
            "dc1_power": 5000.0, "dc2_power": 5000.0, "dc3_power": 500.0,
            "total_dc_power": 10500.0, "inverter_state": 6,
        })
        dc_alerts = [a for a in alerts if "dc" in a.category]
        assert len(dc_alerts) >= 1

    def test_dc_strings_balanced_no_alert(self) -> None:
        m = FireSafetyMonitor()
        alerts = m.analyze({
            "dc1_power": 5000.0, "dc2_power": 4800.0, "dc3_power": 5100.0,
            "inverter_state": 6,
        })
        dc_alerts = [a for a in alerts if "dc" in a.category]
        assert len(dc_alerts) == 0

    def test_grid_frequency_emergency(self) -> None:
        m = FireSafetyMonitor()
        alerts = m.analyze({"grid_frequency": 47.0, "inverter_state": 6})
        assert any(a.category == "grid_emergency" for a in alerts)

    def test_grid_frequency_60hz_profile_no_emergency(self) -> None:
        m = FireSafetyMonitor()
        alerts = m.analyze({"grid_frequency": 60.2, "inverter_state": 6})
        assert not any(a.category == "grid_emergency" for a in alerts)

    def test_phase_voltage_extreme(self) -> None:
        m = FireSafetyMonitor()
        alerts = m.analyze({"phase1_voltage": 280.0, "inverter_state": 6})
        assert any(a.category == "voltage_extreme" for a in alerts)

    def test_phase_voltage_120v_profile_no_extreme(self) -> None:
        m = FireSafetyMonitor()
        alerts = m.analyze(
            {
                "phase1_voltage": 120.0,
                "phase2_voltage": 122.0,
                "phase3_voltage": 119.0,
                "inverter_state": 6,
            }
        )
        assert not any(a.category == "voltage_extreme" for a in alerts)

    def test_multiple_hazards(self) -> None:
        m = FireSafetyMonitor()
        alerts = m.analyze({
            "isolation_resistance": 40000.0,
            "battery_temperature": 62.0,
            "controller_temp": 90.0,
            "total_dc_power": 5000.0,
            "inverter_state": 6,
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
        a1 = m.analyze({"isolation_resistance": 30000.0, "total_dc_power": 5000.0, "inverter_state": 6})
        m._check_interval = 9999.0
        a2 = m.analyze({"isolation_resistance": 30000.0, "total_dc_power": 5000.0, "inverter_state": 6})
        assert len(a1) > 0
        assert len(a2) == 0

    def test_active_alerts_expire(self) -> None:
        import time
        m = FireSafetyMonitor()
        m._check_interval = 0.0
        m.analyze({"isolation_resistance": 30000.0, "total_dc_power": 5000.0, "inverter_state": 6})
        assert m.alert_count > 0
        for a in m._alerts:
            object.__setattr__(a, "timestamp", time.monotonic() - 7200)
        assert m.alert_count == 0
        assert m.current_risk_level == FireRiskLevel.SAFE

    def test_isolation_history_only_updates_when_pv_active(self) -> None:
        """Isolation history should avoid mixed-unit night/day entries."""
        m = FireSafetyMonitor()
        m._record_history(
            {"isolation_resistance": 65.5, "total_dc_power": 5000.0, "inverter_state": 6},
            1.0,
        )
        assert len(m._iso_history) == 1
        assert m._iso_history[-1][1] == 65500.0

        # Night/standby sample must not enter iso history.
        m._record_history(
            {"isolation_resistance": 65.5, "total_dc_power": 0.0, "inverter_state": 10},
            2.0,
        )
        assert len(m._iso_history) == 1


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
