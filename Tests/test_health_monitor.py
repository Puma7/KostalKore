"""Tests for InverterHealthMonitor."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from kostal_plenticore.health_monitor import (
    HealthLevel,
    HealthSample,
    InverterHealthMonitor,
    ParameterTracker,
    MAX_HISTORY_SIZE,
)


class TestParameterTracker:
    """Test ParameterTracker statistics and threshold logic."""

    def test_empty_tracker(self) -> None:
        t = ParameterTracker(name="test", unit="V")
        assert t.current is None
        assert t.min_value is None
        assert t.max_value is None
        assert t.avg_value is None
        assert t.sample_count == 0
        assert t.level == HealthLevel.UNKNOWN
        assert t.trend == "insufficient_data"

    def test_record_and_stats(self) -> None:
        t = ParameterTracker(name="test", unit="W")
        t.record(10.0)
        t.record(20.0)
        t.record(30.0)
        assert t.current == 30.0
        assert t.min_value == 10.0
        assert t.max_value == 30.0
        assert t.avg_value == 20.0
        assert t.sample_count == 3

    def test_warning_high(self) -> None:
        t = ParameterTracker(name="temp", unit="°C", warning_high=65.0)
        t.record(70.0)
        assert t.level == HealthLevel.WARNING

    def test_warning_low(self) -> None:
        t = ParameterTracker(name="iso", unit="Ohm", warning_low=500000.0)
        t.record(400000.0)
        assert t.level == HealthLevel.WARNING

    def test_critical_high(self) -> None:
        t = ParameterTracker(name="temp", unit="°C", critical_high=75.0)
        t.record(80.0)
        assert t.level == HealthLevel.CRITICAL

    def test_critical_low(self) -> None:
        t = ParameterTracker(name="iso", unit="Ohm", critical_low=100000.0)
        t.record(50000.0)
        assert t.level == HealthLevel.CRITICAL

    def test_good_level(self) -> None:
        t = ParameterTracker(name="temp", unit="°C", warning_high=65.0, critical_high=75.0)
        t.record(50.0)
        assert t.level == HealthLevel.GOOD

    def test_trend_stable(self) -> None:
        t = ParameterTracker(name="test", unit="V")
        for _ in range(20):
            t.record(100.0)
        assert t.trend == "stable"

    def test_trend_rising(self) -> None:
        t = ParameterTracker(name="test", unit="V")
        for i in range(20):
            t.record(float(i))
        assert t.trend == "rising"

    def test_trend_falling(self) -> None:
        t = ParameterTracker(name="test", unit="V")
        for i in range(20, 0, -1):
            t.record(float(i))
        assert t.trend == "falling"

    def test_max_history_size(self) -> None:
        t = ParameterTracker(name="test", unit="V")
        for i in range(MAX_HISTORY_SIZE + 100):
            t.record(float(i))
        assert t.sample_count == MAX_HISTORY_SIZE


class TestInverterHealthMonitor:
    """Test the central health monitoring engine."""

    def test_initial_state(self) -> None:
        m = InverterHealthMonitor()
        assert m.overall_health == HealthLevel.UNKNOWN
        assert m.health_score == 100
        assert m.communication_reliability == 100.0
        assert m.error_rate_per_hour == 0.0
        assert m.event_count == 0

    def test_update_from_modbus(self) -> None:
        m = InverterHealthMonitor()
        m.update_from_modbus({
            "isolation_resistance": 1000000.0,
            "total_dc_power": 5000.0,
            "controller_temp": 45.0,
            "battery_temperature": 25.0,
            "grid_frequency": 50.01,
            "battery_cycles": 150.0,
        })
        assert m.isolation.current == 1000000.0
        assert m.controller_temp.current == 45.0
        assert m.battery_temp.current == 25.0
        assert m.grid_frequency.current == 50.01
        assert m.battery_cycles.current == 150.0

    def test_grid_thresholds_adapt_to_60hz(self) -> None:
        m = InverterHealthMonitor()
        m.update_from_modbus({"grid_frequency": 60.1})
        assert m.grid_frequency.level == HealthLevel.GOOD

    def test_voltage_thresholds_adapt_to_120v(self) -> None:
        m = InverterHealthMonitor()
        m.update_from_modbus(
            {
                "phase1_voltage": 121.0,
                "phase2_voltage": 119.0,
                "phase3_voltage": 122.0,
            }
        )
        assert m.phase1_voltage.level == HealthLevel.GOOD

    def test_update_from_modbus_skips_none(self) -> None:
        m = InverterHealthMonitor()
        m.update_from_modbus({})
        assert m.isolation.current is None

    def test_update_from_modbus_skips_invalid(self) -> None:
        m = InverterHealthMonitor()
        m.update_from_modbus({"isolation_resistance": "not_a_number", "total_dc_power": 5000.0})
        assert m.isolation.current is None

    def test_update_battery_soh(self) -> None:
        m = InverterHealthMonitor()
        m.update_battery_soh(95.0)
        assert m.battery_soh.current == 95.0

    def test_record_error(self) -> None:
        m = InverterHealthMonitor()
        m.record_error("modbus", "Connection timeout")
        assert m.event_count == 1
        assert m._failed_polls == 1
        assert len(m.recent_events) == 1
        assert m.recent_events[0].category == "modbus"

    def test_record_event(self) -> None:
        m = InverterHealthMonitor()
        m.record_event("info", "System started", HealthLevel.GOOD)
        assert m.event_count == 1

    def test_error_rate_calculation(self) -> None:
        m = InverterHealthMonitor()
        for _ in range(10):
            m.record_error("test", "error")
        assert m.error_rate_per_hour == 10.0

    def test_communication_reliability(self) -> None:
        m = InverterHealthMonitor()
        m._total_polls = 100
        m._failed_polls = 5
        assert m.communication_reliability == 95.0

    def test_overall_health_good(self) -> None:
        m = InverterHealthMonitor()
        m.update_from_modbus({
            "isolation_resistance": 2000000.0,
            "total_dc_power": 5000.0,
            "controller_temp": 40.0,
            "grid_frequency": 50.0,
        })
        assert m.overall_health == HealthLevel.GOOD

    def test_overall_health_critical(self) -> None:
        m = InverterHealthMonitor()
        m.update_from_modbus({
            "isolation_resistance": 50000.0,
            "total_dc_power": 5000.0,
        })
        assert m.overall_health == HealthLevel.CRITICAL

    def test_overall_health_warning_from_errors(self) -> None:
        m = InverterHealthMonitor()
        m.update_from_modbus({"isolation_resistance": 2000000.0, "total_dc_power": 5000.0})
        for _ in range(10):
            m.record_error("test", "error")
        assert m.overall_health == HealthLevel.WARNING

    def test_health_score_decreases(self) -> None:
        m = InverterHealthMonitor()
        assert m.health_score == 100
        m.update_from_modbus({"isolation_resistance": 50000.0, "total_dc_power": 5000.0})
        assert m.health_score < 100

    def test_health_score_min_zero(self) -> None:
        m = InverterHealthMonitor()
        m.update_from_modbus({
            "isolation_resistance": 50000.0,
            "total_dc_power": 5000.0,
            "controller_temp": 80.0,
            "grid_frequency": 52.0,
        })
        m.update_battery_soh(50.0)
        m._total_polls = 100
        m._failed_polls = 50
        for _ in range(20):
            m.record_error("test", "error")
        assert m.health_score >= 0

    def test_get_health_summary(self) -> None:
        m = InverterHealthMonitor()
        m.update_from_modbus({
            "isolation_resistance": 1500000.0,
            "total_dc_power": 5000.0,
            "controller_temp": 42.0,
        })
        summary = m.get_health_summary()
        assert "overall_health" in summary
        assert "health_score" in summary
        assert "trackers" in summary
        assert "isolation_resistance" in summary["trackers"]
        assert summary["trackers"]["isolation_resistance"]["current"] == 1500000.0
        assert "dc_string_imbalance" in summary
        assert "phase_voltage_imbalance" in summary

    def test_all_trackers_dict(self) -> None:
        m = InverterHealthMonitor()
        trackers = m.all_trackers
        assert "isolation_resistance" in trackers
        assert "controller_temperature" in trackers
        assert "battery_soh" in trackers
        assert "battery_temperature" in trackers
        assert "battery_cycles" in trackers
        assert "grid_frequency" in trackers
        assert "phase1_voltage" in trackers
        assert "dc1_power" in trackers
        assert "active_errors" in trackers
        assert len(trackers) == 22

    def test_dc_string_imbalance(self) -> None:
        m = InverterHealthMonitor()
        m.update_from_modbus({"dc1_power": 5000.0, "dc2_power": 5000.0, "dc3_power": 3000.0})
        imb = m.dc_string_imbalance
        assert imb is not None
        assert imb > 0

    def test_dc_string_imbalance_none_when_no_data(self) -> None:
        m = InverterHealthMonitor()
        assert m.dc_string_imbalance is None

    def test_dc_string_imbalance_excludes_dc3_when_bidirectional(self) -> None:
        m = InverterHealthMonitor(num_bidirectional=1)
        m.update_from_modbus(
            {"dc1_power": 60.0, "dc2_power": 120.0, "dc3_power": 6000.0}
        )
        imb = m.dc_string_imbalance
        assert imb is not None
        assert round(imb, 2) == round((30.0 / 90.0) * 100.0, 2)

    def test_phase_voltage_imbalance(self) -> None:
        m = InverterHealthMonitor()
        m.update_from_modbus({"phase1_voltage": 230.0, "phase2_voltage": 235.0, "phase3_voltage": 228.0})
        imb = m.phase_voltage_imbalance
        assert imb is not None
        assert imb >= 0

    def test_inverter_state_tracking(self) -> None:
        m = InverterHealthMonitor()
        m.update_from_modbus({"inverter_state": 6})
        m.update_from_modbus({"inverter_state": 10})
        m.update_from_modbus({"inverter_state": 6})
        assert m.state_change_count == 2

    def test_update_error_counts(self) -> None:
        m = InverterHealthMonitor()
        m.update_error_counts(2, 5)
        assert m.active_error_count.current == 2.0
        assert m.active_warning_count.current == 5.0

    def test_info_level_threshold(self) -> None:
        m = InverterHealthMonitor()
        m.update_from_modbus({"controller_temp": 64.0})
        assert m.controller_temp.level == HealthLevel.INFO

    def test_overall_health_warning_branch(self) -> None:
        m = InverterHealthMonitor()
        m.update_from_modbus({"grid_frequency": 50.6})
        assert m.grid_frequency.level == HealthLevel.WARNING
        assert m.overall_health == HealthLevel.WARNING

    def test_isolation_stored_in_ohm(self) -> None:
        m = InverterHealthMonitor()
        m.update_from_modbus({"isolation_resistance": 2000000.0, "total_dc_power": 5000.0})
        assert m.isolation.current == 2000000.0

    def test_isolation_skipped_when_pv_inactive(self) -> None:
        """Isolation not recorded at night to avoid mixed-unit values."""
        m = InverterHealthMonitor()
        m.update_from_modbus({"isolation_resistance": 2000000.0, "total_dc_power": 0.0})
        assert m.isolation.current is None
        assert m.get_isolation_resistance_ohm() is None
        assert m._isolation_modbus_unavailable is True
        assert m.isolation_modbus_attributes()["modbus_measurement_unavailable"] is True

    def test_isolation_sentinel_recorded_as_off_scale_during_pv(self) -> None:
        """Sentinel during PV matches Kostal ~65.5 MΩ off-scale display."""
        m = InverterHealthMonitor()
        m.update_from_modbus(
            {
                "isolation_resistance": 65_535_000.0,
                "total_dc_power": 5000.0,
                "inverter_state": 6,
            }
        )
        assert m.isolation.current == 65_535_000.0
        assert m.get_isolation_resistance_ohm() == 65_535_000.0
        assert m._isolation_modbus_off_scale is True
        attrs = m.isolation_modbus_attributes()
        assert attrs["modbus_sentinel"] is True
        assert attrs["kostal_display_mohm"] == 65.5
        assert attrs["modbus_measurement_unavailable"] is False

    def test_isolation_sentinel_skipped_at_night(self) -> None:
        """Sentinel without PV must not seed a fake high reading."""
        m = InverterHealthMonitor()
        m.update_from_modbus(
            {
                "isolation_resistance": 65_535_000.0,
                "total_dc_power": 0.0,
                "inverter_state": 10,
            }
        )
        assert m.isolation.current is None
        assert m.get_isolation_resistance_ohm() is None
        assert m._isolation_modbus_unavailable is True

    def test_isolation_sentinel_unavailable_when_not_off_scale(self) -> None:
        m = InverterHealthMonitor()
        with patch(
            "custom_components.kostal_kore.health_monitor.isolation_sentinel_as_off_scale_high",
            return_value=False,
        ):
            m.update_from_modbus(
                {
                    "isolation_resistance": 65_535_000.0,
                    "total_dc_power": 5000.0,
                    "inverter_state": 6,
                }
            )
        assert m.get_isolation_resistance_ohm() is None
        assert m._isolation_modbus_unavailable is True

    def test_isolation_unavailable_when_normalization_fails(self) -> None:
        m = InverterHealthMonitor()
        with patch(
            "custom_components.kostal_kore.health_monitor.normalize_isolation_resistance_ohm",
            return_value=None,
        ):
            m.update_from_modbus(
                {
                    "isolation_resistance": 5000.0,
                    "total_dc_power": 5000.0,
                    "inverter_state": 6,
                }
            )
        assert m.get_isolation_resistance_ohm() is None
        assert m._isolation_modbus_unavailable is True

    @pytest.mark.asyncio
    async def test_restore_isolation_skips_expired_persisted_sample(
        self, hass: HomeAssistant
    ) -> None:
        import time
        from unittest.mock import AsyncMock, MagicMock

        from custom_components.kostal_kore.modbus_coordinator import (
            ModbusDataUpdateCoordinator,
        )

        client = MagicMock()
        client.host = "192.168.1.250"
        client.port = 1502
        coord = ModbusDataUpdateCoordinator(hass, client)
        monitor = InverterHealthMonitor()
        coord._health_monitor = monitor
        coord._isolation_store.async_load = AsyncMock(
            return_value={
                "isolation_ohm": 22_700_000.0,
                "saved_at": time.time() - 90_000.0,
            }
        )
        await coord._restore_isolation_sample()
        assert monitor.isolation.sample_count == 0

    def test_isolation_sentinel_replaces_stale_low_sample(self) -> None:
        """Off-scale sentinel overwrites a wrong historic sample (e.g. 22.7 MΩ)."""
        m = InverterHealthMonitor()
        m.isolation.record(22_700_000.0)
        m.update_from_modbus(
            {
                "isolation_resistance": 65_535_000.0,
                "total_dc_power": 9000.0,
                "inverter_state": 6,
            }
        )
        assert m.get_isolation_resistance_ohm() == 65_535_000.0
        assert m.isolation.sample_count == 2


class TestHealthMonitorCoverageGaps:

    def test_parameter_tracker_info_low_level_branch(self) -> None:
        tracker = ParameterTracker(name="test", unit="V", info_low=210.0)
        tracker.record(205.0)
        assert tracker.level == HealthLevel.INFO

    def test_update_from_modbus_skips_unusable_isolation_and_invalid_state(self) -> None:
        monitor = InverterHealthMonitor()
        with patch(
            "kostal_plenticore.health_monitor.normalize_isolation_resistance_ohm",
            return_value=None,
        ):
            monitor.update_from_modbus(
                {
                    "isolation_resistance": 5000.0,
                    "total_dc_power": 5000.0,
                    "inverter_state": "not-a-state",
                }
            )

        assert monitor.isolation.current is None
        assert monitor.state_change_count == 0

    def test_update_battery_soh_zero_is_ignored(self) -> None:
        monitor = InverterHealthMonitor()
        monitor.update_battery_soh(0.0)
        assert monitor.battery_soh.current is None

    def test_overall_health_info_and_score_penalties(self) -> None:
        monitor = InverterHealthMonitor()
        monitor.update_from_modbus(
            {"controller_temp": 64.0, "dc1_power": 60.0, "dc2_power": 120.0}
        )
        monitor._total_polls = 10
        monitor._failed_polls = 1

        assert monitor.overall_health == HealthLevel.INFO
        assert monitor.health_score == 82
