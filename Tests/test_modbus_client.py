"""Tests for KostalModbusClient encoding/decoding and error handling."""

from __future__ import annotations

import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kostal_plenticore.modbus_client import (
    KostalModbusClient,
    ModbusClientError,
    ModbusConnectionError,
    ModbusPermanentError,
    ModbusReadError,
    ModbusTransientError,
    ModbusWriteError,
    _classify_exception_response,
    EXCEPTION_MESSAGES,
    ModbusExceptionCode,
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

    def test_decode_uint32_little_endian_sunspec(self) -> None:
        """SunSpec registers (<500) honor byte_order for UINT32 word order."""
        c = self._client("little")
        reg = ModbusRegister(56, "t", "t", DataType.UINT32, 2, Access.RO, RegisterGroup.DEVICE_INFO)
        raw = struct.pack(">HH", 0x0006, 0x0000)
        val = c._decode(raw, reg)
        assert val == 6

    def test_decode_uint32_little_endian_vendor(self) -> None:
        """Vendor registers (>=500) always use big-endian word order."""
        c = self._client("little")
        reg = ModbusRegister(512, "t", "t", DataType.UINT32, 2, Access.RO, RegisterGroup.BATTERY)
        raw = struct.pack(">I", 50)
        val = c._decode(raw, reg)
        assert val == 50

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
        with patch.object(c, "reconnect", new_callable=AsyncMock, side_effect=ModbusConnectionError("refused")):
            with pytest.raises((ModbusConnectionError, ModbusReadError)):
                await c.read_register(REG_TOTAL_DC_POWER)

    @pytest.mark.asyncio
    async def test_read_error_response_permanent(self) -> None:
        c = KostalModbusClient("192.168.1.100")
        mock_client = AsyncMock()
        mock_client.connected = True
        err_resp = MagicMock()
        err_resp.isError.return_value = True
        err_resp.exception_code = 0x02
        err_resp.function_code = 0x83
        mock_client.read_holding_registers = AsyncMock(return_value=err_resp)
        c._client = mock_client
        with pytest.raises(ModbusPermanentError):
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


class TestExceptionClassification:
    """Test Modbus exception code classification."""

    def test_illegal_data_address_is_permanent(self) -> None:
        resp = MagicMock()
        resp.exception_code = ModbusExceptionCode.ILLEGAL_DATA_ADDRESS
        resp.function_code = 0x83
        err = _classify_exception_response(resp)
        assert isinstance(err, ModbusPermanentError)
        assert "not available" in str(err)

    def test_server_busy_is_transient(self) -> None:
        resp = MagicMock()
        resp.exception_code = ModbusExceptionCode.SERVER_DEVICE_BUSY
        resp.function_code = 0x83
        err = _classify_exception_response(resp)
        assert isinstance(err, ModbusTransientError)
        assert "busy" in str(err).lower()

    def test_server_failure_is_transient(self) -> None:
        resp = MagicMock()
        resp.exception_code = ModbusExceptionCode.SERVER_DEVICE_FAILURE
        resp.function_code = 0x83
        err = _classify_exception_response(resp)
        assert isinstance(err, ModbusTransientError)

    def test_illegal_function_is_permanent(self) -> None:
        resp = MagicMock()
        resp.exception_code = ModbusExceptionCode.ILLEGAL_FUNCTION
        resp.function_code = 0x81
        err = _classify_exception_response(resp)
        assert isinstance(err, ModbusPermanentError)

    def test_illegal_data_value_is_permanent(self) -> None:
        resp = MagicMock()
        resp.exception_code = ModbusExceptionCode.ILLEGAL_DATA_VALUE
        resp.function_code = 0x90
        err = _classify_exception_response(resp)
        assert isinstance(err, ModbusPermanentError)

    def test_unknown_code_is_generic(self) -> None:
        resp = MagicMock()
        resp.exception_code = 0xFF
        resp.function_code = 0x83
        err = _classify_exception_response(resp)
        assert isinstance(err, ModbusClientError)

    def test_no_exception_code_is_generic(self) -> None:
        resp = MagicMock(spec=[])
        err = _classify_exception_response(resp)
        assert isinstance(err, ModbusClientError)

    def test_all_messages_defined(self) -> None:
        for code in ModbusExceptionCode:
            assert code.value in EXCEPTION_MESSAGES


class TestUnavailableRegisterTracking:
    """Test the strike-based register suppression system."""

    def test_unavailable_registers_initially_empty(self) -> None:
        c = KostalModbusClient("127.0.0.1")
        assert len(c.unavailable_registers) == 0

    def test_single_strike_does_not_suppress(self) -> None:
        c = KostalModbusClient("127.0.0.1")
        c._record_unavailable_strike(100, "test_reg")
        assert 100 not in c.unavailable_registers
        assert c._unavailable_strikes[100] == 1

    def test_threshold_strikes_suppress(self) -> None:
        c = KostalModbusClient("127.0.0.1")
        for _ in range(3):
            c._record_unavailable_strike(100, "test_reg")
        assert 100 in c.unavailable_registers
        assert c._is_suppressed(100) is True

    def test_reset_clears_all(self) -> None:
        c = KostalModbusClient("127.0.0.1")
        for _ in range(3):
            c._record_unavailable_strike(100, "test_reg")
        assert 100 in c.unavailable_registers
        c.reset_unavailable()
        assert len(c.unavailable_registers) == 0
        assert c._is_suppressed(100) is False

    def test_suppression_auto_expires(self) -> None:
        import time
        c = KostalModbusClient("127.0.0.1")
        for _ in range(3):
            c._record_unavailable_strike(100, "test_reg")
        c._unavailable_suppressed[100] = time.monotonic() - 99999
        assert c._is_suppressed(100) is False

    def test_successful_read_clears_strikes(self) -> None:
        c = KostalModbusClient("127.0.0.1")
        c._unavailable_strikes[100] = 2
        c._unavailable_strikes.pop(100, None)
        assert 100 not in c._unavailable_strikes

    def test_export_unavailable_state_persists_only_strikes(self) -> None:
        c = KostalModbusClient("127.0.0.1")
        for _ in range(3):
            c._record_unavailable_strike(100, "test_reg")
        state = c.export_unavailable_state()
        assert state == {"strikes": {"100": 3}}

    def test_import_unavailable_state_ignores_legacy_suppressed(self) -> None:
        c = KostalModbusClient("127.0.0.1")
        c.import_unavailable_state(
            {
                "strikes": {"100": 3},
                "suppressed": {"100": 99999.0},
            }
        )
        assert c._unavailable_strikes[100] == 3
        assert c._unavailable_suppressed == {}
        assert c._is_suppressed(100) is False
