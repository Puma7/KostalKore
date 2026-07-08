"""Tests for write_audit.py and observability_entities.py."""

from __future__ import annotations

import time
from types import SimpleNamespace  # noqa: F401
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: F401

import pytest

from custom_components.kostal_kore.write_audit import WriteAuditLog, WriteEvent

# ---------------------------------------------------------------------------
# WriteAuditLog tests
# ---------------------------------------------------------------------------


def _event(result: str = "ok", offset_s: float = 0.0) -> WriteEvent:
    return WriteEvent(
        ts=time.monotonic() - offset_s,
        source="modbus_coord",
        key="bat_charge",
        value=5000,
        result=result,
    )


def test_write_audit_log_basic():
    audit = WriteAuditLog(maxlen=10)
    assert audit.total_count == 0
    assert audit.write_rate_per_min == 0.0
    assert audit.error_count_5min == 0
    assert audit.recent == []


def test_write_audit_log_appends_and_returns_copy():
    audit = WriteAuditLog(maxlen=5)
    e = _event()
    audit.log(e)
    recent = audit.recent
    assert len(recent) == 1
    assert recent[0] is e
    # copy — modifying it doesn't affect the buffer
    recent.clear()
    assert audit.total_count == 1


def test_write_audit_log_respects_maxlen():
    audit = WriteAuditLog(maxlen=3)
    for i in range(5):
        audit.log(WriteEvent(ts=time.monotonic(), source="mqtt", key=f"r{i}",
                              value=i, result="ok"))
    assert audit.total_count == 3
    assert audit.recent[0].key == "r2"
    assert audit.recent[-1].key == "r4"


def test_write_audit_error_count_5min_counts_errors_and_rejections():
    audit = WriteAuditLog()
    audit.log(_event("ok"))
    audit.log(_event("error"))
    audit.log(_event("rejected_rate"))
    audit.log(_event("rejected_soc_active"))
    audit.log(_event("rejected_installer"))
    audit.log(_event("rejected_validation"))
    audit.log(_event("forwarded_direct"))
    # "ok" and "forwarded_direct" should NOT count
    assert audit.error_count_5min == 5


def test_write_audit_error_count_5min_excludes_old_events():
    audit = WriteAuditLog()
    # event older than 5 minutes
    old = WriteEvent(ts=time.monotonic() - 400, source="mqtt",
                     key="r", value=1, result="error")
    audit.log(old)
    audit.log(_event("error"))
    assert audit.error_count_5min == 1


def test_write_audit_write_rate_per_min_counts_all_last_60s():
    audit = WriteAuditLog()
    for _ in range(4):
        audit.log(_event("ok"))
    audit.log(_event("error"))
    # one old event outside 60 s window
    audit.log(WriteEvent(ts=time.monotonic() - 70, source="proxy_fc06",
                          key="r", value=0, result="ok"))
    assert audit.writes_in_last_n_seconds(60) == 5
    assert audit.write_rate_per_min == 5.0


def test_write_event_as_dict_contains_iso_ts():
    e = WriteEvent(ts=time.monotonic(), source="proxy_fwd", key="addr:1034",
                   value=None, result="forwarded_direct", detail="FC06")
    d = e.as_dict()
    assert "ts_iso" in d
    assert d["source"] == "proxy_fwd"
    assert d["result"] == "forwarded_direct"
    assert d["detail"] == "FC06"


# ---------------------------------------------------------------------------
# WriteAuditSensor tests (entity unit tests)
# ---------------------------------------------------------------------------


def _make_coordinator():
    coord = MagicMock()
    coord.data = {}
    coord.update_count = 42
    coord.poll_phase = 3
    coord.slow_data_age_s = 12.5
    coord._fast_error_count = 0
    return coord


def test_write_audit_sensor_native_value():
    from custom_components.kostal_kore.observability_entities import WriteAuditSensor

    audit = WriteAuditLog()
    for _ in range(3):
        audit.log(_event("ok"))

    coord = _make_coordinator()
    sensor = WriteAuditSensor.__new__(WriteAuditSensor)
    sensor._audit = audit
    sensor.coordinator = coord

    val = sensor.native_value
    assert val == 3.0


def test_write_audit_sensor_extra_state_attributes():
    from custom_components.kostal_kore.observability_entities import WriteAuditSensor

    audit = WriteAuditLog()
    audit.log(_event("ok"))
    audit.log(_event("error"))

    coord = _make_coordinator()
    sensor = WriteAuditSensor.__new__(WriteAuditSensor)
    sensor._audit = audit
    sensor.coordinator = coord

    attrs = sensor.extra_state_attributes
    assert "last_10_writes" in attrs
    assert len(attrs["last_10_writes"]) == 2
    assert attrs["total_count"] == 2
    assert attrs["error_count_5min"] == 1


# ---------------------------------------------------------------------------
# RequestSchedulerSensor tests
# ---------------------------------------------------------------------------


def test_request_scheduler_sensor_native_value():
    from custom_components.kostal_kore.observability_entities import RequestSchedulerSensor

    scheduler = MagicMock()
    scheduler.get_stats.return_value = {
        "total_requests": 150,
        "waits": 5,
        "timeouts": 0,
        "lock_held": False,
    }

    coord = _make_coordinator()
    sensor = RequestSchedulerSensor.__new__(RequestSchedulerSensor)
    sensor._scheduler = scheduler
    sensor.coordinator = coord

    assert sensor.native_value == 150
    attrs = sensor.extra_state_attributes
    assert attrs["waits"] == 5
    assert attrs["timeouts"] == 0
    assert attrs["lock_held"] is False


# ---------------------------------------------------------------------------
# ModbusCoordinatorSensor tests
# ---------------------------------------------------------------------------


def test_modbus_coordinator_sensor_native_value_and_attrs():
    from custom_components.kostal_kore.observability_entities import ModbusCoordinatorSensor

    coord = _make_coordinator()
    sensor = ModbusCoordinatorSensor.__new__(ModbusCoordinatorSensor)
    sensor.coordinator = coord

    assert sensor.native_value == 42
    attrs = sensor.extra_state_attributes
    assert attrs["poll_phase"] == 3
    assert attrs["slow_data_age_s"] == 12.5
    assert attrs["fast_error_count"] == 0


def test_modbus_coordinator_sensor_slow_data_age_none():
    from custom_components.kostal_kore.observability_entities import ModbusCoordinatorSensor

    coord = _make_coordinator()
    coord.slow_data_age_s = None
    sensor = ModbusCoordinatorSensor.__new__(ModbusCoordinatorSensor)
    sensor.coordinator = coord

    attrs = sensor.extra_state_attributes
    assert attrs["slow_data_age_s"] is None


# ---------------------------------------------------------------------------
# RestModbusConsistencySensor tests
# ---------------------------------------------------------------------------


def _make_rest_coord(modules: dict) -> MagicMock:
    coord = MagicMock()
    coord.data = modules
    return coord


def _make_modbus_coord_with_data(data: dict) -> MagicMock:
    coord = _make_coordinator()
    coord.data = data
    return coord


def test_consistency_sensor_ok_all_three_pairs():
    from custom_components.kostal_kore.observability_entities import RestModbusConsistencySensor

    rest = _make_rest_coord({
        "devices:local:battery": {"SoC": "67"},
        "devices:local": {"Dc_P": "4200", "Home_P": "1500"},
    })
    modbus = _make_modbus_coord_with_data({
        "battery_soc": 67.5,
        "total_dc_power": 4190.0,
        "home_from_pv": 1000.0,
        "home_from_battery": 300.0,
        "home_from_grid": 200.0,
    })

    sensor = RestModbusConsistencySensor.__new__(RestModbusConsistencySensor)
    sensor._process_coord = rest
    sensor.coordinator = modbus

    assert sensor.native_value == "ok"


def test_consistency_sensor_mismatch_soc():
    from custom_components.kostal_kore.observability_entities import RestModbusConsistencySensor

    rest = _make_rest_coord({
        "devices:local:battery": {"SoC": "80"},
        "devices:local": {"Dc_P": "4200", "Home_P": "1500"},
    })
    modbus = _make_modbus_coord_with_data({
        "battery_soc": 65.0,   # 15 pp off → mismatch
        "total_dc_power": 4200.0,
        "home_from_pv": 1500.0,
        "home_from_battery": 0.0,
        "home_from_grid": 0.0,
    })

    sensor = RestModbusConsistencySensor.__new__(RestModbusConsistencySensor)
    sensor._process_coord = rest
    sensor.coordinator = modbus

    assert sensor.native_value == "mismatch"


def test_consistency_sensor_insufficient_data_when_no_rest_coord():
    from custom_components.kostal_kore.observability_entities import RestModbusConsistencySensor

    modbus = _make_modbus_coord_with_data({"battery_soc": 67.0})

    sensor = RestModbusConsistencySensor.__new__(RestModbusConsistencySensor)
    sensor._process_coord = None
    sensor.coordinator = modbus

    assert sensor.native_value == "insufficient_data"


def test_consistency_sensor_missing_modbus_key_gives_insufficient_data_for_pair():
    from custom_components.kostal_kore.observability_entities import (
        RestModbusConsistencySensor,
        _to_float,  # noqa: F401
    )

    rest = _make_rest_coord({
        "devices:local:battery": {"SoC": "67"},
        "devices:local": {"Dc_P": "4200", "Home_P": "1500"},
    })
    modbus = _make_modbus_coord_with_data({
        # battery_soc missing
        "total_dc_power": 4200.0,
        "home_from_pv": 1500.0,
    })

    sensor = RestModbusConsistencySensor.__new__(RestModbusConsistencySensor)
    sensor._process_coord = rest
    sensor.coordinator = modbus

    pairs = sensor._compute_pairs()
    soc_pair = next(p for p in pairs if p["key"] == "battery_soc")
    assert soc_pair["status"] == "insufficient_data"


def test_consistency_sensor_warn_threshold_power():
    from custom_components.kostal_kore.observability_entities import RestModbusConsistencySensor

    # 8 % deviation for DC power → warn (>5 %, <15 %)
    rest = _make_rest_coord({
        "devices:local:battery": {"SoC": "67"},
        "devices:local": {"Dc_P": "4000", "Home_P": "1500"},
    })
    modbus = _make_modbus_coord_with_data({
        "battery_soc": 67.0,
        "total_dc_power": 3680.0,   # ~8 % off
        "home_from_pv": 1500.0,
        "home_from_battery": 0.0,
        "home_from_grid": 0.0,
    })

    sensor = RestModbusConsistencySensor.__new__(RestModbusConsistencySensor)
    sensor._process_coord = rest
    sensor.coordinator = modbus

    assert sensor.native_value == "warn"


def test_to_float_helper():
    from custom_components.kostal_kore.observability_entities import _to_float

    assert _to_float(None) is None
    assert _to_float("3.14") == pytest.approx(3.14)
    assert _to_float(42) == pytest.approx(42.0)
    assert _to_float("bad") is None


def test_consistency_sensor_get_rest_float_missing_module():
    from custom_components.kostal_kore.observability_entities import RestModbusConsistencySensor

    rest = _make_rest_coord({})  # no modules at all
    modbus = _make_modbus_coord_with_data({"battery_soc": 67.0})

    sensor = RestModbusConsistencySensor.__new__(RestModbusConsistencySensor)
    sensor._process_coord = rest
    sensor.coordinator = modbus

    # All REST keys missing → all insufficient_data
    assert sensor.native_value == "insufficient_data"


def test_consistency_sensor_get_rest_float_missing_key_in_module():
    from custom_components.kostal_kore.observability_entities import RestModbusConsistencySensor

    # Module has other keys but NOT "SoC" → module.get("SoC") returns None (line 181)
    rest = _make_rest_coord({"devices:local:battery": {"other_key": "42"}})
    modbus = _make_modbus_coord_with_data({"battery_soc": 67.0})

    sensor = RestModbusConsistencySensor.__new__(RestModbusConsistencySensor)
    sensor._process_coord = rest
    sensor.coordinator = modbus

    pairs = sensor._compute_pairs()
    soc_pair = next(p for p in pairs if p["key"] == "battery_soc")
    assert soc_pair["status"] == "insufficient_data"


def test_consistency_sensor_get_rest_float_non_numeric_value():
    from custom_components.kostal_kore.observability_entities import RestModbusConsistencySensor

    rest = _make_rest_coord({"devices:local:battery": {"SoC": "not_a_number"}})
    modbus = _make_modbus_coord_with_data({"battery_soc": 67.0})

    sensor = RestModbusConsistencySensor.__new__(RestModbusConsistencySensor)
    sensor._process_coord = rest
    sensor.coordinator = modbus

    pairs = sensor._compute_pairs()
    soc_pair = next(p for p in pairs if p["key"] == "battery_soc")
    assert soc_pair["status"] == "insufficient_data"


def test_consistency_sensor_soc_warn_threshold():
    from custom_components.kostal_kore.observability_entities import RestModbusConsistencySensor

    # SoC 3 pp off → warn (>2 pp, <5 pp)
    rest = _make_rest_coord({
        "devices:local:battery": {"SoC": "70"},
        "devices:local": {"Dc_P": "4000", "Home_P": "1500"},
    })
    modbus = _make_modbus_coord_with_data({
        "battery_soc": 67.0,
        "total_dc_power": 4000.0,
        "home_from_pv": 1500.0,
        "home_from_battery": 0.0,
        "home_from_grid": 0.0,
    })

    sensor = RestModbusConsistencySensor.__new__(RestModbusConsistencySensor)
    sensor._process_coord = rest
    sensor.coordinator = modbus

    pairs = sensor._compute_pairs()
    soc_pair = next(p for p in pairs if p["key"] == "battery_soc")
    assert soc_pair["status"] == "warn"
    assert sensor.native_value == "warn"


def test_consistency_sensor_dc_mismatch_relative():
    from custom_components.kostal_kore.observability_entities import RestModbusConsistencySensor

    # DC power 20 % off → mismatch
    rest = _make_rest_coord({
        "devices:local:battery": {"SoC": "67"},
        "devices:local": {"Dc_P": "5000", "Home_P": "1500"},
    })
    modbus = _make_modbus_coord_with_data({
        "battery_soc": 67.0,
        "total_dc_power": 4000.0,   # 20 % off → mismatch
        "home_from_pv": 1500.0,
        "home_from_battery": 0.0,
        "home_from_grid": 0.0,
    })

    sensor = RestModbusConsistencySensor.__new__(RestModbusConsistencySensor)
    sensor._process_coord = rest
    sensor.coordinator = modbus

    pairs = sensor._compute_pairs()
    dc_pair = next(p for p in pairs if p["key"] == "dc_power_w")
    assert dc_pair["status"] == "mismatch"


def test_consistency_sensor_low_power_no_spurious_mismatch():
    """At low power (<150W absolute delta), large relative %s must NOT trip mismatch."""
    from custom_components.kostal_kore.observability_entities import RestModbusConsistencySensor

    # rest=50W, modbus=60W → delta_abs=10W, delta_pct=16.7% (would be mismatch
    # under pure-relative comparison, but absolute floor must suppress it).
    rest = _make_rest_coord({
        "devices:local:battery": {"SoC": "67"},
        "devices:local": {"Dc_P": "50", "Home_P": "40"},
    })
    modbus = _make_modbus_coord_with_data({
        "battery_soc": 67.0,
        "total_dc_power": 60.0,
        "home_from_pv": 30.0,
        "home_from_battery": 0.0,
        "home_from_grid": 0.0,
    })

    sensor = RestModbusConsistencySensor.__new__(RestModbusConsistencySensor)
    sensor._process_coord = rest
    sensor.coordinator = modbus

    pairs = sensor._compute_pairs()
    dc_pair = next(p for p in pairs if p["key"] == "dc_power_w")
    home_pair = next(p for p in pairs if p["key"] == "home_power_w")
    assert dc_pair["status"] == "ok"
    assert home_pair["status"] == "ok"
    assert sensor.native_value == "ok"


def test_consistency_sensor_partial_when_some_pairs_ok_some_insufficient():
    """native_value == 'partial' when at least one pair is ok AND one is insufficient_data."""
    from custom_components.kostal_kore.observability_entities import RestModbusConsistencySensor

    # SoC REST missing → insufficient_data; DC+Home match → ok
    rest = _make_rest_coord({
        "devices:local": {"Dc_P": "4200", "Home_P": "1500"},
        # no "devices:local:battery" key at all
    })
    modbus = _make_modbus_coord_with_data({
        "battery_soc": 67.0,
        "total_dc_power": 4200.0,
        "home_from_pv": 1500.0,
        "home_from_battery": 0.0,
        "home_from_grid": 0.0,
    })

    sensor = RestModbusConsistencySensor.__new__(RestModbusConsistencySensor)
    sensor._process_coord = rest
    sensor.coordinator = modbus

    assert sensor.native_value == "partial"


def test_consistency_sensor_extra_state_attributes_returns_pairs():
    from custom_components.kostal_kore.observability_entities import RestModbusConsistencySensor

    rest = _make_rest_coord({
        "devices:local:battery": {"SoC": "67"},
        "devices:local": {"Dc_P": "4200", "Home_P": "1500"},
    })
    modbus = _make_modbus_coord_with_data({
        "battery_soc": 67.0,
        "total_dc_power": 4200.0,
        "home_from_pv": 1500.0,
        "home_from_battery": 0.0,
        "home_from_grid": 0.0,
    })

    sensor = RestModbusConsistencySensor.__new__(RestModbusConsistencySensor)
    sensor._process_coord = rest
    sensor.coordinator = modbus

    attrs = sensor.extra_state_attributes
    assert "pairs" in attrs
    assert len(attrs["pairs"]) == 3


# ---------------------------------------------------------------------------
# Modbus coordinator hooks (write audit via coordinator)
# ---------------------------------------------------------------------------


def test_modbus_coordinator_write_hook_logs_ok(monkeypatch):
    """async_write_register should call audit.log with result='ok'."""
    import asyncio
    from unittest.mock import MagicMock

    from custom_components.kostal_kore.modbus_coordinator import ModbusDataUpdateCoordinator

    audit = WriteAuditLog()

    client = MagicMock()
    client.write_register = AsyncMock(return_value=None)

    coord = ModbusDataUpdateCoordinator.__new__(ModbusDataUpdateCoordinator)
    coord._client = client
    coord._write_audit = audit
    coord._last_commanded = {}

    from custom_components.kostal_kore.modbus_registers import Access
    reg = MagicMock()
    reg.access = Access.RW
    reg.name = "bat_charge"

    asyncio.get_event_loop().run_until_complete(coord.async_write_register(reg, 5000))

    assert audit.total_count == 1
    assert audit.recent[0].result == "ok"
    assert audit.recent[0].key == "bat_charge"
    assert audit.recent[0].value == 5000


def test_modbus_coordinator_write_hook_logs_error_on_exception(monkeypatch):
    import asyncio
    from unittest.mock import MagicMock

    from custom_components.kostal_kore.modbus_client import ModbusClientError
    from custom_components.kostal_kore.modbus_coordinator import ModbusDataUpdateCoordinator

    audit = WriteAuditLog()

    client = MagicMock()
    client.write_register = AsyncMock(side_effect=ModbusClientError("timeout"))

    coord = ModbusDataUpdateCoordinator.__new__(ModbusDataUpdateCoordinator)
    coord._client = client
    coord._write_audit = audit

    from custom_components.kostal_kore.modbus_registers import Access
    reg = MagicMock()
    reg.access = Access.RW
    reg.name = "bat_charge"

    with pytest.raises(ModbusClientError):
        asyncio.get_event_loop().run_until_complete(coord.async_write_register(reg, 5000))

    assert audit.total_count == 1
    assert audit.recent[0].result == "error"
    assert "timeout" in audit.recent[0].detail


def test_modbus_coordinator_write_register_honors_audit_source():
    """audit_source parameter must override default 'modbus_coord' tag."""
    import asyncio
    from unittest.mock import MagicMock

    from custom_components.kostal_kore.modbus_coordinator import ModbusDataUpdateCoordinator
    from custom_components.kostal_kore.modbus_registers import Access

    audit = WriteAuditLog()
    client = MagicMock()
    client.write_register = AsyncMock(return_value=None)

    coord = ModbusDataUpdateCoordinator.__new__(ModbusDataUpdateCoordinator)
    coord._client = client
    coord._write_audit = audit
    coord._last_commanded = {}

    reg = MagicMock()
    reg.access = Access.RW
    reg.name = "bat_charge"

    asyncio.get_event_loop().run_until_complete(
        coord.async_write_register(reg, 5000, audit_source="mqtt")
    )

    assert audit.recent[0].source == "mqtt"


def test_modbus_coordinator_write_register_logs_non_modbus_exceptions():
    """Non-ModbusClientError exceptions must still produce an audit error event."""
    import asyncio
    from unittest.mock import MagicMock

    from custom_components.kostal_kore.modbus_coordinator import ModbusDataUpdateCoordinator
    from custom_components.kostal_kore.modbus_registers import Access

    audit = WriteAuditLog()
    client = MagicMock()
    client.write_register = AsyncMock(side_effect=TimeoutError("connection lost"))

    coord = ModbusDataUpdateCoordinator.__new__(ModbusDataUpdateCoordinator)
    coord._client = client
    coord._write_audit = audit

    reg = MagicMock()
    reg.access = Access.RW
    reg.name = "bat_charge"

    with pytest.raises(TimeoutError):
        asyncio.get_event_loop().run_until_complete(
            coord.async_write_register(reg, 5000, audit_source="mqtt")
        )

    assert audit.total_count == 1
    assert audit.recent[0].result == "error"
    assert audit.recent[0].source == "mqtt"
    assert "connection lost" in audit.recent[0].detail


def test_modbus_coordinator_write_by_address_logs_ok():
    """async_write_by_address must audit successful writes."""
    import asyncio
    from unittest.mock import MagicMock

    from custom_components.kostal_kore.modbus_coordinator import ModbusDataUpdateCoordinator

    audit = WriteAuditLog()
    client = MagicMock()
    client.write_by_address = AsyncMock(return_value=None)

    coord = ModbusDataUpdateCoordinator.__new__(ModbusDataUpdateCoordinator)
    coord._client = client
    coord._write_audit = audit
    coord._last_commanded = {}

    asyncio.get_event_loop().run_until_complete(
        coord.async_write_by_address(1034, 80, audit_source="proxy_fc06")
    )

    assert audit.total_count == 1
    e = audit.recent[0]
    assert e.source == "proxy_fc06"
    assert e.result == "ok"
    assert e.key == "addr:1034"
    assert e.value == 80


def test_modbus_coordinator_write_by_address_logs_error():
    """async_write_by_address must audit write failures and re-raise."""
    import asyncio
    from unittest.mock import MagicMock

    from custom_components.kostal_kore.modbus_coordinator import ModbusDataUpdateCoordinator

    audit = WriteAuditLog()
    client = MagicMock()
    client.write_by_address = AsyncMock(side_effect=RuntimeError("nope"))

    coord = ModbusDataUpdateCoordinator.__new__(ModbusDataUpdateCoordinator)
    coord._client = client
    coord._write_audit = audit

    with pytest.raises(RuntimeError):
        asyncio.get_event_loop().run_until_complete(
            coord.async_write_by_address(1034, 80, audit_source="proxy_fc06")
        )

    assert audit.total_count == 1
    assert audit.recent[0].result == "error"
    assert "nope" in audit.recent[0].detail


# ---------------------------------------------------------------------------
# Modbus coordinator property tests
# ---------------------------------------------------------------------------


def test_modbus_coordinator_poll_phase_property():
    from custom_components.kostal_kore.modbus_coordinator import ModbusDataUpdateCoordinator

    coord = ModbusDataUpdateCoordinator.__new__(ModbusDataUpdateCoordinator)
    coord._slow_tick = 3
    assert coord.poll_phase == 3


def test_modbus_coordinator_slow_data_age_none_when_never_polled():
    from custom_components.kostal_kore.modbus_coordinator import ModbusDataUpdateCoordinator

    coord = ModbusDataUpdateCoordinator.__new__(ModbusDataUpdateCoordinator)
    coord._last_slow_ts = 0.0
    assert coord.slow_data_age_s is None


def test_modbus_coordinator_slow_data_age_positive_after_poll():
    from custom_components.kostal_kore.modbus_coordinator import ModbusDataUpdateCoordinator

    coord = ModbusDataUpdateCoordinator.__new__(ModbusDataUpdateCoordinator)
    coord._last_slow_ts = time.monotonic() - 15.0
    age = coord.slow_data_age_s
    assert age is not None
    assert 14.5 <= age <= 16.0


def test_modbus_coordinator_update_count_property():
    from custom_components.kostal_kore.modbus_coordinator import ModbusDataUpdateCoordinator

    coord = ModbusDataUpdateCoordinator.__new__(ModbusDataUpdateCoordinator)
    coord._update_count = 99
    assert coord.update_count == 99


# ---------------------------------------------------------------------------
# MQTT bridge rejection hooks
# ---------------------------------------------------------------------------


def test_mqtt_bridge_logs_installer_rejection():
    from custom_components.kostal_kore.mqtt_bridge import KostalMqttBridge

    audit = WriteAuditLog()
    coord_mock = MagicMock()
    coord_mock._write_audit = audit

    bridge = KostalMqttBridge.__new__(KostalMqttBridge)
    bridge._coordinator = coord_mock
    bridge._installer_access = False
    bridge._soc_controller = None
    bridge._last_write = {}
    bridge._write_lock = MagicMock()
    bridge._command_count = 0
    bridge._rate_limited_count = 0

    from custom_components.kostal_kore.mqtt_bridge import KostalMqttBridge as MQTT  # noqa: F401
    reg = MagicMock()
    reg.name = "bat_charge_dc_abs_power"

    import asyncio
    asyncio.get_event_loop().run_until_complete(bridge._execute_write(reg, "5000", "test"))

    assert audit.total_count == 1
    assert audit.recent[0].result == "rejected_installer"


def test_mqtt_bridge_rate_limited_count_incremented():
    from custom_components.kostal_kore.mqtt_bridge import KostalMqttBridge

    audit = WriteAuditLog()
    coord_mock = MagicMock()
    coord_mock._write_audit = audit

    bridge = KostalMqttBridge.__new__(KostalMqttBridge)
    bridge._coordinator = coord_mock
    bridge._installer_access = True
    bridge._soc_controller = None
    bridge._last_write = {"some_reg": time.monotonic() + 1.0}  # recently written
    bridge._write_lock = MagicMock()
    bridge._command_count = 0
    bridge._rate_limited_count = 0

    reg = MagicMock()
    reg.name = "some_reg"

    import asyncio
    asyncio.get_event_loop().run_until_complete(bridge._execute_write(reg, "5000", "test"))

    assert bridge._rate_limited_count == 1
    assert audit.total_count == 1
    assert audit.recent[0].result == "rejected_rate"


# ---------------------------------------------------------------------------
# Proxy write audit hooks
# ---------------------------------------------------------------------------


def test_proxy_log_audit_writes_to_coordinator_audit():
    from custom_components.kostal_kore.modbus_proxy import ModbusTcpProxyServer

    audit = WriteAuditLog()
    coord_mock = MagicMock()
    coord_mock._write_audit = audit

    proxy = ModbusTcpProxyServer.__new__(ModbusTcpProxyServer)
    proxy._coordinator = coord_mock

    proxy._log_audit("addr:1034", 5000, "forwarded_direct", "FC06")

    assert audit.total_count == 1
    e = audit.recent[0]
    assert e.result == "forwarded_direct"
    assert e.source == "proxy_fc06"
    assert e.key == "addr:1034"


def test_proxy_fc06_multi_register_rejection_creates_audit_event():
    """FC06 on a multi-word register must log rejected_validation, not drop silently."""
    from custom_components.kostal_kore.modbus_proxy import ModbusTcpProxyServer

    audit = WriteAuditLog()
    coord_mock = MagicMock()
    coord_mock._write_audit = audit

    proxy = ModbusTcpProxyServer.__new__(ModbusTcpProxyServer)
    proxy._coordinator = coord_mock

    # Simulate what the code path calls: _log_audit with FC06 multi-reg detail
    proxy._log_audit("some_reg", 42, "rejected_validation", "FC06 multi-reg")

    assert audit.total_count == 1
    e = audit.recent[0]
    assert e.result == "rejected_validation"
    assert e.source == "proxy_fc06"


def test_proxy_log_audit_fc16_error_classifies_as_proxy_fc16():
    """FC16 decode/write errors must surface as proxy_fc16 audit events."""
    from custom_components.kostal_kore.modbus_proxy import ModbusTcpProxyServer

    audit = WriteAuditLog()
    coord_mock = MagicMock()
    coord_mock._write_audit = audit

    proxy = ModbusTcpProxyServer.__new__(ModbusTcpProxyServer)
    proxy._coordinator = coord_mock

    proxy._log_audit("min_soc", None, "error", "FC16 decode failure")

    assert audit.total_count == 1
    e = audit.recent[0]
    assert e.source == "proxy_fc16"
    assert e.result == "error"


def test_proxy_log_audit_no_op_when_no_write_audit():
    from custom_components.kostal_kore.modbus_proxy import ModbusTcpProxyServer

    coord_mock = MagicMock()
    coord_mock._write_audit = None

    proxy = ModbusTcpProxyServer.__new__(ModbusTcpProxyServer)
    proxy._coordinator = coord_mock

    # Should not raise
    proxy._log_audit("addr:1034", 5000, "ok", "FC06")


# ---------------------------------------------------------------------------
# create_observability_sensors factory
# ---------------------------------------------------------------------------


def test_create_observability_sensors_returns_four_entities():
    from custom_components.kostal_kore.observability_entities import create_observability_sensors

    coord = _make_coordinator()
    audit = WriteAuditLog()
    scheduler = MagicMock()
    scheduler.get_stats.return_value = {"total_requests": 0, "waits": 0,
                                         "timeouts": 0, "lock_held": False}
    device_info = MagicMock()

    entities = create_observability_sensors(
        modbus_coordinator=coord,
        process_coordinator=None,
        write_audit=audit,
        scheduler=scheduler,
        entry_id="test_entry",
        device_info=device_info,
    )

    assert len(entities) == 4
    names = [type(e).__name__ for e in entities]
    assert "WriteAuditSensor" in names
    assert "RequestSchedulerSensor" in names
    assert "ModbusCoordinatorSensor" in names
    assert "RestModbusConsistencySensor" in names
