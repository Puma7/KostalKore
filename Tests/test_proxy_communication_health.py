"""Communication failures must never masquerade as fresh proxy measurements."""
from __future__ import annotations

import asyncio
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.kostal_kore import modbus_proxy as proxy_module
from custom_components.kostal_kore.modbus_registers import REG_PM_TOTAL_ACTIVE


def proxy(*, observed=None, connected=True, raw=None):
    client = SimpleNamespace(connected=connected, _raw_read=AsyncMock(side_effect=TimeoutError("link lost")))
    if raw is not None:
        client._raw_read = AsyncMock(return_value=raw)
    coordinator = SimpleNamespace(
        data={REG_PM_TOTAL_ACTIVE.name: 9999.0}, proxy_cache_data=observed or {}, client=client,
        async_write_by_address=AsyncMock(),
    )
    return proxy_module.ModbusTcpProxyServer(coordinator, port=0, endianness="big"), client


@pytest.mark.asyncio
@pytest.mark.parametrize("fc", [3, 4])
@pytest.mark.parametrize("connected", [False, True])
async def test_old_cache_is_not_a_successful_measurement(fc, connected):
    server, _ = proxy(connected=connected)
    request = struct.pack(">BHH", fc, REG_PM_TOTAL_ACTIVE.address, 2)
    assert await server._process_pdu(request, 71) == bytes([fc | 0x80, 4])


@pytest.mark.asyncio
async def test_only_observed_values_are_returned_not_optimistic_command_cache():
    server, client = proxy(observed={REG_PM_TOTAL_ACTIVE.name: 1200.0})
    result = await server._process_pdu(struct.pack(">BHH", 3, REG_PM_TOTAL_ACTIVE.address, 2), 71)
    assert result == b"\x03\x04" + struct.pack(">f", 1200.0)
    client._raw_read.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
async def test_non_finite_cache_is_not_fabricated_zero(bad):
    server, _ = proxy(observed={REG_PM_TOTAL_ACTIVE.name: bad})
    server._coordinator.data[REG_PM_TOTAL_ACTIVE.name] = bad
    result = await server._process_pdu(struct.pack(">BHH", 3, REG_PM_TOTAL_ACTIVE.address, 2), 71)
    assert result == b"\x83\x04"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
async def test_non_finite_upstream_float_is_a_gateway_failure(bad):
    server, _ = proxy(raw=struct.pack(">f", bad))
    result = await server._process_pdu(struct.pack(">BHH", 3, REG_PM_TOTAL_ACTIVE.address, 2), 71)
    assert result == b"\x83\x04"


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [b"\x00\x01", b"\x00" * 6])
async def test_short_or_oversize_forwarded_reply_is_a_gateway_failure(raw):
    server, _ = proxy(raw=raw)
    # Unknown register range guarantees an actual upstream read.
    assert await server._process_pdu(struct.pack(">BHH", 3, 40000, 2), 71) == b"\x83\x04"


@pytest.mark.asyncio
async def test_half_open_upstream_request_has_a_deadline(monkeypatch):
    server, client = proxy()
    monkeypatch.setattr(proxy_module, "PROXY_REQUEST_TIMEOUT_S", 0.01, raising=False)

    async def never_answers(*args):
        await asyncio.Future()

    client._raw_read.side_effect = never_answers
    result = await asyncio.wait_for(server._process_pdu(struct.pack(">BHH", 3, 40000, 2), 71), 0.3)
    assert result == b"\x83\x04"


@pytest.mark.asyncio
async def test_real_tcp_client_gets_error_then_fresh_value_after_recovery():
    server, _ = proxy(connected=False)
    await server.start()
    try:
        port = server._server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            pdu = struct.pack(">BHH", 3, REG_PM_TOTAL_ACTIVE.address, 2)
            writer.write(struct.pack(">HHHB", 1, 0, len(pdu) + 1, 71) + pdu)
            await writer.drain()
            header = await asyncio.wait_for(reader.readexactly(7), 1)
            assert await reader.readexactly(struct.unpack(">HHHB", header)[2] - 1) == b"\x83\x04"
            server._coordinator.client.connected = True
            server._coordinator.proxy_cache_data = {REG_PM_TOTAL_ACTIVE.name: 2500.0}
            writer.write(struct.pack(">HHHB", 2, 0, len(pdu) + 1, 71) + pdu)
            await writer.drain()
            header = await asyncio.wait_for(reader.readexactly(7), 1)
            assert struct.unpack(">HHHB", header)[0] == 2
            assert await reader.readexactly(struct.unpack(">HHHB", header)[2] - 1) == b"\x03\x04" + struct.pack(">f", 2500.0)
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_expected_traffic_watchdog_deduplicates_and_recovers():
    server, _ = proxy(observed={REG_PM_TOTAL_ACTIVE.name: 1200.0})
    server._client_timeout_s = 30
    server._hass, server._entry_id = MagicMock(), "one"
    server._last_request_ts = 0
    with patch("custom_components.kostal_kore.modbus_proxy.time.monotonic", return_value=31), patch(
        "homeassistant.components.persistent_notification.async_create",
    ) as notify:
        await server._check_client_watchdog()
        await server._check_client_watchdog()
        assert notify.call_count == 1
        assert server.communication.snapshot()["faults"] == ["proxy"]
        await server._process_pdu(struct.pack(">BHH", 3, REG_PM_TOTAL_ACTIVE.address, 2), 71)
        assert notify.call_count == 2
        assert server.communication.snapshot()["state"] == "healthy"
        assert server.communication.incidents == 1


@pytest.mark.asyncio
async def test_write_timeout_has_unknown_outcome_and_is_never_replayed(monkeypatch):
    server, client = proxy()
    server._installer_access = True
    client._raw_write = AsyncMock(side_effect=lambda *args: None)

    async def no_ack(*args):
        await asyncio.Future()

    client._raw_write.side_effect = no_ack
    monkeypatch.setattr(proxy_module, "PROXY_REQUEST_TIMEOUT_S", .01)
    pdu = struct.pack(">BHHBH", 16, 40000, 1, 2, 7)
    assert await server._process_pdu(pdu, 71) == b"\x90\x04"
    assert server.communication_diagnostics()["uncertain_writes"] == 1
    client._raw_write.assert_awaited_once()
    client._raw_write.side_effect = None
    await server._check_client_watchdog()
    client._raw_write.assert_awaited_once()  # no reconnect/retry replay queue
