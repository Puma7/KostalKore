"""Tests for DegradationTracker persistence and rate calculation."""

from __future__ import annotations

import time
from unittest.mock import patch

from kostal_plenticore.degradation_tracker import (
    SECONDS_PER_DAY,
    DailySnapshot,
    DegradationTracker,
    TrackedParameter,
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

    def test_trend_category_stable_state(self) -> None:
        """The trend category is the low-churn sensor state (steigend/fallend/
        stabil/unbekannt), unlike the verbose, numbers-embedding description."""
        empty = TrackedParameter(name="test", unit="V")
        assert empty.trend == "unbekannt"  # no data yet

        stable = TrackedParameter(name="test", unit="V")
        base_time = time.time()
        for day in range(11):
            stable.record(100.0, base_time + day * SECONDS_PER_DAY)
        assert stable.trend == "stabil"

        rising = TrackedParameter(name="test", unit="V")
        for day in range(11):
            rising.record(100.0 + day * 5.0, base_time + day * SECONDS_PER_DAY)
        assert rising.trend == "steigend"

        falling = TrackedParameter(name="test", unit="V")
        for day in range(11):
            falling.record(200.0 - day * 5.0, base_time + day * SECONDS_PER_DAY)
        assert falling.trend == "fallend"

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
            "total_dc_power": 5000.0,
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
        t.update_from_modbus({"isolation_resistance": 2000000.0, "total_dc_power": 5000.0})
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


class TestTrackedParameterCoverageGaps:

    def test_record_default_now_and_snapshot_trimming(self, monkeypatch) -> None:
        from kostal_plenticore import degradation_tracker as tracker_mod

        p = TrackedParameter(name="trim", unit="W")
        base_time = 100 * SECONDS_PER_DAY
        monkeypatch.setattr(tracker_mod, "MAX_DAILY_SNAPSHOTS", 2)
        monkeypatch.setattr(tracker_mod.time, "time", lambda: base_time)

        p.record(1.0)
        p.record(2.0, base_time + SECONDS_PER_DAY)
        p.record(3.0, base_time + 2 * SECONDS_PER_DAY)
        p.record(4.0, base_time + 3 * SECONDS_PER_DAY)

        assert len(p.snapshots) == 2

    def test_peak_metrics_and_trend_description_branches(self) -> None:
        p = TrackedParameter(name="peak", unit="W")
        assert p.baseline_peak is None
        assert p.current_peak is None

        p._current_snapshot = DailySnapshot(
            day=1, min_val=5.0, max_val=9.0, sum_val=14.0, count=2
        )
        assert p.current_peak == 9.0

        zero = TrackedParameter(name="zero", unit="W")
        base_time = 200 * SECONDS_PER_DAY
        for day in range(7):
            zero.record(0.0, base_time + day * SECONDS_PER_DAY)
        zero.record(0.0, base_time + 7 * SECONDS_PER_DAY)
        assert zero.peak_deviation_pct is None

        seasonal = TrackedParameter(name="seasonal", unit="W")
        seasonal.snapshots = [
            DailySnapshot(
                day=10 + day,
                min_val=100.0,
                max_val=100.0,
                sum_val=100.0,
                count=1,
            )
            for day in range(10)
        ]
        seasonal._current_day = 20
        seasonal._current_snapshot = DailySnapshot(
            day=20, min_val=150.0, max_val=150.0, sum_val=150.0, count=1
        )

        assert seasonal.seasonal_avg(target_day=370) is not None
        assert "Saisonbereinigt" in seasonal.seasonal_trend_description
        assert "saisonbereinigt" in seasonal.trend_description.lower()

    def test_same_day_current_snapshot_replaces_recent_average_and_peak(self) -> None:
        p = TrackedParameter(name="same_day", unit="W")
        p.snapshots = [
            DailySnapshot(day=10, min_val=10.0, max_val=20.0, sum_val=20.0, count=1),
            DailySnapshot(day=11, min_val=10.0, max_val=30.0, sum_val=30.0, count=1),
        ]
        p._current_snapshot = DailySnapshot(
            day=11, min_val=5.0, max_val=99.0, sum_val=99.0, count=1
        )

        assert p.current_peak == 59.5
        assert p.current_avg == 59.5

    def test_rate_and_seasonal_none_paths(self) -> None:
        p = TrackedParameter(name="flat_days", unit="W")
        p.snapshots = [
            DailySnapshot(day=5, min_val=10.0, max_val=20.0, sum_val=20.0, count=1)
            for _ in range(7)
        ]
        assert p.degradation_rate_per_month is None
        assert p.seasonal_avg(target_day=100) is None
        no_season = TrackedParameter(name="no_season", unit="W")
        assert "Noch nicht genug" in no_season.seasonal_trend_description

        zero_seasonal = TrackedParameter(name="zero_seasonal", unit="W")
        zero_seasonal.snapshots = [
            DailySnapshot(day=day, min_val=0.0, max_val=0.0, sum_val=0.0, count=1)
            for day in range(7)
        ]
        zero_seasonal._current_snapshot = DailySnapshot(
            day=8, min_val=10.0, max_val=10.0, sum_val=10.0, count=1
        )
        assert zero_seasonal.seasonal_deviation_pct is None

    def test_trend_description_skips_small_baseline_deviation_suffix(self) -> None:
        p = TrackedParameter(name="small_dev", unit="W")
        base_time = 500 * SECONDS_PER_DAY
        for day in range(7):
            p.record(100.0 + day, base_time + day * SECONDS_PER_DAY)
        for day in range(7, 14):
            p.record(97.0 + day, base_time + day * SECONDS_PER_DAY)
        p.record(111.0, base_time + 14 * SECONDS_PER_DAY)

        desc = p.trend_description
        assert "Monat" in desc
        assert "seit Baseline" not in desc


class TestDegradationTrackerCoverageGaps:

    def test_update_from_modbus_handles_invalid_values(self) -> None:
        t = DegradationTracker()
        with patch(
            "kostal_plenticore.degradation_tracker.normalize_isolation_resistance_ohm",
            side_effect=TypeError("broken"),
        ):
            t.update_from_modbus(
                {
                    "isolation_resistance": "bad",
                    "total_dc_power": "bad",
                    "inverter_state": "bad",
                    "battery_temperature": "bad",
                    "controller_temp": "bad",
                    "dc1_power": "bad",
                    "dc2_power": "bad",
                    "daily_yield": "bad",
                    "battery_work_capacity": "bad",
                }
            )

        assert t.isolation.days_tracked == 0
        assert t.battery_temp_avg.days_tracked == 0
        assert t.controller_temp_avg.days_tracked == 0

    def test_get_alerts_covers_controller_and_dc_paths(self) -> None:
        t = DegradationTracker()
        base_time = 300 * SECONDS_PER_DAY

        for day in range(7):
            t.controller_temp_avg.record(
                50.0 + day, base_time + day * SECONDS_PER_DAY
            )
            t.dc1_peak_power.record(200.0, base_time + day * SECONDS_PER_DAY)

        for day in range(7, 14):
            t.dc1_peak_power.record(80.0, base_time + day * SECONDS_PER_DAY)

        t.controller_temp_avg.record(80.0, base_time + 14 * SECONDS_PER_DAY)
        t.dc1_peak_power.record(80.0, base_time + 14 * SECONDS_PER_DAY)

        alerts = t.get_alerts()
        assert any(a["parameter"] == t.controller_temp_avg.name for a in alerts)
        assert any(a["parameter"] == t.dc1_peak_power.name for a in alerts)

    def test_update_battery_soh_and_restore_from_invalid_dict(self) -> None:
        t = DegradationTracker()
        t.update_battery_soh(95.0)
        assert t.battery_soh.days_tracked == 1
        assert t.battery_soh.current_avg == 95.0

        t.restore_from_dict({"isolation": "invalid"})
        assert t.isolation.days_tracked == 0

    def test_update_from_modbus_skips_low_values_and_handles_iso_type_error(self) -> None:
        t = DegradationTracker()
        with patch(
            "kostal_plenticore.degradation_tracker.normalize_isolation_resistance_ohm",
            side_effect=TypeError("bad iso"),
        ):
            t.update_from_modbus(
                {
                    "isolation_resistance": 123.0,
                    "total_dc_power": 500.0,
                    "inverter_state": 6,
                    "dc1_power": 50.0,
                    "dc2_power": 100.0,
                    "daily_yield": 0.0,
                }
            )

        assert t.isolation.days_tracked == 0
        assert t.dc1_peak_power.days_tracked == 0
        assert t.dc2_peak_power.days_tracked == 0
        assert t.daily_yield.days_tracked == 0

    def test_update_from_modbus_skips_when_normalized_iso_is_none(self) -> None:
        t = DegradationTracker()
        with patch(
            "kostal_plenticore.degradation_tracker.normalize_isolation_resistance_ohm",
            return_value=None,
        ):
            t.update_from_modbus(
                {
                    "isolation_resistance": 123000.0,
                    "total_dc_power": 800.0,
                    "inverter_state": 6,
                }
            )

        assert t.isolation.days_tracked == 0

    def test_get_alerts_covers_battery_soh_path(self) -> None:
        t = DegradationTracker()
        base_time = 400 * SECONDS_PER_DAY
        for day in range(7):
            t.battery_soh.record(100.0 - day, base_time + day * SECONDS_PER_DAY)
        t.battery_soh.record(85.0, base_time + 8 * SECONDS_PER_DAY)

        alerts = t.get_alerts()
        assert any(a["parameter"] == t.battery_soh.name for a in alerts)

    def test_get_alerts_skips_dc_peak_when_effective_deviation_not_low_enough(self) -> None:
        t = DegradationTracker()
        base_time = 600 * SECONDS_PER_DAY
        for day in range(14):
            value = 200.0 if day < 7 else 195.0
            t.dc1_peak_power.record(value, base_time + day * SECONDS_PER_DAY)
        t.dc1_peak_power.record(195.0, base_time + 15 * SECONDS_PER_DAY)

        alerts = t.get_alerts()
        assert not any(a["parameter"] == t.dc1_peak_power.name for a in alerts)
