"""Regression tests for edge cases found in the November 2026 system review."""

from __future__ import annotations

import re

import pytest
from homeassistant.components.diagnostics import async_redact_data

from custom_components.kostal_kore.diagnostics import TO_REDACT
from custom_components.kostal_kore.helper import device_configuration_url
from custom_components.kostal_kore.sensor import (
    _CALCULATED_STATISTIC_INPUTS,
    SENSOR_PROCESS_DATA,
    PlenticoreCalculatedSensor,
)


@pytest.mark.parametrize(
    ("host", "url"),
    [
        ("192.168.1.5", "http://192.168.1.5"),
        ("kostal.local", "http://kostal.local"),
        (" kostal.local ", "http://kostal.local"),
        ("2001:db8::5", "http://[2001:db8::5]"),
        ("[2001:db8::5]", None),
        ("host:8080", None),
    ],
)
def test_device_configuration_url(host: str, url: str | None) -> None:
    """An IPv6 host gets brackets; a host yarl rejects gets no link."""
    assert device_configuration_url(host) == url


def _calculated_descriptions() -> list[object]:
    return [d for d in SENSOR_PROCESS_DATA if d.module_id == "_calc_"]


def _calculated_sensor(data_id: str) -> PlenticoreCalculatedSensor:
    sensor = PlenticoreCalculatedSensor.__new__(PlenticoreCalculatedSensor)
    sensor.data_id = data_id
    return sensor


def test_every_calculated_metric_registers_its_inputs() -> None:
    """Each calculated statistic names the inputs native_value reads."""
    metrics = {d.key.split(":")[0] for d in _calculated_descriptions()}
    assert metrics
    assert metrics <= set(_CALCULATED_STATISTIC_INPUTS)


@pytest.mark.parametrize(
    ("data_id", "inputs"),
    [
        (
            "TotalGridConsumption:Total",
            ["Statistic:EnergyHomeGrid:Total", "Statistic:EnergyChargeGrid:Total"],
        ),
        (
            "BatteryChargeTotal:Day",
            ["Statistic:EnergyChargeGrid:Day", "Statistic:EnergyChargePv:Day"],
        ),
        ("Unknown:Day", []),
        ("NoPeriod", []),
    ],
)
def test_calculated_sensor_input_ids(data_id: str, inputs: list[str]) -> None:
    sensor = _calculated_sensor(data_id)
    assert sensor._required_input_ids() == inputs


def test_calculated_inputs_match_what_native_value_reads() -> None:
    """The table matches the statistic ids sensor.py reads per metric."""
    import inspect

    from custom_components.kostal_kore import sensor as sensor_module

    source = inspect.getsource(sensor_module.PlenticoreCalculatedSensor.native_value.fget)
    branches = re.split(r'metric == "(\w+)"', source)[1:]
    read: dict[str, set[str]] = {}
    for metric, body in zip(branches[::2], branches[1::2], strict=True):
        read[metric] = set(re.findall(r"Statistic:(\w+):", body))
    assert read
    for metric, names in read.items():
        assert set(_CALCULATED_STATISTIC_INPUTS[metric]) == names, metric


def test_debug_bundle_redacts_the_rest_battery_serial() -> None:
    bundle = {
        "rest_snapshot": {
            "devices:local:battery": {"BatSerialNo": "SN123", "SoC": "55"},
        },
        "settings": {"devices:local": {"Properties:SerialNo": "SN456"}},
    }
    redacted = async_redact_data(bundle, TO_REDACT)
    battery = redacted["rest_snapshot"]["devices:local:battery"]
    assert battery["BatSerialNo"] == "**REDACTED**"
    assert battery["SoC"] == "55"
    assert redacted["settings"]["devices:local"]["Properties:SerialNo"] == "**REDACTED**"


def test_decimal_step_numbers_are_not_read_back_rounded() -> None:
    """A 0.01/0.1 step setting (cos phi, voltages) must keep its decimals."""
    from custom_components.kostal_kore.helper import PlenticoreDataFormatter
    from custom_components.kostal_kore.number import NUMBER_SETTINGS_DATA

    decimal = [d for d in NUMBER_SETTINGS_DATA if d.native_step is not None and d.native_step < 1]
    assert decimal
    for description in decimal:
        read = PlenticoreDataFormatter.get_method(description.fmt_from)
        assert read("0.35") == pytest.approx(0.35), description.key
