"""Cross-platform health and protocol regressions from the third review."""
import logging
import struct
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.kostal_kore.communication import CommunicationState
from custom_components.kostal_kore.coordinator import (
    Plenticore,
    ProcessDataUpdateCoordinator,
    SettingDataUpdateCoordinator,
    _process_value_text,
)
from custom_components.kostal_kore.modbus_client import KostalModbusClient
from custom_components.kostal_kore.modbus_registers import REGISTER_BY_ADDRESS
from custom_components.kostal_kore.notifications import (
    clear_control_lease_notice,
    notify_control_lease_lost,
)
from custom_components.kostal_kore.sensor import PlenticoreDataSensor
from Tests.test_proxy_communication_health import proxy


def coordinator(kind, hass, owner):
    with patch("homeassistant.helpers.frame.report_usage", create=True):
        return kind(hass, MagicMock(), logging.getLogger(__name__), "Settings Data", timedelta(seconds=5), owner)


@pytest.mark.asyncio
async def test_separate_settings_instances_cannot_mask_each_others_outages(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    owner = object.__new__(Plenticore)
    owner.hass, owner.config_entry = hass, SimpleNamespace(entry_id="third-review")
    owner.rest_communication, owner._rest_poll_health = CommunicationState(), {}
    owner._communication_store = SimpleNamespace(async_save=AsyncMock())
    good = {"battery": {"limit": "1000"}}
    owner._client = SimpleNamespace(get_setting_values=AsyncMock(side_effect=[TimeoutError(), good, good]))
    number, switch = (coordinator(SettingDataUpdateCoordinator, hass, owner) for _ in range(2))
    for item in (number, switch):
        item._fetch["battery"] = ["limit"]
    with patch("homeassistant.components.persistent_notification.async_create") as notice, patch("custom_components.kostal_kore.coordinator.clear_issue"):
        with pytest.raises(UpdateFailed):
            await number._async_update_data()
        await switch._async_update_data()
        assert number.communication_stale and not switch.communication_stale
        assert owner.rest_communication.faults == ("rest",)
        assert notice.call_count == 1
        await number._async_update_data()
        assert owner.rest_communication.faults == ()
        assert notice.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [None, float("nan"), float("inf"), -float("inf"), "NaN", "Infinity", "None", "1e999", "null", ""])
async def test_invalid_process_value_cannot_renew_sensor_freshness(tmp_path, invalid):
    hass = HomeAssistant(str(tmp_path))
    client = SimpleNamespace(get_process_data_values=AsyncMock(return_value={"battery": {"SoC": SimpleNamespace(value=invalid)}}))
    owner = SimpleNamespace(client=client, hass=None, async_report_rest_communication=AsyncMock())
    item = coordinator(ProcessDataUpdateCoordinator, hass, owner)
    item._fetch["battery"] = ["SoC"]
    item._last_result, item._last_success_ts = {"battery": {"SoC": "20"}}, time.monotonic()
    for _ in range(2):
        item.async_set_updated_data(await item._async_update_data())
        entity = object.__new__(PlenticoreDataSensor)
        entity.coordinator, entity.module_id, entity.data_id = item, "battery", "SoC"
        assert entity.available is False
        assert item.fresh_fields == set()
        assert item.communication_stale is True
        assert owner.async_report_rest_communication.await_args.args == (False,)


@pytest.mark.parametrize("value,expected", [(0,"0"), (1.5,"1.5"), ("grid", "grid")])
def test_finite_and_textual_process_states_remain_supported(value, expected):
    assert _process_value_text(value) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("address", [583,585])
@pytest.mark.parametrize("wire,expected", [(65535,-1),(32768,-32768),(32767,32767),(0,0)])
async def test_fc06_signed_register_values_reach_the_encoder_correctly(address, wire, expected):
    server, client = proxy()
    encoder = KostalModbusClient("127.0.0.1")
    client._raw_write = AsyncMock()

    async def forward(register_address, value, **kwargs):
        await client._raw_write(register_address, encoder._encode(value, REGISTER_BY_ADDRESS[register_address]), 1)

    server._coordinator.async_write_by_address.side_effect = forward
    request = struct.pack(">BHH",6,address,wire)
    assert await server._process_pdu(request,71) == request
    client._raw_write.assert_awaited_once_with(address, struct.pack(">h",expected),1)
    assert "write_unconfirmed" not in server.communication.faults


@pytest.mark.asyncio
@pytest.mark.parametrize("remove_subscription", [False,True])
async def test_empty_module_is_not_a_partial_response(tmp_path, remove_subscription):
    hass = HomeAssistant(str(tmp_path))
    client = SimpleNamespace(get_process_data_values=AsyncMock(return_value={"active": {"P": SimpleNamespace(value=600)}}))
    item = coordinator(ProcessDataUpdateCoordinator, hass, SimpleNamespace(client=client,hass=None))
    item._fetch["active"] = ["P"]
    item._fetch["removed"] = ["P"] if remove_subscription else []
    if remove_subscription:
        item.stop_fetch_data("removed","P")
        assert "removed" not in item._fetch
    await item._async_update_data()
    assert item.communication_stale is False
    client.get_process_data_values.assert_awaited_once_with({"active":["P"]})


def test_resuming_discharge_does_not_dismiss_halted_charge_notice():
    hass = MagicMock()
    with patch("homeassistant.components.persistent_notification.async_create") as create, patch("homeassistant.components.persistent_notification.async_dismiss") as dismiss:
        notify_control_lease_lost(hass,"entry",control="modbus_charge")
        notify_control_lease_lost(hass,"entry",control="modbus_discharge")
        charge_id, discharge_id = (call.kwargs["notification_id"] for call in create.call_args_list)
        assert charge_id != discharge_id
        clear_control_lease_notice(hass,"entry",control="modbus_discharge")
        dismiss.assert_called_once_with(hass,discharge_id)
        assert dismiss.call_args.args[1] != charge_id
