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

    def test_update_from_modbus_skips_none(self) -> None:
        m = InverterHealthMonitor()
        m.update_from_modbus({})
        assert m.isolation.current is None

    def test_update_from_modbus_skips_invalid(self) -> None:
        m = InverterHealthMonitor()
        m.update_from_modbus({"isolation_resistance": "not_a_number"})
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
            "controller_temp": 40.0,
            "grid_frequency": 50.0,
        })
        assert m.overall_health == HealthLevel.GOOD

    def test_overall_health_critical(self) -> None:
        m = InverterHealthMonitor()
        m.update_from_modbus({
            "isolation_resistance": 50000.0,
        })
        assert m.overall_health == HealthLevel.CRITICAL

    def test_overall_health_warning_from_errors(self) -> None:
        m = InverterHealthMonitor()
        m.update_from_modbus({"isolation_resistance": 2000000.0})
        for _ in range(10):
            m.record_error("test", "error")
        assert m.overall_health == HealthLevel.WARNING

    def test_health_score_decreases(self) -> None:
        m = InverterHealthMonitor()
        assert m.health_score == 100
        m.update_from_modbus({"isolation_resistance": 50000.0})
        assert m.health_score < 100

    def test_health_score_min_zero(self) -> None:
        m = InverterHealthMonitor()
        m.update_from_modbus({
            "isolation_resistance": 50000.0,
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
            "controller_temp": 42.0,
        })
        summary = m.get_health_summary()
        assert "overall_health" in summary
        assert "health_score" in summary
        assert "trackers" in summary
        assert "isolation_resistance" in summary["trackers"]
        assert summary["trackers"]["isolation_resistance"]["current"] == 1500000.0

    def test_all_trackers_dict(self) -> None:
        m = InverterHealthMonitor()
        trackers = m.all_trackers
        assert "isolation_resistance" in trackers
        assert "controller_temperature" in trackers
        assert "battery_soh" in trackers
        assert "battery_temperature" in trackers
        assert "battery_cycles" in trackers
        assert "grid_frequency" in trackers
        assert len(trackers) == 6
