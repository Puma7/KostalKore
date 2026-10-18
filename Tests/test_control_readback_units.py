"""Validate battery-limit readings in both KOSTAL word orders without writes."""
import struct
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.kostal_kore.modbus_client import KostalModbusClient
from custom_components.kostal_kore.modbus_registers import (
    REG_BAT_MAX_CHARGE_LIMIT,
    REG_BAT_MAX_DISCHARGE_LIMIT,
)


@pytest.mark.parametrize("register", [REG_BAT_MAX_CHARGE_LIMIT, REG_BAT_MAX_DISCHARGE_LIMIT])
@pytest.mark.parametrize("endianness", ["big", "little"])
@pytest.mark.parametrize("watts", [0.0, 51.49, 4500.0, 39760.0])
async def test_control_readback_uses_absolute_watts(register, endianness, watts):
    """No percent conversion, rounding to the control step or automatic reset."""
    raw = struct.pack(">f", watts)
    if endianness == "little":
        raw = raw[2:] + raw[:2]
    client = KostalModbusClient("127.0.0.1", endianness=endianness)
    with (
        patch.object(client, "_raw_read", new=AsyncMock(return_value=raw)) as read,
        patch.object(client, "_raw_write", new=AsyncMock()) as write,
    ):
        assert await client.read_register(register) == pytest.approx(watts, rel=1e-6)
    read.assert_awaited_once_with(register.address, 2)
    write.assert_not_awaited()
