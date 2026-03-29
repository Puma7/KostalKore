"""Tests for FireSafetyMonitor."""

from __future__ import annotations

from kostal_plenticore.fire_safety import (
    FireRiskLevel,
    FireSafetyMonitor,
    SafetyAlert,
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

    def test_alerts_property_and_additional_risk_levels(self) -> None:
        import time as _time

        m = FireSafetyMonitor()
        m._alerts.append(
            SafetyAlert(
                _time.monotonic(),
                FireRiskLevel.MONITOR,
                "monitor_case",
                "Monitor",
                "detail",
                "action",
                {},
            )
        )
        assert len(m.alerts) == 1
        assert m.current_risk_level == FireRiskLevel.MONITOR

        m._alerts.append(
            SafetyAlert(
                _time.monotonic(),
                FireRiskLevel.ELEVATED,
                "elevated_case",
                "Elevated",
                "detail",
                "action",
                {},
            )
        )
        assert m.current_risk_level == FireRiskLevel.ELEVATED

    def test_analyze_invalid_state_and_duplicate_alerts_are_suppressed(self) -> None:
        import time as _time

        m = FireSafetyMonitor()
        m._check_interval = 0.0
        m._alerts.append(
            SafetyAlert(
                _time.monotonic(),
                FireRiskLevel.HIGH,
                "battery_thermal",
                "Existing",
                "detail",
                "action",
                {},
            )
        )

        alerts = m.analyze({"battery_temperature": 52.0, "inverter_state": "bad"})
        assert alerts == []
        assert len(m._alerts) == 1

    def test_isolation_rapid_drop_alert(self) -> None:
        m = FireSafetyMonitor()
        m._iso_history.extend(
            [(0.0, 500000.0), (150.0, 450000.0), (300.0, 300000.0)]
        )

        alerts = m._check_isolation(
            {
                "isolation_resistance": 400000.0,
                "total_dc_power": 1000.0,
                "inverter_state": 6,
            },
            300.0,
        )

        assert any(a.risk_level == FireRiskLevel.ELEVATED for a in alerts)

    def test_dc_string_anomaly_low_average_and_arc_branch(self) -> None:
        m = FireSafetyMonitor()
        assert (
            m._check_dc_string_anomaly(
                {
                    "dc1_power": 50.0,
                    "dc2_power": 60.0,
                    "dc1_voltage": 300.0,
                    "dc2_voltage": 310.0,
                },
                0.0,
            )
            == []
        )

        m._dc_power_history["dc1"].extend([(0.0, 600.0), (150.0, 500.0), (300.0, 100.0)])
        alerts = m._check_dc_string_anomaly(
            {
                "dc1_power": 100.0,
                "dc2_power": 2000.0,
                "dc1_voltage": 300.0,
                "dc2_voltage": 305.0,
            },
            300.0,
        )
        assert any(a.category == "dc_arc_indicator" for a in alerts)

    def test_stable_ratio_true_for_steady_history(self) -> None:
        m = FireSafetyMonitor()
        for idx in range(10):
            m._dc_ratio_history.append((float(idx * 60), 0.12))
        assert m._is_stable_ratio(600.0) is True

    def test_battery_and_controller_rate_based_alerts(self) -> None:
        m = FireSafetyMonitor()
        m._bat_temp_history.extend([(0.0, 42.0), (150.0, 44.0), (300.0, 46.5)])
        m._bat_voltage_history.extend([(0.0, 400.0), (150.0, 390.0), (300.0, 380.0)])
        bat_alerts = m._check_battery_thermal(
            {"battery_temperature": 46.5, "battery_voltage": 380.0},
            300.0,
        )
        assert any(a.category == "battery_thermal" for a in bat_alerts)
        assert any(a.category == "battery_voltage_anomaly" for a in bat_alerts)

        m._ctrl_temp_history.extend([(0.0, 70.0), (150.0, 74.0), (300.0, 79.0)])
        ctrl_alerts = m._check_controller_thermal({"controller_temp": 79.0}, 300.0)
        assert any(a.category == "controller_thermal" for a in ctrl_alerts)

    def test_clear_stale_alerts_keeps_recent_non_thermal_and_unknown_level_defaults_safe(self) -> None:
        import time as _time

        m = FireSafetyMonitor()
        m._alerts.append(
            SafetyAlert(
                _time.monotonic() - 100,
                "unexpected",
                "other",
                "Other",
                "detail",
                "action",
                {},
            )
        )
        m.clear_stale_alerts(False)
        assert len(m._alerts) == 1
        assert m.current_risk_level == FireRiskLevel.SAFE

    def test_clear_stale_alerts_drops_old_thermal_alerts_after_one_hour(self) -> None:
        import time as _time

        m = FireSafetyMonitor()
        m._alerts.append(
            SafetyAlert(
                _time.monotonic() - 3700,
                FireRiskLevel.HIGH,
                "battery_thermal",
                "Old thermal",
                "detail",
                "action",
                {},
            )
        )
        m.clear_stale_alerts(False)
        assert len(m._alerts) == 0

    def test_current_risk_level_high_branch(self) -> None:
        import time as _time

        m = FireSafetyMonitor()
        m._alerts.append(
            SafetyAlert(
                _time.monotonic(),
                FireRiskLevel.HIGH,
                "controller_thermal",
                "High",
                "detail",
                "action",
                {},
            )
        )
        assert m.current_risk_level == FireRiskLevel.HIGH

    def test_isolation_and_rate_checks_cover_non_alert_paths(self) -> None:
        m = FireSafetyMonitor()

        assert m._check_isolation(
            {"isolation_resistance": 500.0, "total_dc_power": 0.0, "inverter_state": 6},
            0.0,
        ) == []
        assert m._check_isolation(
            {"isolation_resistance": 500.0, "total_dc_power": 1000.0, "inverter_state": 10},
            0.0,
        ) == []

        m._iso_history.extend([(0.0, 500000.0), (150.0, 490000.0), (300.0, 480000.0)])
        alerts = m._check_isolation(
            {"isolation_resistance": 400000.0, "total_dc_power": 1000.0, "inverter_state": 6},
            300.0,
        )
        assert alerts == []

        fresh = FireSafetyMonitor()
        assert fresh._check_isolation(
            {"isolation_resistance": 400000.0, "total_dc_power": 1000.0, "inverter_state": 6},
            0.0,
        ) == []
        assert fresh._check_isolation(
            {"isolation_resistance": 600000.0, "total_dc_power": 1000.0, "inverter_state": 6},
            0.0,
        ) == []

    def test_dc_ratio_learning_and_thermal_checks_cover_no_alert_branches(self) -> None:
        m = FireSafetyMonitor()
        assert (
            m._check_dc_string_anomaly(
                {"dc1_power": 0.0, "dc2_power": 0.0},
                0.0,
            )
            == []
        )
        assert len(m._dc_ratio_history) == 0

        m._bat_temp_history.extend([(0.0, 44.0), (150.0, 44.5), (300.0, 45.5)])
        m._bat_voltage_history.extend([(0.0, 400.0), (150.0, 401.0), (300.0, 403.0)])
        assert (
            m._check_battery_thermal(
                {"battery_temperature": 45.5, "battery_voltage": 403.0},
                300.0,
            )
            == []
        )

        m._ctrl_temp_history.extend([(0.0, 74.0), (150.0, 74.5), (300.0, 75.5)])
        assert m._check_controller_thermal({"controller_temp": 75.5}, 300.0) == []

        assert m._check_controller_thermal({"controller_temp": 80.0}, 0.0) == []
        assert m._check_controller_thermal({"controller_temp": 70.0}, 0.0) == []


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

    def test_rate_of_change_window_and_short_delta(self) -> None:
        from collections import deque

        old_samples: deque[tuple[float, float]] = deque(
            [(0.0, 1.0), (10.0, 2.0), (20.0, 3.0)]
        )
        assert _rate_of_change(old_samples, 5.0) is None

        short_delta: deque[tuple[float, float]] = deque(
            [(100.0, 1.0), (105.0, 2.0), (109.0, 4.0)]
        )
        assert _rate_of_change(short_delta, 300.0) is None
