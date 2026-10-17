"""An acknowledgement only resolves the Modbus words it actually writes."""
import struct
from unittest.mock import AsyncMock

import pytest

from Tests.test_proxy_communication_health import proxy


def multiple(address, quantity):
    return struct.pack(">BHHB", 16, address, quantity, quantity * 2) + b"\x00\x01" * quantity


@pytest.mark.asyncio
@pytest.mark.parametrize("first_quantity", [4, 123])
async def test_shorter_write_cannot_confirm_the_rest_of_a_failed_range(first_quantity):
    server, client = proxy()
    client._raw_write = AsyncMock(side_effect=TimeoutError())
    assert await server._process_pdu(multiple(40000, first_quantity), 71) == b"\x90\x04"
    assert server.communication_diagnostics()["pending_write_confirmations"] == first_quantity
    client._raw_write.side_effect = None
    for address in (40000, 40001):
        request = struct.pack(">BHH", 6, address, 1)
        assert await server._process_pdu(request, 71) == request
    assert server.communication.faults == ("write_unconfirmed",)
    assert server.communication_diagnostics()["pending_write_confirmations"] == first_quantity - 2
    rest = multiple(40002, first_quantity - 2)
    assert await server._process_pdu(rest, 71) == rest[:5]
    assert server.communication.faults == ()


@pytest.mark.asyncio
async def test_overlapping_failed_ranges_require_every_word_to_be_confirmed():
    server, client = proxy()
    client._raw_write = AsyncMock(side_effect=TimeoutError())
    for address in (40000, 40002):
        assert await server._process_pdu(multiple(address, 4), 71) == b"\x90\x04"
    assert server.communication_diagnostics()["pending_write_confirmations"] == 6
    client._raw_write.side_effect = None
    await server._process_pdu(multiple(40000, 4), 71)
    assert server.communication_diagnostics()["pending_write_confirmations"] == 2
    assert server.communication.faults == ("write_unconfirmed",)
    await server._process_pdu(multiple(40004, 2), 71)
    assert server.communication.faults == ()


@pytest.mark.asyncio
async def test_overflowing_write_range_is_rejected_before_upstream_io():
    server, client = proxy()
    client._raw_write = AsyncMock()
    assert await server._process_pdu(multiple(65535, 2), 71) == b"\x90\x03"
    client._raw_write.assert_not_awaited()
    assert server.communication_diagnostics()["pending_write_confirmations"] == 0
