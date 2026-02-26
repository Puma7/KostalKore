"""Tests for DegradationTracker persistence and rate calculation."""

from __future__ import annotations

import time

from kostal_plenticore.degradation_tracker import (
    DailySnapshot,
    DegradationTracker,
    TrackedParameter,
    SECONDS_PER_DAY,
)


class TestDailySnapshot:

    def test_avg(self) -> None:
        s = DailySnapshot(day=1, min_val=10, max_val=30, sum_val=60, count=3)
        assert s.avg == 20.0

    def test_roundtrip(self) -> None:
        s = DailySnapshot(day=5, min_val=1, max_val=9, sum_val=15, count=3)
        restored = DailySnapshot.from_dict(s.to_dict())
        assert restored.day == 5
        assert restored.avg == 5.0


class TestTrackedParameter:

    def test_empty(self) -> None:
        p = TrackedParameter(name="test", unit="V")
        assert p.days_tracked == 0
        assert p.baseline_avg is None
        assert p.current_avg is None
        assert p.degradation_rate_per_month is None
        assert "nicht genug" in p.trend_description.lower() or "Daten" in p.trend_description

    def test_record_single_day(self) -> None:
        p = TrackedParameter(name="test", unit="V")
        now = time.time()
        p.record(100.0, now)
        p.record(110.0, now)
        p.record(90.0, now)
        assert p.days_tracked == 1
        assert p.current_avg == 100.0

    def test_record_multiple_days(self) -> None:
        p = TrackedParameter(name="test", unit="kΩ")
        base_time = time.time()
        for day in range(10):
            t = base_time + day * SECONDS_PER_DAY
            p.record(1000.0 - day * 10, t)
        assert p.days_tracked >= 10

    def test_baseline_needs_7_days(self) -> None:
        p = TrackedParameter(name="test", unit="V")
        base_time = time.time()
        for day in range(5):
            t = base_time + day * SECONDS_PER_DAY
            p.record(100.0, t)
        # Force snapshot flush
        p.record(100.0, base_time + 6 * SECONDS_PER_DAY)
        assert p.baseline_avg is None  # only 5-6 snapshots, need 7

    def test_baseline_with_enough_data(self) -> None:
        p = TrackedParameter(name="test", unit="V")
        base_time = time.time()
        for day in range(10):
            t = base_time + day * SECONDS_PER_DAY
            p.record(100.0, t)
        # flush last
        p.record(100.0, base_time + 11 * SECONDS_PER_DAY)
        assert p.baseline_avg is not None
        assert abs(p.baseline_avg - 100.0) < 1.0

    def test_degradation_rate_falling(self) -> None:
        p = TrackedParameter(name="iso", unit="kΩ")
        base_time = time.time()
        for day in range(30):
            t = base_time + day * SECONDS_PER_DAY
            p.record(1000.0 - day * 5, t)
        p.record(850.0, base_time + 31 * SECONDS_PER_DAY)
        rate = p.degradation_rate_per_month
        assert rate is not None
        assert rate < 0  # falling

    def test_degradation_rate_stable(self) -> None:
        p = TrackedParameter(name="test", unit="V")
        base_time = time.time()
        for day in range(30):
            t = base_time + day * SECONDS_PER_DAY
            p.record(230.0, t)
        p.record(230.0, base_time + 31 * SECONDS_PER_DAY)
        rate = p.degradation_rate_per_month
        assert rate is not None
        assert abs(rate) < 0.5

    def test_trend_description_stable(self) -> None:
        p = TrackedParameter(name="test", unit="V")
        base_time = time.time()
        for day in range(10):
            p.record(100.0, base_time + day * SECONDS_PER_DAY)
        p.record(100.0, base_time + 11 * SECONDS_PER_DAY)
        assert "Stabil" in p.trend_description or "stabil" in p.trend_description.lower()

    def test_persistence_roundtrip(self) -> None:
        p = TrackedParameter(name="test", unit="V")
        base_time = time.time()
        for day in range(5):
            p.record(100.0 + day, base_time + day * SECONDS_PER_DAY)

        data = p.to_dict()
        restored = TrackedParameter.from_dict(data)
        assert restored.name == "test"
        assert restored.unit == "V"
        assert len(restored.snapshots) == len(p.snapshots)

    def test_baseline_deviation(self) -> None:
        p = TrackedParameter(name="test", unit="kΩ")
        base_time = time.time()
        for day in range(7):
            p.record(1000.0, base_time + day * SECONDS_PER_DAY)
        for day in range(7, 14):
            p.record(800.0, base_time + day * SECONDS_PER_DAY)
        p.record(800.0, base_time + 15 * SECONDS_PER_DAY)
        dev = p.baseline_deviation_pct
        assert dev is not None
        assert dev < 0  # decreased


class TestDegradationTracker:

    def test_initial_state(self) -> None:
        t = DegradationTracker()
        assert len(t.all_parameters) == 8
        assert len(t.get_alerts()) == 0

    def test_update_from_modbus(self) -> None:
        t = DegradationTracker()
        t.update_from_modbus({
            "isolation_resistance": 2000000.0,
            "battery_temperature": 25.0,
            "controller_temp": 45.0,
            "dc1_power": 5000.0,
            "dc2_power": 3000.0,
            "daily_yield": 30000.0,
            "battery_work_capacity": 35000.0,
        })
        assert t.isolation.days_tracked == 1
        assert t.battery_temp_avg.days_tracked == 1
        assert t.dc1_peak_power.days_tracked == 1

    def test_persistence_roundtrip(self) -> None:
        t = DegradationTracker()
        t.update_from_modbus({"isolation_resistance": 2000000.0})
        data = t.to_dict()

        t2 = DegradationTracker()
        t2.restore_from_dict(data)
        assert t2.isolation.days_tracked == t.isolation.days_tracked

    def test_degradation_alert_isolation(self) -> None:
        t = DegradationTracker()
        base_time = time.time()
        for day in range(30):
            t.isolation.record(1000.0 - day * 20, base_time + day * SECONDS_PER_DAY)
        t.isolation.record(400.0, base_time + 31 * SECONDS_PER_DAY)
        alerts = t.get_alerts()
        iso_alerts = [a for a in alerts if "Isolation" in a["parameter"]]
        assert len(iso_alerts) >= 1

    def test_no_alert_stable(self) -> None:
        t = DegradationTracker()
        base_time = time.time()
        for day in range(30):
            t.isolation.record(2000.0, base_time + day * SECONDS_PER_DAY)
        t.isolation.record(2000.0, base_time + 31 * SECONDS_PER_DAY)
        alerts = t.get_alerts()
        iso_alerts = [a for a in alerts if "Isolation" in a["parameter"]]
        assert len(iso_alerts) == 0


class TestSeasonalTracking:

    def test_seasonal_avg_no_data(self) -> None:
        p = TrackedParameter(name="test", unit="kΩ")
        assert p.seasonal_avg() is None

    def test_seasonal_avg_same_season(self) -> None:
        p = TrackedParameter(name="test", unit="kΩ")
        base_time = time.time()
        for day in range(30):
            p.record(1000.0, base_time + day * SECONDS_PER_DAY)
        p.record(1000.0, base_time + 31 * SECONDS_PER_DAY)
        avg = p.seasonal_avg()
        assert avg is not None
        assert abs(avg - 1000.0) < 1.0

    def test_seasonal_deviation_stable(self) -> None:
        p = TrackedParameter(name="test", unit="V")
        base_time = time.time()
        for day in range(30):
            p.record(230.0, base_time + day * SECONDS_PER_DAY)
        p.record(230.0, base_time + 31 * SECONDS_PER_DAY)
        dev = p.seasonal_deviation_pct
        assert dev is not None
        assert abs(dev) < 1.0

    def test_seasonal_deviation_changed(self) -> None:
        p = TrackedParameter(name="test", unit="kΩ")
        base_time = time.time()
        for day in range(15):
            p.record(1000.0, base_time + day * SECONDS_PER_DAY)
        for day in range(15, 30):
            p.record(800.0, base_time + day * SECONDS_PER_DAY)
        p.record(800.0, base_time + 31 * SECONDS_PER_DAY)
        dev = p.seasonal_deviation_pct
        assert dev is not None

    def test_seasonal_trend_description_stable(self) -> None:
        p = TrackedParameter(name="test", unit="V")
        base_time = time.time()
        for day in range(20):
            p.record(230.0, base_time + day * SECONDS_PER_DAY)
        p.record(230.0, base_time + 21 * SECONDS_PER_DAY)
        desc = p.seasonal_trend_description
        assert "stabil" in desc.lower() or "Daten" in desc

    def test_seasonal_included_in_trend_description(self) -> None:
        p = TrackedParameter(name="iso", unit="kΩ")
        base_time = time.time()
        for day in range(15):
            p.record(1000.0, base_time + day * SECONDS_PER_DAY)
        for day in range(15, 30):
            p.record(700.0, base_time + day * SECONDS_PER_DAY)
        p.record(700.0, base_time + 31 * SECONDS_PER_DAY)
        desc = p.trend_description
        assert "Monat" in desc or "fallend" in desc

    def test_seasonal_window_matches(self) -> None:
        p = TrackedParameter(name="test", unit="W")
        base_time = time.time()
        for day in range(365 + 30):
            val = 5000.0 if (day % 365) < 180 else 2000.0
            p.record(val, base_time + day * SECONDS_PER_DAY)
        p.record(5000.0, base_time + (365 + 31) * SECONDS_PER_DAY)
        summer_avg = p.seasonal_avg()
        assert summer_avg is not None
