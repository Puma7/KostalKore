"""Tests for the battery SoH calculator."""

from __future__ import annotations

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
        "custom_components.kostal_kore.battery_soh_calculator.Store"
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
    assert calc.total_throughput_kwh == pytest.approx(900.0)


def test_rejects_zero_or_negative_capacity(calc):
    assert calc.update_from_modbus({"battery_work_capacity": 0}) is False
    assert calc.update_from_modbus({"battery_work_capacity": -5.0}) is False
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


def test_soh_projection_with_linear_degradation():
    hass = MagicMock()
    with patch(
        "custom_components.kostal_kore.battery_soh_calculator.Store"
    ) as store_cls:
        store_cls.return_value.async_load = AsyncMock(return_value=None)
        store_cls.return_value.async_save = AsyncMock(return_value=None)
        c = BatterySohCalculator(hass, "k")

    # Simulate 6 samples spread over 60 days, linearly losing capacity.
    # 50 kWh annual throughput, losing 100 Wh per 1000 kWh throughput.
    now = time.time()
    for i in range(6):
        ts = now - (60 - i * 12) * 86400  # spread over 60 days
        throughput_kwh = i * 10.0  # 10 kWh per sample
        capacity_wh = 35000.0 - (throughput_kwh * 0.1)  # 0.1 Wh per kWh = 100 mWh
        c._samples.append((throughput_kwh, capacity_wh, ts))

    c._baseline_capacity_wh = 35000.0
    c._baseline_set_at = now - 60 * 86400
    c._current_capacity_wh = 35000.0 - 5.0  # 5 Wh lost so far

    slope = c.degradation_per_kwh
    assert slope is not None
    assert slope == pytest.approx(-0.1, rel=0.01)

    annual = c.annual_throughput_kwh
    assert annual is not None
    # 50 kWh over 60 days × 365.25/60 ≈ 304 kWh/year
    assert 250 < annual < 350

    projection = c.soh_projection_5y_pct
    assert projection is not None
    assert projection < c.soh_pct  # must project a loss


def test_soh_projection_non_degrading_returns_current_soh():
    hass = MagicMock()
    with patch(
        "custom_components.kostal_kore.battery_soh_calculator.Store"
    ) as store_cls:
        store_cls.return_value.async_load = AsyncMock(return_value=None)
        store_cls.return_value.async_save = AsyncMock(return_value=None)
        c = BatterySohCalculator(hass, "k")

    # Constant capacity over time — slope is 0 → not degrading
    now = time.time()
    for i in range(6):
        c._samples.append((i * 10.0, 35000.0, now - (60 - i * 12) * 86400))
    c._baseline_capacity_wh = 35000.0
    c._current_capacity_wh = 35000.0

    slope = c.degradation_per_kwh
    assert slope == pytest.approx(0.0, abs=1e-9)
    assert c.soh_projection_5y_pct == pytest.approx(100.0)


def test_sample_interval_throttles_recording():
    hass = MagicMock()
    with patch(
        "custom_components.kostal_kore.battery_soh_calculator.Store"
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
        "custom_components.kostal_kore.battery_soh_calculator.Store"
    ) as store_cls:
        store_cls.return_value.async_load = AsyncMock(return_value=saved)
        store_cls.return_value.async_save = AsyncMock(return_value=None)
        c = BatterySohCalculator(hass, "k")
        await c.async_load()

    assert c.baseline_capacity_wh == 36000.0
    assert c._baseline_set_at == 12345.0
    assert c.sample_count == 2


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
        "custom_components.kostal_kore.battery_soh_calculator.Store"
    ) as store_cls:
        store_cls.return_value.async_load = AsyncMock(return_value=saved)
        store_cls.return_value.async_save = AsyncMock(return_value=None)
        c = BatterySohCalculator(hass, "k")
        await c.async_load()

    assert c.sample_count == 1


@pytest.mark.asyncio
async def test_save_writes_all_state():
    hass = MagicMock()
    with patch(
        "custom_components.kostal_kore.battery_soh_calculator.Store"
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
    assert saved["samples"][0][:2] == [pytest.approx(0.9), 35000.0]


def test_load_failure_keeps_calculator_empty():
    """A storage read failure must not raise — calculator stays empty."""
    hass = MagicMock()
    with patch(
        "custom_components.kostal_kore.battery_soh_calculator.Store"
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
