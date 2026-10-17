"""Freshness epochs, display-cache failures and local diagnostic actions."""
from __future__ import annotations

import logging
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.kostal_kore.communication import CommunicationState
from custom_components.kostal_kore.coordinator import (
    Plenticore,
    ProcessDataUpdateCoordinator,
    _setting_values_equal,
)
from custom_components.kostal_kore.diagnostics import (
    async_register_debug_bundle_service,
    async_unregister_debug_bundle_service_if_unused,
    communication_snapshot,
)
from custom_components.kostal_kore.modbus_coordinator import ModbusDataUpdateCoordinator
from custom_components.kostal_kore.modbus_registers import REG_PM_TOTAL_ACTIVE
from custom_components.kostal_kore.number import PlenticoreDataNumber
from custom_components.kostal_kore.sensor import PreferredGridPowerSensor
from custom_components.kostal_kore.switch import PlenticoreDataSwitch


def test_observation_age_epoch_and_missing_poll_result():
    co = object.__new__(ModbusDataUpdateCoordinator)
    co._shutting_down = False
    co._client = SimpleNamespace(connected=True, connection_generation=1)
    co.last_update_success = True
    co.update_interval = timedelta(seconds=5)
    co._proxy_observations = {}
    reg = REG_PM_TOTAL_ACTIVE
    with patch("custom_components.kostal_kore.modbus_coordinator.time.monotonic", return_value=100):
        co._observe_for_proxy([reg], {reg.name: 1200})
        assert co.proxy_cache_data == {reg.name: 1200}
        co._client.connection_generation = 2
        assert co.proxy_cache_data == {}
        co._client.connection_generation = 1
    with patch("custom_components.kostal_kore.modbus_coordinator.time.monotonic", return_value=116):
        assert co.proxy_cache_data == {}
    co._observe_for_proxy([reg], {reg.name: float("nan")})
    assert co.proxy_cache_data == {}
    co._observe_for_proxy([reg], {reg.name: 1400})
    co._observe_for_proxy([reg], {})
    assert co.proxy_cache_data == {}
    co._observe_for_proxy([reg], {reg.name: 1600})
    co.invalidate_proxy_cache(reg.address + 1, 1)
    assert co.proxy_cache_data == {}
    co._observe_for_proxy([reg], {reg.name: 1600})
    co.invalidate_proxy_cache(reg.address + 2, 1)
    assert co.proxy_cache_data == {reg.name: 1600}
    co._client.connected = False
    assert co.proxy_cache_data == {}


def test_grid_source_selection_never_uses_retained_values_after_loss():
    sensor = object.__new__(PreferredGridPowerSensor)
    sensor.coordinator = SimpleNamespace(data={"net_active_power_w": 100}, last_update_success=False)
    sensor._modbus_coordinator = SimpleNamespace(data={"pm_total_active": 200}, proxy_cache_data={})
    sensor._process_coordinator = SimpleNamespace(data={"devices:local": {"Grid_P": 300}},
                                                last_update_success=True, communication_stale=True, fresh_fields=set())
    assert sensor.available is False
    sensor._process_coordinator.communication_stale = False
    assert sensor.native_value == 300
    sensor._modbus_coordinator.proxy_cache_data = {"pm_total_active": 400}
    assert sensor.native_value == 400


@pytest.mark.parametrize("expected,actual,matches", [
    ("1", "nan", False), ("0", "inf", False), ("nan", "nan", False),
    ("1", "1.0005", True), ("1", "1.01", False),
    ("text", "text", True), ("text", "different", False),
])
def test_invalid_readback_is_never_confirmation(expected, actual, matches):
    assert _setting_values_equal(expected, actual) is matches


@pytest.mark.parametrize("raw,expected", [("0", False), ("1", True), ("nan", None), ("future-state", None), (None, None)])
def test_unknown_switch_data_is_unknown_rather_than_off(raw, expected):
    switch = object.__new__(PlenticoreDataSwitch)
    switch.module_id, switch.data_id, switch._is_on = "module", "control", "1"
    switch.on_value, switch.off_value = "1", "0"
    switch.coordinator = SimpleNamespace(data={"module": {"control": raw}},
                                         last_update_success=True, communication_stale=False)
    assert switch.is_on is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
async def test_invalid_rest_numeric_command_is_rejected_before_device_access(value):
    from homeassistant.exceptions import ServiceValidationError
    number = object.__new__(PlenticoreDataNumber)
    number.coordinator = SimpleNamespace(async_write_data=AsyncMock())
    with pytest.raises(ServiceValidationError, match="finite"):
        await number.async_set_native_value(value)
    number.coordinator.async_write_data.assert_not_awaited()


@pytest.mark.asyncio
async def test_cached_process_data_stays_unavailable_until_real_recovery(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    client = SimpleNamespace(get_process_data_values=AsyncMock(side_effect=TimeoutError()))
    owner = SimpleNamespace(client=client, hass=None, async_report_rest_communication=AsyncMock())
    with patch("homeassistant.helpers.frame.report_usage", create=True):
        co = ProcessDataUpdateCoordinator(hass, MagicMock(), logging.getLogger(__name__),
                                          "test", timedelta(seconds=5), owner)
    co._fetch["devices:local:battery"] = ["SoC"]
    co._last_result = {"devices:local:battery": {"SoC": "20"}}
    co._last_success_ts = time.monotonic()
    assert await co._async_update_data() == co._last_result
    assert co.communication_stale is True
    owner.async_report_rest_communication.assert_awaited_once_with(False, source="process")
    client.get_process_data_values = AsyncMock(return_value={
        "devices:local:battery": {"SoC": SimpleNamespace(value=21)},
    })
    assert await co._async_update_data() == {"devices:local:battery": {"SoC": "21"}}
    assert co.communication_stale is False
    assert owner.async_report_rest_communication.await_args.args == (True,)


@pytest.mark.asyncio
async def test_runtime_rest_notifications_deduplicate_and_keep_recovery(tmp_path):
    owner = object.__new__(Plenticore)
    owner.hass = HomeAssistant(str(tmp_path))
    owner.config_entry = SimpleNamespace(entry_id="one")
    owner.rest_communication = CommunicationState()
    owner._rest_poll_health = {}
    owner._communication_store = SimpleNamespace(async_save=AsyncMock())
    with patch("homeassistant.components.persistent_notification.async_create") as notice:
        await owner.async_report_rest_communication(False)
        await owner.async_report_rest_communication(False)
        assert notice.call_count == 1
        await owner.async_report_rest_communication(True)
        assert notice.call_count == 2
        assert "wiederhergestellt" in notice.call_args.args[1]
    assert owner.rest_communication.incidents == 1
    assert owner._communication_store.async_save.await_count == 2


@pytest.mark.asyncio
async def test_read_only_diagnostics_use_snapshots_and_unregister(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    health = CommunicationState()
    store = {"modbus_coordinator": SimpleNamespace(communication=health, proxy_cache_data={}),
             "process_coordinator": SimpleNamespace(_plenticore=SimpleNamespace(rest_communication=health)),
             "modbus_proxy": SimpleNamespace(communication_diagnostics=lambda: {"failed_requests": 2})}
    assert communication_snapshot({}) == {}
    snapshot = communication_snapshot(store)
    assert snapshot["modbus"]["observed_cache_entries"] == 0
    assert snapshot["rest"]["incidents"] == 0
    assert snapshot["proxy"]["failed_requests"] == 2
    hass.data["kostal_kore"] = {"one": store, "non-entry": object()}
    async_register_debug_bundle_service(hass)
    async_register_debug_bundle_service(hass)
    result = await hass.services.async_call("kostal_kore", "communication_diagnostics", {},
                                            blocking=True, return_response=True)
    assert result == {"one": snapshot}
    async_unregister_debug_bundle_service_if_unused(hass)
    assert hass.services.has_service("kostal_kore", "communication_diagnostics")
    hass.data["kostal_kore"].clear()
    async_unregister_debug_bundle_service_if_unused(hass)
    assert not hass.services.has_service("kostal_kore", "communication_diagnostics")
