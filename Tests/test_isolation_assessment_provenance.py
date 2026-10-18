"""Distinguish historical isolation warnings from available device measurements."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.kostal_kore.health_binary_sensor import IsolationWarning
from custom_components.kostal_kore.health_monitor import HealthLevel, InverterHealthMonitor
from custom_components.kostal_kore.helper import ISOLATION_SENTINEL_OHM
from custom_components.kostal_kore.modbus_coordinator import ModbusDataUpdateCoordinator


@pytest.mark.parametrize("next_reading,expected_level", [
    (0.0, HealthLevel.CRITICAL), (5_000_000.0, HealthLevel.GOOD),
    (ISOLATION_SENTINEL_OHM, HealthLevel.GOOD),
])
async def test_restored_low_sample_stays_historical_until_valid_poll(
    hass: HomeAssistant, next_reading, expected_level,
):
    client = MagicMock(host="127.0.0.1", port=1502, unit_id=71)
    coordinator = ModbusDataUpdateCoordinator(hass, client)
    monitor = InverterHealthMonitor()
    coordinator._health_monitor = monitor
    coordinator._isolation_store.async_load = AsyncMock(
        return_value={"isolation_ohm": 10_000.0, "saved_at": time.time()},
    )
    await coordinator._restore_isolation_sample()
    attrs = monitor.isolation_modbus_attributes()
    assert attrs["assessment_basis"] == "historical"
    assert attrs["sample_origin"] == "restored"
    assert attrs["current_measurement_ohm"] is None
    assert monitor.get_isolation_resistance_ohm() is None
    warning = IsolationWarning(monitor, "synthetic", {})
    assert warning.is_on is True

    monitor.update_from_modbus({
        "isolation_resistance": ISOLATION_SENTINEL_OHM,
        "total_dc_power": 0.0, "inverter_state": 0, "grid_frequency": 50.0,
    })
    summary = monitor.get_health_summary()
    attrs = summary["isolation_measurement"]
    assert monitor.get_isolation_resistance_ohm() is None
    assert attrs["current_measurement_ohm"] is None
    assert attrs["last_recorded_ohm"] == 10_000.0
    assert attrs["kostal_display_mohm"] is None
    assert attrs["assessment_basis"] == "historical"
    assert attrs["modbus_measurement_unavailable"] is True
    assert summary["overall_health"] == "critical"
    assert summary["health_score"] == 80
    assert monitor.isolation.sample_count == 1
    assert warning.is_on is True
    assert warning.extra_state_attributes["assessment_basis"] == "historical"

    monitor.update_from_modbus({
        "isolation_resistance": next_reading, "total_dc_power": 1500.0,
        "inverter_state": 6, "grid_frequency": 50.0,
    })
    attrs = monitor.get_health_summary()["isolation_measurement"]
    assert attrs["assessment_basis"] == "current"
    assert attrs["sample_origin"] == "modbus"
    assert attrs["current_measurement_ohm"] == next_reading
    assert monitor.overall_health == expected_level
    assert warning.is_on is (expected_level == HealthLevel.CRITICAL)


def test_empty_monitor_has_no_current_or_historical_isolation():
    attrs = InverterHealthMonitor().get_health_summary()["isolation_measurement"]
    assert attrs["assessment_basis"] == "unavailable"
    assert attrs["sample_origin"] == "none"
    assert attrs["last_recorded_ohm"] is None
    assert attrs["current_measurement_ohm"] is None


def test_modbus_sample_becomes_historical_in_standby_without_clearing_warning():
    monitor = InverterHealthMonitor()
    monitor.update_from_modbus({
        "isolation_resistance": 0.0, "total_dc_power": 1500.0, "inverter_state": 6,
    })
    assert monitor.isolation_modbus_attributes()["assessment_basis"] == "current"
    monitor.update_from_modbus({
        "isolation_resistance": ISOLATION_SENTINEL_OHM,
        "total_dc_power": 0.0, "inverter_state": 0,
    })
    attrs = monitor.isolation_modbus_attributes()
    assert attrs["sample_origin"] == "modbus"
    assert attrs["assessment_basis"] == "historical"
    assert attrs["last_recorded_ohm"] == 0
    assert attrs["current_measurement_ohm"] is None
    assert monitor.overall_health == HealthLevel.CRITICAL


@pytest.mark.parametrize("previous", [0.0, 5_000_000.0])
@pytest.mark.parametrize("frame,is_current", [
    ({}, True), ({"isolation_resistance": None}, True),
    ({"isolation_resistance": "invalid"}, True),
    ({"isolation_resistance": float("nan")}, True),
    ({"isolation_resistance": float("inf")}, True),
    ({"isolation_resistance": float("-inf")}, True),
    ({"isolation_resistance": 1_000_000.0}, False),
])
def test_missing_invalid_or_quality_retained_poll_is_historical(previous, frame, is_current):
    monitor = InverterHealthMonitor()
    monitor.update_from_modbus({
        "isolation_resistance": previous, "total_dc_power": 1500.0, "inverter_state": 6,
    })
    old_level = monitor.overall_health
    old_score = monitor.health_score
    monitor.update_from_modbus(
        {"total_dc_power": 1500.0, "inverter_state": 6, **frame},
        isolation_current=is_current,
    )
    attrs = monitor.isolation_modbus_attributes()
    assert monitor.get_isolation_resistance_ohm() is None
    assert attrs["assessment_basis"] == "historical"
    assert attrs["current_measurement_ohm"] is None
    assert attrs["modbus_raw_ohm"] is None
    assert attrs["last_recorded_ohm"] == previous
    assert monitor.isolation.sample_count == 1
    assert monitor.overall_health == old_level
    assert monitor.health_score == old_score
    monitor.update_from_modbus({
        "isolation_resistance": previous, "total_dc_power": 1500.0, "inverter_state": 6,
    })
    assert monitor.isolation_modbus_attributes()["assessment_basis"] == "current"


def test_isolation_only_invalidation_preserves_other_tracker_context():
    monitor = InverterHealthMonitor()
    monitor.update_from_modbus({
        "isolation_resistance": 0.0, "total_dc_power": 1500.0,
        "inverter_state": 6, "grid_frequency": 60.0,
        "phase1_voltage": 120.0, "phase2_voltage": 120.0, "phase3_voltage": 120.0,
    })
    frequency = monitor.grid_frequency.current
    threshold = monitor.grid_frequency.critical_high
    samples = monitor.grid_frequency.sample_count
    polls = monitor._total_polls
    monitor.invalidate_isolation_measurement()
    assert monitor.get_isolation_resistance_ohm() is None
    assert monitor.isolation_modbus_attributes()["assessment_basis"] == "historical"
    assert monitor.grid_frequency.current == frequency
    assert monitor.grid_frequency.critical_high == threshold
    assert monitor.grid_frequency.sample_count == samples
    assert monitor._total_polls == polls
    assert monitor.overall_health == HealthLevel.CRITICAL
