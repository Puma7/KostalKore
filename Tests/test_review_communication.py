"""Regression cases from the independent second communication review."""
import logging
import struct
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.kostal_kore.communication import CommunicationState
from custom_components.kostal_kore.coordinator import ProcessDataUpdateCoordinator
from custom_components.kostal_kore.modbus_client import KostalModbusClient, ModbusConnectionError
from custom_components.kostal_kore.modbus_coordinator import ModbusDataUpdateCoordinator
from custom_components.kostal_kore.modbus_registers import (
    REG_BATTERY_SOC,
    REG_G3_MAX_CHARGE,
    REG_PM_TOTAL_ACTIVE,
)
from Tests.test_proxy_communication_health import proxy


@pytest.mark.parametrize("stamp,expected", [(None,None), (42,None), ("bad",None),
    ("2026-01-15T00:00:00",None), ("2026-01-15T00:00:00+00:00","2026-01-15T00:00:00+00:00")])
def test_restore_last_success_with_validation(stamp, expected):
    assert CommunicationState({"last_success_at":stamp}).last_success_at == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["parse", "absent"])
async def test_partial_rest_success_does_not_renew_retained_soc(tmp_path, missing):
    hass = HomeAssistant(str(tmp_path))
    payload = {"devices:local:battery": {"P": SimpleNamespace(value=5000)}}
    if missing == "parse":
        payload["devices:local:battery"]["SoC"] = object()
    client = SimpleNamespace(get_process_data_values=AsyncMock(return_value=payload))
    owner = SimpleNamespace(client=client, hass=None, async_report_rest_communication=AsyncMock())
    with patch("homeassistant.helpers.frame.report_usage", create=True):
        co = ProcessDataUpdateCoordinator(hass, MagicMock(), logging.getLogger(__name__), "test", timedelta(seconds=5), owner)
    co._fetch["devices:local:battery"] = ["SoC", "P"]
    co._last_result = {"devices:local:battery": {"SoC": "20"}}
    co._last_success_ts = time.monotonic()
    await co._async_update_data()
    assert co.communication_stale is True
    owner.async_report_rest_communication.assert_awaited_once_with(False)


@pytest.mark.asyncio
async def test_a_different_successful_read_recovers_link_fault():
    server, client = proxy()
    assert await server._process_pdu(struct.pack(">BHH",3,40000,2),71) == b"\x83\x04"
    client._raw_read.side_effect = None
    client._raw_read.return_value = b"\x00"*4
    assert await server._process_pdu(struct.pack(">BHH",3,40010,2),71) == b"\x03\x04"+b"\x00"*4
    assert server.communication.faults == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("fc", [6,16])
@pytest.mark.parametrize("known", [False,True])
@pytest.mark.parametrize("client", [None, SimpleNamespace(connected=False)])
async def test_disconnected_write_is_known_failed_not_unconfirmed(fc, known, client):
    server, _ = proxy(connected=False)
    server._installer_access = True
    server._coordinator.client = client
    request = (struct.pack(">BHH",6,533 if known else 40000,0) if fc == 6 else
               struct.pack(">BHHB",16,1280 if known else 40000,2,4)+b"\x00"*4)
    assert await server._process_pdu(request,71) == bytes([fc|128,11])
    assert "write_unconfirmed" not in server.communication.faults
    assert server.communication_diagnostics()["uncertain_writes"] == 0
    assert await server._forward_write_single(40000,0) is False
    assert await server._forward_write_multiple(40000,2,b"\x00"*4) is False


@pytest.mark.asyncio
async def test_nan_write_is_rejected_before_upstream_io():
    server, client = proxy()
    server._installer_access = True
    server._coordinator.async_write_register = AsyncMock()
    request = struct.pack(">BHHB",16,REG_G3_MAX_CHARGE.address,2,4)+struct.pack(">f",float("nan"))
    assert await server._process_pdu(request,71) == b"\x90\x03"
    server._coordinator.async_write_register.assert_not_awaited()
    assert "write_unconfirmed" not in server.communication.faults


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 1e20])
def test_retained_quality_value_is_never_a_fresh_proxy_observation(bad):
    client = KostalModbusClient("127.0.0.1")
    reg = REG_PM_TOTAL_ACTIVE
    client._last_good_values[reg.address] = 1200
    assert client._apply_quality_filter(reg,bad) == 1200
    assert reg.address in client.retained_quality_addresses
    co = object.__new__(ModbusDataUpdateCoordinator)
    co._client = client
    co._proxy_observations = {}
    co._observe_for_proxy([reg], {reg.name:1200})
    assert co._proxy_observations == {}
    assert client._apply_quality_filter(reg,1300) == 1300
    assert reg.address not in client.retained_quality_addresses


@pytest.mark.asyncio
async def test_initial_slow_poll_failure_is_reported_even_without_a_cache(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    client = SimpleNamespace(host="127.0.0.1", port=1502, connected=True, closing=False,
        connection_generation=1, unavailable_registers=frozenset(),
        read_registers_batch=AsyncMock(side_effect=[{REG_BATTERY_SOC.name:20},ModbusConnectionError("slow unavailable")]))
    with patch("homeassistant.helpers.frame.report_usage", create=True):
        co = ModbusDataUpdateCoordinator(hass,client)
    co._save_register_capability_state_if_changed = AsyncMock()
    co._report_communication = AsyncMock()
    await co._async_update_data()
    assert co.slow_poll_stale is True
    co._report_communication.assert_awaited_once_with(["modbus_partial"])
