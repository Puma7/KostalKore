"""Tests for the battery SoH sensor entities."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.kostal_kore.battery_soh_entities import (
    BatterySohCalculatedSensor,
    BatterySohProjection5yearsSensor,
    create_battery_soh_sensors,
)


def _device_info():
    from homeassistant.helpers.device_registry import DeviceInfo
    return DeviceInfo(identifiers={("kostal_kore", "test")})


def _calc_stub(
    *,
    soh_pct=None,
    projection_5y=None,
    baseline_wh=None,
    current_wh=None,
    age_days=None,
    discharge_kwh=None,
    charge_kwh=None,
    cycles=None,
    samples=0,
    degradation=None,
    annual=None,
    reliable=False,
):
    calc = MagicMock()
    calc.soh_pct = soh_pct
    calc.soh_projection_5y_pct = projection_5y
    calc.baseline_capacity_wh = baseline_wh
    calc.current_capacity_wh = current_wh
    calc.baseline_age_days = age_days
    calc.total_discharge_kwh = discharge_kwh
    calc.total_charge_kwh = charge_kwh
    calc.cycles = cycles
    calc.sample_count = samples
    calc.degradation_per_kwh = degradation
    calc.annual_throughput_kwh = annual
    calc.projection_reliable = reliable
    return calc


def _coordinator():
    coord = MagicMock()
    coord.last_update_success = True
    return coord


def test_calculated_sensor_unavailable_when_no_data():
    sensor = BatterySohCalculatedSensor.__new__(BatterySohCalculatedSensor)
    sensor._calc = _calc_stub(soh_pct=None)
    assert sensor.available is False
    assert sensor.native_value is None


def test_calculated_sensor_rounds_native_value_and_emits_source_attr():
    sensor = BatterySohCalculatedSensor.__new__(BatterySohCalculatedSensor)
    sensor._calc = _calc_stub(
        soh_pct=98.7654,
        baseline_wh=36000.0,
        current_wh=35555.5,
        age_days=12.3456,
        discharge_kwh=1234.567,
        charge_kwh=1500.0,
        cycles=76.0,
        samples=42,
    )
    assert sensor.available is True
    assert sensor.native_value == pytest.approx(98.77)
    attrs = sensor.extra_state_attributes
    assert attrs["source"] == "calculated_capacity_ratio"
    assert attrs["baseline_wh"] == 36000.0
    assert attrs["current_wh"] == 35555.5
    assert attrs["baseline_age_days"] == 12.3
    assert attrs["total_discharge_kwh"] == 1234.6
    assert attrs["total_charge_kwh"] == 1500.0
    assert attrs["cycles_observed"] == 76.0
    assert attrs["samples"] == 42


def test_calculated_sensor_handles_partial_none_attributes():
    sensor = BatterySohCalculatedSensor.__new__(BatterySohCalculatedSensor)
    sensor._calc = _calc_stub(
        soh_pct=100.0,
        baseline_wh=36000.0,
        current_wh=36000.0,
        age_days=None,
        discharge_kwh=None,
        charge_kwh=None,
        cycles=None,
        samples=1,
    )
    attrs = sensor.extra_state_attributes
    assert attrs["baseline_age_days"] is None
    assert attrs["total_discharge_kwh"] is None
    assert attrs["total_charge_kwh"] is None


def test_projection_sensor_unavailable_until_min_window():
    sensor = BatterySohProjection5yearsSensor.__new__(
        BatterySohProjection5yearsSensor
    )
    sensor._calc = _calc_stub(projection_5y=None, reliable=False)
    assert sensor.available is False
    assert sensor.native_value is None


def test_projection_sensor_exposes_reliability_and_slope():
    sensor = BatterySohProjection5yearsSensor.__new__(
        BatterySohProjection5yearsSensor
    )
    sensor._calc = _calc_stub(
        projection_5y=93.4567,
        degradation=-0.12345,
        annual=1800.5,
        samples=45,
        reliable=True,
    )
    assert sensor.available is True
    assert sensor.native_value == pytest.approx(93.46)
    attrs = sensor.extra_state_attributes
    assert attrs["source"] == "calculated_ols_extrapolation"
    assert attrs["degradation_per_kwh"] == -0.1235
    assert attrs["annual_discharge_kwh"] == 1800.5
    assert attrs["samples"] == 45
    assert attrs["projection_reliable"] is True


def test_projection_sensor_handles_none_slope_and_annual():
    sensor = BatterySohProjection5yearsSensor.__new__(
        BatterySohProjection5yearsSensor
    )
    sensor._calc = _calc_stub(
        projection_5y=100.0,
        degradation=None,
        annual=None,
        samples=5,
        reliable=False,
    )
    attrs = sensor.extra_state_attributes
    assert attrs["degradation_per_kwh"] is None
    assert attrs["annual_discharge_kwh"] is None


def test_factory_creates_both_sensors_with_unique_ids():
    coord = _coordinator()
    calc = _calc_stub()
    entities = create_battery_soh_sensors(coord, calc, "entry42", _device_info())
    assert len(entities) == 2

    uids = {e._attr_unique_id for e in entities}
    assert uids == {
        "entry42_battery_soh_calculated",
        "entry42_battery_soh_projection_5y",
    }

    # Each entity is wired to the same coordinator and calculator
    for entity in entities:
        assert entity._calc is calc
        assert entity.coordinator is coord
