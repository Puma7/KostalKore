"""Tests for the battery SoH calculator."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.kostal_kore.battery_soh_calculator import (
    BatterySohCalculator,
    _opt_float,
)


@pytest.fixture
def calc():
    hass = MagicMock()
    with patch(
        "custom_components.kostal_kore.battery_soh_calculator._BatterySohStore"
    ) as store_cls:
        store_cls.return_value.async_load = AsyncMock(return_value=None)
        store_cls.return_value.async_save = AsyncMock(return_value=None)
        c = BatterySohCalculator(hass, "test_store_key")
    return c


def test_opt_float_handles_invalid_inputs():
    assert _opt_float(None) is None
    assert _opt_float("not-a-number") is None
    assert _opt_float(float("nan")) is None
    assert _opt_float(float("inf")) is None
    assert _opt_float(float("-inf")) is None
    assert _opt_float("3.14") == 3.14
    assert _opt_float(42) == 42.0


def test_no_data_returns_none(calc):
    assert calc.soh_pct is None
    assert calc.baseline_capacity_wh is None
    assert calc.degradation_per_kwh is None
    assert calc.soh_projection_5y_pct is None
    assert calc.sample_count == 0


def test_first_observation_sets_baseline(calc):
    changed = calc.update_from_modbus({
        "battery_work_capacity": 35700.0,
        "total_dc_charge": 500_000.0,
        "total_dc_discharge": 400_000.0,
        "battery_cycles": 76.0,
    })
    assert changed is True
    assert calc.baseline_capacity_wh == 35700.0
    assert calc.current_capacity_wh == 35700.0
    assert calc.soh_pct == pytest.approx(100.0)
    assert calc.cycles == 76.0
    # Throughput attribute exposes charge + discharge for debugging
    assert calc.total_throughput_kwh == pytest.approx(900.0)
    assert calc.total_charge_kwh == pytest.approx(500.0)
    assert calc.total_discharge_kwh == pytest.approx(400.0)


def test_rejects_zero_or_negative_capacity(calc):
    assert calc.update_from_modbus({"battery_work_capacity": 0}) is False
    assert calc.update_from_modbus({"battery_work_capacity": -5.0}) is False
    assert calc.baseline_capacity_wh is None


def test_rejects_implausible_capacity(calc):
    """A corrupted Modbus read with absurd capacity must not poison baseline."""
    assert calc.update_from_modbus({"battery_work_capacity": 50_000_000.0}) is False
    assert calc.baseline_capacity_wh is None
    # Subsequent realistic readings still work normally
    assert calc.update_from_modbus({"battery_work_capacity": 35000.0}) is True
    assert calc.baseline_capacity_wh == 35000.0


def test_rejects_nan_capacity(calc):
    """NaN must be filtered out before any state change."""
    assert calc.update_from_modbus(
        {"battery_work_capacity": float("nan")}
    ) is False
    assert calc.baseline_capacity_wh is None


def test_baseline_only_raises_above_threshold(calc):
    calc.update_from_modbus({"battery_work_capacity": 35000.0})
    # +0.3 % — below threshold, baseline stays
    calc.update_from_modbus({"battery_work_capacity": 35100.0})
    assert calc.baseline_capacity_wh == 35000.0
    # +1 % — above 0.5 % threshold → baseline raises
    calc.update_from_modbus({"battery_work_capacity": 35350.0})
    assert calc.baseline_capacity_wh == 35350.0


def test_baseline_never_falls(calc):
    calc.update_from_modbus({"battery_work_capacity": 35700.0})
    # Capacity drops (degradation) — baseline must stay at peak
    calc.update_from_modbus({"battery_work_capacity": 34000.0})
    assert calc.baseline_capacity_wh == 35700.0
    assert calc.soh_pct == pytest.approx(34000.0 / 35700.0 * 100.0)


def test_soh_projection_requires_minimum_samples(calc):
    # Single sample — no slope possible
    calc.update_from_modbus({"battery_work_capacity": 35000.0})
    assert calc.degradation_per_kwh is None
    assert calc.soh_projection_5y_pct is None


def _seed_samples(c, count: int, span_days: float = 60.0, slope_wh_per_kwh: float = -0.1):
    """Helper: seed the calculator with synthetic linear-degradation samples."""
    now = time.time()
    for i in range(count):
        # Span 'span_days' evenly. Oldest = now - span_days.
        ts = now - (span_days - i * (span_days / max(count - 1, 1))) * 86400
        discharge_kwh = i * 10.0
        capacity_wh = 35000.0 + (discharge_kwh * slope_wh_per_kwh)
        c._samples.append((discharge_kwh, capacity_wh, ts))
    c._baseline_capacity_wh = 35000.0
    c._baseline_set_at = now - span_days * 86400
    c._current_capacity_wh = c._samples[-1][1]


def test_soh_projection_with_linear_degradation():
    hass = MagicMock()
    with patch(
        "custom_components.kostal_kore.battery_soh_calculator._BatterySohStore"
    ) as store_cls:
        store_cls.return_value.async_load = AsyncMock(return_value=None)
        store_cls.return_value.async_save = AsyncMock(return_value=None)
        c = BatterySohCalculator(hass, "k")

    # 30 samples over 60 days → above both _MIN_SAMPLES_FOR_SLOPE (30)
    # and _MIN_PROJECTION_AGE_S (30 days).
    _seed_samples(c, count=30, span_days=60.0, slope_wh_per_kwh=-0.1)

    slope = c.degradation_per_kwh
    assert slope is not None
    assert slope == pytest.approx(-0.1, rel=0.01)

    annual = c.annual_throughput_kwh
    assert annual is not None
    assert 1500 < annual < 2000  # 30 samples × 10 kWh = 290 kWh / 60 days

    projection = c.soh_projection_5y_pct
    assert projection is not None
    assert projection < c.soh_pct
    assert c.projection_reliable is True


def test_soh_projection_non_degrading_returns_current_soh():
    hass = MagicMock()
    with patch(
        "custom_components.kostal_kore.battery_soh_calculator._BatterySohStore"
    ) as store_cls:
        store_cls.return_value.async_load = AsyncMock(return_value=None)
        store_cls.return_value.async_save = AsyncMock(return_value=None)
        c = BatterySohCalculator(hass, "k")

    _seed_samples(c, count=30, span_days=60.0, slope_wh_per_kwh=0.0)

    slope = c.degradation_per_kwh
    assert slope == pytest.approx(0.0, abs=1e-9)
    assert c.soh_projection_5y_pct == pytest.approx(100.0)


def test_projection_blocked_until_min_age():
    """Many samples within a short window do not unlock the projection."""
    hass = MagicMock()
    with patch(
        "custom_components.kostal_kore.battery_soh_calculator._BatterySohStore"
    ) as store_cls:
        store_cls.return_value.async_load = AsyncMock(return_value=None)
        store_cls.return_value.async_save = AsyncMock(return_value=None)
        c = BatterySohCalculator(hass, "k")

    # 50 samples in just 5 days — passes the count gate but fails the
    # window gate (must be ≥ 30 days).
    _seed_samples(c, count=50, span_days=5.0, slope_wh_per_kwh=-0.1)

    assert c.projection_reliable is False
    assert c.degradation_per_kwh is None
    assert c.soh_projection_5y_pct is None


def test_sample_interval_throttles_recording():
    hass = MagicMock()
    with patch(
        "custom_components.kostal_kore.battery_soh_calculator._BatterySohStore"
    ) as store_cls:
        store_cls.return_value.async_load = AsyncMock(return_value=None)
        store_cls.return_value.async_save = AsyncMock(return_value=None)
        c = BatterySohCalculator(hass, "k")

    snap = {
        "battery_work_capacity": 35000.0,
        "total_dc_charge": 1000.0,
        "total_dc_discharge": 1000.0,
    }
    c.update_from_modbus(snap)
    initial = c.sample_count
    # Immediate second update should NOT add a new sample
    c.update_from_modbus(snap)
    assert c.sample_count == initial


def test_baseline_unchanged_returns_false_after_first(calc):
    calc.update_from_modbus({"battery_work_capacity": 35000.0})
    # Second update with same value, immediately after — neither baseline
    # raise nor new sample.
    changed = calc.update_from_modbus({"battery_work_capacity": 35000.0})
    assert changed is False


@pytest.mark.asyncio
async def test_load_restores_state():
    hass = MagicMock()
    saved = {
        "baseline_capacity_wh": 36000.0,
        "baseline_set_at": 12345.0,
        "samples": [[10.0, 35900.0, 1000.0], [20.0, 35850.0, 2000.0]],
    }
    with patch(
        "custom_components.kostal_kore.battery_soh_calculator._BatterySohStore"
    ) as store_cls:
        store_cls.return_value.async_load = AsyncMock(return_value=saved)
        store_cls.return_value.async_save = AsyncMock(return_value=None)
        c = BatterySohCalculator(hass, "k")
        await c.async_load()

    assert c.baseline_capacity_wh == 36000.0
    assert c._baseline_set_at == 12345.0
    assert c.sample_count == 2


@pytest.mark.asyncio
async def test_store_migration_v1_to_v2_drops_old_format_samples():
    """v1 stored throughput=charge+discharge; v2 uses discharge only.

    Mixing samples from both formats in one OLS slope would mis-attribute
    degradation. Migration must drop the samples but keep the baseline.
    """
    from custom_components.kostal_kore.battery_soh_calculator import (
        _BatterySohStore,
    )

    store = _BatterySohStore.__new__(_BatterySohStore)
    old_v1_data = {
        "baseline_capacity_wh": 36000.0,
        "baseline_set_at": 12345.0,
        "samples": [
            [100.0, 35900.0, 1000.0],
            [200.0, 35850.0, 2000.0],
            [300.0, 35800.0, 3000.0],
        ],
    }
    migrated = await store._async_migrate_func(1, 1, old_v1_data)
    assert migrated["baseline_capacity_wh"] == 36000.0
    assert migrated["baseline_set_at"] == 12345.0
    assert migrated["samples"] == []


@pytest.mark.asyncio
async def test_store_migration_passthrough_for_current_version():
    """No mutation should happen for stores already at the current version."""
    from custom_components.kostal_kore.battery_soh_calculator import (
        _BatterySohStore,
        _STORE_VERSION,
    )

    store = _BatterySohStore.__new__(_BatterySohStore)
    data = {
        "baseline_capacity_wh": 36000.0,
        "baseline_set_at": 12345.0,
        "samples": [[100.0, 35900.0, 1000.0]],
    }
    out = await store._async_migrate_func(_STORE_VERSION, 1, data)
    assert out is data
    assert out["samples"] == [[100.0, 35900.0, 1000.0]]


@pytest.mark.asyncio
async def test_load_handles_corrupt_samples():
    hass = MagicMock()
    saved = {
        "baseline_capacity_wh": 36000.0,
        "samples": [
            ["nope", "nope", "nope"],   # un-parseable
            [10.0, 35900.0, 1000.0],    # valid
            "garbage",                    # not a list
            [10.0, 35850.0],              # wrong length
        ],
    }
    with patch(
        "custom_components.kostal_kore.battery_soh_calculator._BatterySohStore"
    ) as store_cls:
        store_cls.return_value.async_load = AsyncMock(return_value=saved)
        store_cls.return_value.async_save = AsyncMock(return_value=None)
        c = BatterySohCalculator(hass, "k")
        await c.async_load()

    assert c.sample_count == 1


@pytest.mark.asyncio
async def test_debounced_save_calls_async_save(hass):
    """Debounced save task must eventually persist after the sleep window."""
    with patch(
        "custom_components.kostal_kore.battery_soh_calculator._BatterySohStore"
    ) as store_cls:
        save_mock = AsyncMock(return_value=None)
        store_cls.return_value.async_load = AsyncMock(return_value=None)
        store_cls.return_value.async_save = save_mock
        calc = BatterySohCalculator(hass, "k")
        with patch(
            "custom_components.kostal_kore.battery_soh_calculator.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            calc.schedule_save()
            task = calc._save_task
            assert task is not None
            await task
    save_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_schedule_save_coalesces_concurrent_calls(hass):
    """schedule_save must not spawn unbounded save tasks during baseline calibration."""
    with patch(
        "custom_components.kostal_kore.battery_soh_calculator._BatterySohStore"
    ) as store_cls:
        store_cls.return_value.async_load = AsyncMock(return_value=None)
        store_cls.return_value.async_save = AsyncMock(return_value=None)
        calc = BatterySohCalculator(hass, "k")
        calc.schedule_save()
        calc.schedule_save()
        assert calc._save_task is not None
        calc._save_task.cancel()
        try:
            await calc._save_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_save_writes_all_state():
    hass = MagicMock()
    with patch(
        "custom_components.kostal_kore.battery_soh_calculator._BatterySohStore"
    ) as store_cls:
        save_mock = AsyncMock(return_value=None)
        store_cls.return_value.async_load = AsyncMock(return_value=None)
        store_cls.return_value.async_save = save_mock
        c = BatterySohCalculator(hass, "k")
        c.update_from_modbus({
            "battery_work_capacity": 35000.0,
            "total_dc_charge": 500.0,
            "total_dc_discharge": 400.0,
        })
        await c.async_save()

    save_mock.assert_awaited_once()
    saved = save_mock.call_args.args[0]
    assert saved["baseline_capacity_wh"] == 35000.0
    assert len(saved["samples"]) == 1
    # Only discharge counts on the throughput axis (400/1000 = 0.4)
    assert saved["samples"][0][:2] == [pytest.approx(0.4), 35000.0]


@pytest.mark.asyncio
async def test_save_failure_is_swallowed():
    """Storage write errors must not crash the calculator (line 139-140)."""
    hass = MagicMock()
    with patch(
        "custom_components.kostal_kore.battery_soh_calculator._BatterySohStore"
    ) as store_cls:
        store_cls.return_value.async_load = AsyncMock(return_value=None)
        store_cls.return_value.async_save = AsyncMock(
            side_effect=OSError("disk full")
        )
        c = BatterySohCalculator(hass, "k")
        # async_save must NOT raise even though the underlying store fails
        await c.async_save()


def test_total_throughput_kwh_none_when_partial(calc):
    """charge_kwh present but discharge_kwh absent should yield None (line 222)."""
    # _latest_charge_kwh and _latest_discharge_kwh both start None
    assert calc.total_throughput_kwh is None
    # Set only charge — still None
    calc._latest_charge_kwh = 5.0
    assert calc.total_throughput_kwh is None
    # Set only discharge — still None
    calc._latest_charge_kwh = None
    calc._latest_discharge_kwh = 5.0
    assert calc.total_throughput_kwh is None
    # Both set — value
    calc._latest_charge_kwh = 5.0
    calc._latest_discharge_kwh = 4.0
    assert calc.total_throughput_kwh == pytest.approx(9.0)


def test_baseline_age_days_none_until_set(calc):
    """baseline_age_days returns None before any observation (lines 235-237)."""
    assert calc.baseline_age_days is None
    calc.update_from_modbus({"battery_work_capacity": 35000.0})
    assert calc.baseline_age_days is not None
    assert calc.baseline_age_days >= 0.0


def test_has_min_window_false_with_single_sample(calc):
    """_has_min_window must return False with fewer than 2 samples (line 272)."""
    # Empty deque
    assert calc._has_min_window() is False
    # One sample only
    calc._samples.append((10.0, 35000.0, 1000.0))
    assert calc._has_min_window() is False
    # Two samples spanning under the min age
    calc._samples.append((20.0, 34900.0, 1500.0))
    assert calc._has_min_window() is False


def test_degradation_per_kwh_none_when_window_too_short(calc):
    """Sufficient samples but window < 30 days returns None (line 266)."""
    import time as _t
    now = _t.time()
    # 35 samples spanning only 5 days — fails the window gate
    for i in range(35):
        ts = now - (5 - i * (5 / 34)) * 86400
        calc._samples.append((i * 10.0, 35000.0 - i, ts))
    calc._baseline_capacity_wh = 35000.0
    calc._current_capacity_wh = 34965.0
    assert calc.degradation_per_kwh is None


def test_degradation_slope_none_when_all_x_identical(calc):
    """If every sample has the same throughput, denom == 0 and slope is None (line 266)."""
    import time as _t
    now = _t.time()
    for i in range(30):
        ts = now - (60 - i * (60 / 29)) * 86400
        # SAME x value for every sample — OLS denominator collapses to zero
        calc._samples.append((100.0, 35000.0 + i, ts))
    assert calc.degradation_per_kwh is None


def test_annual_throughput_none_when_timestamps_equal(calc):
    """If oldest and newest sample share a timestamp, the rate is undefined (line 295)."""
    fixed_ts = 1_700_000_000.0
    calc._samples.append((0.0, 35000.0, fixed_ts))
    calc._samples.append((10.0, 34999.0, fixed_ts))
    assert calc.annual_throughput_kwh is None


def test_projection_returns_current_soh_when_slope_positive(calc):
    """Non-degrading (positive or zero slope) projects to current SoH (line 295)."""
    import time as _t
    now = _t.time()
    # Mock a positive slope by seeding samples where capacity GROWS with throughput
    for i in range(30):
        ts = now - (60 - i * (60 / 29)) * 86400
        # Positive slope: capacity increases by 0.05 Wh per kWh
        calc._samples.append((i * 10.0, 35000.0 + i * 0.5, ts))
    calc._baseline_capacity_wh = 35000.0
    calc._baseline_set_at = now - 60 * 86400
    calc._current_capacity_wh = calc._samples[-1][1]
    slope = calc.degradation_per_kwh
    assert slope is not None
    assert slope > 0
    # When slope is non-negative, projection should fall back to current SoH
    assert calc.soh_projection_5y_pct == pytest.approx(calc.soh_pct)


def test_load_failure_keeps_calculator_empty():
    """A storage read failure must not raise — calculator stays empty."""
    hass = MagicMock()
    with patch(
        "custom_components.kostal_kore.battery_soh_calculator._BatterySohStore"
    ) as store_cls:
        store_cls.return_value.async_load = AsyncMock(
            side_effect=OSError("boom")
        )
        store_cls.return_value.async_save = AsyncMock(return_value=None)
        c = BatterySohCalculator(hass, "k")

        # Run async_load manually since pytest-asyncio fixture not used here.
        import asyncio
        asyncio.run(c.async_load())

    assert c.baseline_capacity_wh is None
    assert c.sample_count == 0
