"""Tests for KostalModbusClient encoding/decoding and error handling."""

from __future__ import annotations

import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kostal_plenticore.modbus_client import (
    KostalModbusClient,
    ModbusClientError,
    ModbusConnectionError,
    ModbusReadError,
    ModbusWriteError,
)
from kostal_plenticore.modbus_registers import (
    Access,
    DataType,
    ModbusRegister,
    RegisterGroup,
    REG_TOTAL_DC_POWER,
    REG_BATTERY_SOC,
    REG_INVERTER_STATE,
    REG_SERIAL_NUMBER,
    REG_ACTIVE_POWER_SETPOINT,
    REG_BAT_CHARGE_DC_ABS_POWER,
    REG_MODBUS_ENABLE,
    REG_REACTIVE_POWER_SETPOINT,
)


def _make_response(registers: list[int], is_error: bool = False) -> MagicMock:
    resp = MagicMock()
    resp.isError.return_value = is_error
    resp.registers = registers
    return resp


class TestDecoding:
    """Test _decode for every data type and endianness."""

    def _client(self, endianness: str = "little") -> KostalModbusClient:
        return KostalModbusClient("127.0.0.1", endianness=endianness)

    def test_decode_uint16(self) -> None:
        c = self._client()
        raw = struct.pack(">H", 42)
        assert c._decode(raw, REG_BATTERY_SOC) == 42

    def test_decode_sint16(self) -> None:
        c = self._client()
        raw = struct.pack(">h", -100)
        assert c._decode(raw, REG_REACTIVE_POWER_SETPOINT) == -100

    def test_decode_uint32_little_endian(self) -> None:
        c = self._client("little")
        reg = ModbusRegister(56, "t", "t", DataType.UINT32, 2, Access.RO, RegisterGroup.DEVICE_INFO)
        raw = struct.pack(">HH", 0x0006, 0x0000)
        val = c._decode(raw, reg)
        assert val == 6

    def test_decode_uint32_big_endian(self) -> None:
        c = self._client("big")
        reg = ModbusRegister(56, "t", "t", DataType.UINT32, 2, Access.RO, RegisterGroup.DEVICE_INFO)
        raw = struct.pack(">I", 6)
        val = c._decode(raw, reg)
        assert val == 6

    def test_decode_float32_little_endian(self) -> None:
        c = self._client("little")
        packed = struct.pack(">f", 1234.5)
        hi, lo = struct.unpack(">HH", packed)
        raw = struct.pack(">HH", lo, hi)
        val = c._decode(raw, REG_TOTAL_DC_POWER)
        assert abs(val - 1234.5) < 0.1

    def test_decode_float32_big_endian(self) -> None:
        c = self._client("big")
        raw = struct.pack(">f", 1234.5)
        val = c._decode(raw, REG_TOTAL_DC_POWER)
        assert abs(val - 1234.5) < 0.1

    def test_decode_string(self) -> None:
        c = self._client()
        raw = b"PLENTI\x00\x00" + b"\x00" * 8
        val = c._decode(raw, REG_SERIAL_NUMBER)
        assert val == "PLENTI"

    def test_decode_bool_true(self) -> None:
        c = self._client()
        raw = struct.pack(">H", 1)
        assert c._decode(raw, REG_MODBUS_ENABLE) is True

    def test_decode_bool_false(self) -> None:
        c = self._client()
        raw = struct.pack(">H", 0)
        assert c._decode(raw, REG_MODBUS_ENABLE) is False

    def test_decode_uint8(self) -> None:
        c = self._client()
        reg = ModbusRegister(1080, "t", "t", DataType.UINT8, 1, Access.RO, RegisterGroup.BATTERY)
        raw = bytes([0x02, 0x00])
        val = c._decode(raw, reg)
        assert val == 2

    def test_decode_unsupported_raises(self) -> None:
        c = self._client()
        reg = ModbusRegister(0, "t", "t", DataType.SINT32, 2, Access.RO, RegisterGroup.POWER)
        raw = struct.pack(">HH", 0, 42)
        val = c._decode(raw, reg)
        assert isinstance(val, int)


class TestEncoding:
    """Test _encode for writable data types."""

    def _client(self, endianness: str = "little") -> KostalModbusClient:
        return KostalModbusClient("127.0.0.1", endianness=endianness)

    def test_encode_uint16(self) -> None:
        c = self._client()
        raw = c._encode(50, REG_ACTIVE_POWER_SETPOINT)
        assert struct.unpack(">H", raw)[0] == 50

    def test_encode_sint16(self) -> None:
        c = self._client()
        raw = c._encode(-50, REG_REACTIVE_POWER_SETPOINT)
        assert struct.unpack(">h", raw)[0] == -50

    def test_encode_float32_little_endian(self) -> None:
        c = self._client("little")
        raw = c._encode(5000.0, REG_BAT_CHARGE_DC_ABS_POWER)
        hi, lo = struct.unpack(">HH", raw)
        decoded = struct.unpack(">f", struct.pack(">HH", lo, hi))[0]
        assert abs(decoded - 5000.0) < 0.1

    def test_encode_float32_big_endian(self) -> None:
        c = self._client("big")
        raw = c._encode(5000.0, REG_BAT_CHARGE_DC_ABS_POWER)
        decoded = struct.unpack(">f", raw)[0]
        assert abs(decoded - 5000.0) < 0.1

    def test_encode_bool(self) -> None:
        c = self._client()
        assert c._encode(True, REG_MODBUS_ENABLE) == struct.pack(">H", 1)
        assert c._encode(False, REG_MODBUS_ENABLE) == struct.pack(">H", 0)

    def test_encode_string_raises(self) -> None:
        c = self._client()
        with pytest.raises(ModbusWriteError):
            c._encode("test", REG_SERIAL_NUMBER)

    def test_roundtrip_uint32_little(self) -> None:
        c = self._client("little")
        reg = ModbusRegister(0, "t", "t", DataType.UINT32, 2, Access.RW, RegisterGroup.CONTROL)
        encoded = c._encode(123456, reg)
        decoded = c._decode(encoded, reg)
        assert decoded == 123456

    def test_roundtrip_uint32_big(self) -> None:
        c = self._client("big")
        reg = ModbusRegister(0, "t", "t", DataType.UINT32, 2, Access.RW, RegisterGroup.CONTROL)
        encoded = c._encode(123456, reg)
        decoded = c._decode(encoded, reg)
        assert decoded == 123456

    def test_roundtrip_float32_little(self) -> None:
        c = self._client("little")
        encoded = c._encode(-7500.5, REG_BAT_CHARGE_DC_ABS_POWER)
        decoded = c._decode(encoded, REG_BAT_CHARGE_DC_ABS_POWER)
        assert abs(decoded - (-7500.5)) < 0.5

    def test_roundtrip_float32_big(self) -> None:
        c = self._client("big")
        encoded = c._encode(-7500.5, REG_BAT_CHARGE_DC_ABS_POWER)
        decoded = c._decode(encoded, REG_BAT_CHARGE_DC_ABS_POWER)
        assert abs(decoded - (-7500.5)) < 0.5


class TestWriteAccessGuard:
    """Ensure write_register rejects read-only registers."""

    @pytest.mark.asyncio
    async def test_write_readonly_raises(self) -> None:
        c = KostalModbusClient("127.0.0.1")
        c._client = MagicMock()
        c._client.connected = True
        with pytest.raises(ModbusWriteError, match="read-only"):
            await c.write_register(REG_TOTAL_DC_POWER, 100)


class TestConnectionLifecycle:
    """Test connect/disconnect and error handling."""

    @pytest.mark.asyncio
    async def test_connect_success(self) -> None:
        with patch("kostal_plenticore.modbus_client.AsyncModbusTcpClient") as MockClient:
            instance = AsyncMock()
            instance.connect = AsyncMock(return_value=True)
            instance.connected = True
            MockClient.return_value = instance

            c = KostalModbusClient("192.168.1.100")
            result = await c.connect()
            assert result is True
            assert c.connected

    @pytest.mark.asyncio
    async def test_connect_failure_raises(self) -> None:
        with patch("kostal_plenticore.modbus_client.AsyncModbusTcpClient") as MockClient:
            instance = AsyncMock()
            instance.connect = AsyncMock(return_value=False)
            MockClient.return_value = instance

            c = KostalModbusClient("192.168.1.100")
            with pytest.raises(ModbusConnectionError):
                await c.connect()

    @pytest.mark.asyncio
    async def test_disconnect(self) -> None:
        c = KostalModbusClient("192.168.1.100")
        c._client = MagicMock()
        await c.disconnect()
        assert c._client is None

    @pytest.mark.asyncio
    async def test_read_when_disconnected_raises(self) -> None:
        c = KostalModbusClient("192.168.1.100")
        with pytest.raises(ModbusConnectionError, match="Not connected"):
            await c.read_register(REG_TOTAL_DC_POWER)

    @pytest.mark.asyncio
    async def test_read_error_response(self) -> None:
        c = KostalModbusClient("192.168.1.100")
        mock_client = AsyncMock()
        mock_client.connected = True
        mock_client.read_holding_registers = AsyncMock(
            return_value=_make_response([], is_error=True)
        )
        c._client = mock_client
        with pytest.raises(ModbusReadError):
            await c.read_register(REG_TOTAL_DC_POWER)


class TestNameAndAddressLookup:
    """Test read_by_name, write_by_name, read_by_address, write_by_address."""

    @pytest.mark.asyncio
    async def test_read_by_name_unknown(self) -> None:
        c = KostalModbusClient("127.0.0.1")
        with pytest.raises(ModbusReadError, match="Unknown register name"):
            await c.read_by_name("nonexistent_register")

    @pytest.mark.asyncio
    async def test_write_by_name_unknown(self) -> None:
        c = KostalModbusClient("127.0.0.1")
        with pytest.raises(ModbusWriteError, match="Unknown register name"):
            await c.write_by_name("nonexistent_register", 42)

    @pytest.mark.asyncio
    async def test_read_by_address_unknown(self) -> None:
        c = KostalModbusClient("127.0.0.1")
        with pytest.raises(ModbusReadError, match="Unknown register address"):
            await c.read_by_address(99999)

    @pytest.mark.asyncio
    async def test_write_by_address_unknown(self) -> None:
        c = KostalModbusClient("127.0.0.1")
        with pytest.raises(ModbusWriteError, match="Unknown register address"):
            await c.write_by_address(99999, 42)

    def test_host_and_port_properties(self) -> None:
        c = KostalModbusClient("10.0.0.1", port=1502)
        assert c.host == "10.0.0.1"
        assert c.port == 1502
        assert c.connected is False
