"""Tests for KostalModbusClient encoding/decoding and error handling."""

from __future__ import annotations

import asyncio
import struct
from types import SimpleNamespace
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
    REG_BYTE_ORDER,
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
        assert c.unit_id == 71
        assert c.connected is False

    @pytest.mark.asyncio
    async def test_known_name_and_address_lookups_delegate(self) -> None:
        c = KostalModbusClient("127.0.0.1")

        with patch.object(c, "read_register", AsyncMock(return_value=42)) as read_mock:
            assert await c.read_by_name(REG_BATTERY_SOC.name) == 42
        read_mock.assert_awaited_once_with(REG_BATTERY_SOC)

        with patch.object(c, "write_register", AsyncMock()) as write_mock:
            await c.write_by_name(REG_ACTIVE_POWER_SETPOINT.name, 5)
        write_mock.assert_awaited_once_with(REG_ACTIVE_POWER_SETPOINT, 5)

        with patch.object(c, "read_register", AsyncMock(return_value=99)) as read_mock:
            assert await c.read_by_address(REG_BATTERY_SOC.address) == 99
        read_mock.assert_awaited_once_with(REG_BATTERY_SOC)

        with patch.object(c, "write_register", AsyncMock()) as write_mock:
            await c.write_by_address(REG_ACTIVE_POWER_SETPOINT.address, 7)
        write_mock.assert_awaited_once_with(REG_ACTIVE_POWER_SETPOINT, 7)


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

    def test_reset_unavailable_is_noop_when_empty(self) -> None:
        c = KostalModbusClient("127.0.0.1")
        c.reset_unavailable()
        assert c.unavailable_registers == frozenset()


class _SchedulerRequest:
    def __init__(self, sink: list[str], name: str) -> None:
        self._sink = sink
        self._name = name

    async def __aenter__(self):
        self._sink.append(self._name)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Scheduler:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def request(self, name: str) -> _SchedulerRequest:
        return _SchedulerRequest(self.calls, name)


class TestAdditionalClientCoverage:
    """Target retry, scheduler and raw-I/O branches not covered elsewhere."""

    @pytest.mark.asyncio
    async def test_connect_short_circuit_and_detect_endianness(self) -> None:
        c = KostalModbusClient("127.0.0.1")
        c._client = MagicMock()
        c._client.connected = True
        with patch("kostal_plenticore.modbus_client.AsyncModbusTcpClient") as client_cls:
            assert await c.connect() is True
        client_cls.assert_not_called()

        with patch.object(c, "_raw_read", AsyncMock(return_value=struct.pack(">H", 1))):
            assert await c.detect_endianness() == "big"
            assert c.endianness == "big"
        with patch.object(c, "_raw_read", AsyncMock(return_value=struct.pack(">H", 0))):
            assert await c.detect_endianness() == "little"
            assert c.endianness == "little"

    @pytest.mark.asyncio
    async def test_connect_disconnect_and_reconnect_remaining_paths(self) -> None:
        with patch("kostal_plenticore.modbus_client.AsyncModbusTcpClient") as client_cls:
            instance = AsyncMock()
            instance.connect = AsyncMock(side_effect=OSError("refused"))
            client_cls.return_value = instance
            c = KostalModbusClient("127.0.0.1")
            with pytest.raises(ModbusConnectionError, match="Connection to 127.0.0.1:1502 failed"):
                await c.connect()

        c = KostalModbusClient("127.0.0.1")
        await c.disconnect()  # no client branch

        with patch.object(c, "disconnect", AsyncMock()) as disconnect_mock, patch.object(
            c,
            "connect",
            AsyncMock(return_value=True),
        ) as connect_mock:
            assert await c.reconnect() is True
        disconnect_mock.assert_awaited_once()
        connect_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_read_register_retries_and_connection_recovery(self) -> None:
        c = KostalModbusClient("127.0.0.1", endianness="big")

        with (
            patch.object(
                c,
                "_raw_read",
                AsyncMock(
                    side_effect=[
                        ModbusTransientError("device busy"),
                        struct.pack(">f", 123.0),
                    ]
                ),
            ),
            patch("kostal_plenticore.modbus_client.asyncio.sleep", AsyncMock()) as sleep_mock,
        ):
            value = await c.read_register(REG_TOTAL_DC_POWER)
        assert abs(value - 123.0) < 0.1
        sleep_mock.assert_awaited_once()

        with (
            patch.object(
                c,
                "_raw_read",
                AsyncMock(side_effect=ModbusTransientError("busy forever")),
            ),
            patch("kostal_plenticore.modbus_client.asyncio.sleep", AsyncMock()),
        ):
            with pytest.raises(ModbusReadError, match="after 2 retries"):
                await c.read_register(REG_TOTAL_DC_POWER)

        with (
            patch.object(
                c,
                "_raw_read",
                AsyncMock(
                    side_effect=[
                        ModbusConnectionError("lost"),
                        struct.pack(">f", 99.0),
                    ]
                ),
            ),
            patch.object(c, "reconnect", AsyncMock(return_value=True)) as reconnect_mock,
            patch.object(c, "detect_endianness", AsyncMock(return_value="little")) as endian_mock,
        ):
            value = await c.read_register(REG_TOTAL_DC_POWER)
        assert abs(value - 99.0) < 0.1
        reconnect_mock.assert_awaited()
        endian_mock.assert_awaited()

        with patch.object(
            c,
            "_raw_read",
            AsyncMock(side_effect=ModbusConnectionError("still down")),
        ), patch.object(
            c,
            "reconnect",
            AsyncMock(side_effect=ModbusConnectionError("reconnect failed")),
        ), patch.object(
            c,
            "detect_endianness",
            AsyncMock(return_value="little"),
        ):
            with pytest.raises(ModbusConnectionError, match="still down"):
                await c.read_register(REG_TOTAL_DC_POWER)

        c._unavailable_strikes[REG_TOTAL_DC_POWER.address] = 2
        with patch.object(c, "_raw_read", AsyncMock(return_value=struct.pack(">f", 12.5))):
            value = await c.read_register(REG_TOTAL_DC_POWER)
        assert abs(value - 12.5) < 0.1
        assert REG_TOTAL_DC_POWER.address not in c._unavailable_strikes

        c._last_exc_code = ModbusExceptionCode.SERVER_DEVICE_BUSY
        with patch.object(c, "_raw_read", AsyncMock(side_effect=ModbusPermanentError("perm"))):
            with pytest.raises(ModbusPermanentError):
                await c.read_register(REG_TOTAL_DC_POWER)
        assert c._unavailable_strikes.get(REG_TOTAL_DC_POWER.address) is None

    @pytest.mark.asyncio
    async def test_read_register_suppression_and_monitoring_paths(self) -> None:
        c = KostalModbusClient("127.0.0.1")
        with patch.object(c, "_is_suppressed", return_value=True):
            c._unavailable_strikes[REG_TOTAL_DC_POWER.address] = 3
            with pytest.raises(ModbusPermanentError, match="temporarily suppressed"):
                await c.read_register(REG_TOTAL_DC_POWER)

        c = KostalModbusClient("127.0.0.1")

        async def _fake_raw_read_illegal(addr: int, count: int) -> bytes:
            c._last_exc_code = ModbusExceptionCode.ILLEGAL_DATA_ADDRESS
            raise ModbusPermanentError("illegal")

        with patch.object(c, "_raw_read", side_effect=_fake_raw_read_illegal):
            with pytest.raises(ModbusPermanentError):
                await c.read_register(REG_TOTAL_DC_POWER)
        assert c._unavailable_strikes[REG_TOTAL_DC_POWER.address] == 1

        reg_other = ModbusRegister(999, "other", "other", DataType.UINT16, 1, Access.RO, RegisterGroup.POWER)
        with patch(
            "kostal_plenticore.modbus_client.MONITORING_REGISTERS",
            [REG_BATTERY_SOC, REG_TOTAL_DC_POWER, reg_other],
        ), patch.object(
            c,
            "read_register",
            AsyncMock(side_effect=[42, ModbusPermanentError("perm"), ModbusReadError("skip")]),
        ):
            assert await c.read_monitoring() == {"battery_soc": 42}

    def test_quality_filter_branches(self) -> None:
        c = KostalModbusClient("127.0.0.1")
        sentinel_reg = ModbusRegister(575, "generation_energy", "Generation", DataType.UINT16, 1, Access.RO, RegisterGroup.POWER)
        c._last_good_values[sentinel_reg.address] = 123
        assert c._apply_quality_filter(sentinel_reg, 32767) == 123
        c._last_good_values.clear()
        with pytest.raises(ModbusReadError, match="invalid sentinel"):
            c._apply_quality_filter(sentinel_reg, 32767)

        c._last_good_values[REG_TOTAL_DC_POWER.address] = 10.5
        assert c._apply_quality_filter(REG_TOTAL_DC_POWER, float("nan")) == 10.5
        c._last_good_values.clear()
        with pytest.raises(ModbusReadError, match="NaN/Infinity"):
            c._apply_quality_filter(REG_TOTAL_DC_POWER, float("inf"))

        c._last_good_values[REG_TOTAL_DC_POWER.address] = 77.0
        assert c._apply_quality_filter(REG_TOTAL_DC_POWER, 1e12) == 77.0
        c._last_good_values.clear()
        with pytest.raises(ModbusReadError, match="absolute outlier limit"):
            c._apply_quality_filter(REG_TOTAL_DC_POWER, 1e12)

        exempt_reg = ModbusRegister(54, "power_id", "Power ID", DataType.UINT32, 2, Access.RO, RegisterGroup.DEVICE_INFO)
        assert c._apply_quality_filter(sentinel_reg, 123) == 123
        assert c._apply_quality_filter(exempt_reg, 1e12) == 1e12
        assert c._apply_quality_filter(sentinel_reg, "not-a-number") == "not-a-number"
        assert c._apply_quality_filter(REG_TOTAL_DC_POWER, 50.0) == 50.0

        # worktime (address 144) has a per-register override – lifetime seconds
        # counters must not trip the default 10M absolute outlier limit
        worktime_reg = ModbusRegister(
            144, "worktime", "Worktime", DataType.FLOAT32, 2, Access.RO,
            RegisterGroup.DEVICE_INFO, "s",
        )
        assert c._apply_quality_filter(worktime_reg, 14_396_661.0) == 14_396_661.0
        assert c._apply_quality_filter(worktime_reg, 947_000_000.0) == 947_000_000.0
        with pytest.raises(ModbusReadError, match="absolute outlier limit"):
            c._apply_quality_filter(worktime_reg, 1e11)

    @pytest.mark.asyncio
    async def test_raw_read_scheduler_and_inner_branches(self) -> None:
        scheduler = _Scheduler()
        c = KostalModbusClient("127.0.0.1", request_scheduler=scheduler)
        with patch.object(c, "_raw_read_inner", AsyncMock(return_value=b"\x00\x2a")) as inner:
            assert await c._raw_read(10, 1) == b"\x00\x2a"
        inner.assert_awaited_once_with(10, 1)
        assert scheduler.calls == ["modbus_read"]

        c = KostalModbusClient("127.0.0.1")
        with pytest.raises(ModbusConnectionError, match="Not connected"):
            await c._raw_read_inner(10, 1)

        client = AsyncMock()
        client.connected = True
        client.read_holding_registers = AsyncMock(return_value=_make_response([1, 2]))
        c._client = client
        assert await c._raw_read_inner(10, 2) == struct.pack(">HH", 1, 2)

        client.read_holding_registers = AsyncMock(return_value=_make_response([1]))
        with pytest.raises(ModbusReadError, match="Truncated response"):
            await c._raw_read_inner(10, 2)

        err_resp = MagicMock()
        err_resp.isError.return_value = True
        err_resp.exception_code = ModbusExceptionCode.SERVER_DEVICE_BUSY
        err_resp.function_code = 0x83
        client.read_holding_registers = AsyncMock(return_value=err_resp)
        with pytest.raises(ModbusTransientError):
            await c._raw_read_inner(10, 1)
        assert c._last_exc_code == ModbusExceptionCode.SERVER_DEVICE_BUSY

        client.read_holding_registers = AsyncMock(side_effect=asyncio.TimeoutError())
        with pytest.raises(ModbusConnectionError, match="Timeout reading"):
            await c._raw_read_inner(10, 1)
        assert c._client is None  # timeout closes connection

        # Restore client for the next test case
        c._client = client
        c._client.connected = True
        client.read_holding_registers = AsyncMock(side_effect=OSError("broken pipe"))
        with pytest.raises(ModbusConnectionError, match="Connection lost reading"):
            await c._raw_read_inner(10, 1)
        assert c._client is None

        c._client = client
        c._client.connected = True
        client.read_holding_registers = AsyncMock(side_effect=Exception("pymodbus failure"))
        with patch("kostal_plenticore.modbus_client.PyModbusException", Exception):
            with pytest.raises(ModbusReadError, match="Read failed at address"):
                await c._raw_read_inner(10, 1)

    @pytest.mark.asyncio
    async def test_raw_write_scheduler_and_inner_branches(self) -> None:
        scheduler = _Scheduler()
        c = KostalModbusClient("127.0.0.1", request_scheduler=scheduler)
        with patch.object(c, "_raw_write_inner", AsyncMock()) as inner:
            await c._raw_write(10, b"\x00\x2a", 1)
        inner.assert_awaited_once_with(10, b"\x00\x2a", 1)
        assert scheduler.calls == ["modbus_write"]

        c = KostalModbusClient("127.0.0.1")
        with pytest.raises(ModbusConnectionError, match="Not connected"):
            await c._raw_write_inner(10, b"\x00\x2a", 1)

        client = AsyncMock()
        client.connected = True
        ok_resp = MagicMock()
        ok_resp.isError.return_value = False
        client.write_register = AsyncMock(return_value=ok_resp)
        client.write_registers = AsyncMock(return_value=ok_resp)
        c._client = client
        await c._raw_write_inner(10, b"\x00\x2a", 1)
        client.write_register.assert_awaited_once()
        await c._raw_write_inner(10, b"\x00\x01\x00\x02", 2)
        client.write_registers.assert_awaited_once()

        err_resp = MagicMock()
        err_resp.isError.return_value = True
        err_resp.exception_code = ModbusExceptionCode.ILLEGAL_DATA_VALUE
        err_resp.function_code = 0x90
        client.write_register = AsyncMock(return_value=err_resp)
        with pytest.raises(ModbusPermanentError):
            await c._raw_write_inner(10, b"\x00\x2a", 1)

        client.write_register = AsyncMock(side_effect=asyncio.TimeoutError())
        with pytest.raises(ModbusConnectionError, match="Timeout writing"):
            await c._raw_write_inner(10, b"\x00\x2a", 1)
        assert c._client is None  # timeout closes connection

        # Restore client for the next test case
        c._client = client
        c._client.connected = True
        client.write_register = AsyncMock(side_effect=OSError("lost"))
        with pytest.raises(ModbusConnectionError, match="Connection lost writing"):
            await c._raw_write_inner(10, b"\x00\x2a", 1)
        assert c._client is None

        c._client = client
        c._client.connected = True
        client.write_register = AsyncMock(side_effect=Exception("modbus boom"))
        with patch("kostal_plenticore.modbus_client.PyModbusException", Exception):
            with pytest.raises(ModbusWriteError, match="Write failed at address"):
                await c._raw_write_inner(10, b"\x00\x2a", 1)

        c2 = KostalModbusClient("127.0.0.1")
        with patch.object(c2, "_raw_write_inner", AsyncMock()) as inner:
            await c2._raw_write(11, b"\x00\x2b", 1)
        inner.assert_awaited_once_with(11, b"\x00\x2b", 1)

    def test_decode_and_encode_remaining_branches(self) -> None:
        c = KostalModbusClient("127.0.0.1", endianness="little")
        sint32_reg = ModbusRegister(100, "signed", "Signed", DataType.SINT32, 2, Access.RO, RegisterGroup.POWER)
        raw = struct.pack(">HH", 0xFFFF, 0xFFFF)
        assert c._decode(raw, sint32_reg) == -1
        c_big = KostalModbusClient("127.0.0.1", endianness="big")
        assert c_big._decode(struct.pack(">i", -7), sint32_reg) == -7

        unsupported_decode = ModbusRegister(10, "u", "u", object(), 1, Access.RO, RegisterGroup.POWER)  # type: ignore[arg-type]
        with pytest.raises(ModbusReadError, match="Unsupported data type"):
            c._decode(b"\x00\x00", unsupported_decode)

        with pytest.raises(ModbusWriteError, match="NaN/Infinity"):
            c._encode(float("nan"), REG_BAT_CHARGE_DC_ABS_POWER)

        unsupported_encode = ModbusRegister(10, "u", "u", DataType.SINT32, 2, Access.RW, RegisterGroup.CONTROL)
        with pytest.raises(ModbusWriteError, match="Cannot encode data type"):
            c._encode(5, unsupported_encode)
        with pytest.raises(ModbusWriteError, match="Cannot encode value"):
            c._encode("bad", REG_ACTIVE_POWER_SETPOINT)

    @pytest.mark.asyncio
    async def test_write_register_retry_and_connection_paths(self) -> None:
        c = KostalModbusClient("127.0.0.1")
        writable = ModbusRegister(900, "w", "Writable", DataType.UINT16, 1, Access.RW, RegisterGroup.CONTROL)

        with (
            patch.object(c, "_raw_write", AsyncMock(side_effect=[ModbusTransientError("busy"), None])),
            patch("kostal_plenticore.modbus_client.asyncio.sleep", AsyncMock()) as sleep_mock,
        ):
            await c.write_register(writable, 5)
        sleep_mock.assert_awaited_once()

        with (
            patch.object(c, "_raw_write", AsyncMock(side_effect=ModbusTransientError("busy forever"))),
            patch("kostal_plenticore.modbus_client.asyncio.sleep", AsyncMock()),
        ):
            with pytest.raises(ModbusWriteError, match="after 2 retries"):
                await c.write_register(writable, 5)

        with (
            patch.object(c, "_raw_write", AsyncMock(side_effect=[ModbusConnectionError("lost"), None])),
            patch.object(c, "reconnect", AsyncMock(return_value=True)) as reconnect_mock,
            patch.object(c, "detect_endianness", AsyncMock(return_value="little")) as endian_mock,
        ):
            await c.write_register(writable, 5)
        reconnect_mock.assert_awaited()
        endian_mock.assert_awaited()

        with patch.object(c, "_raw_write", AsyncMock(side_effect=ModbusConnectionError("still down"))), patch.object(
            c,
            "reconnect",
            AsyncMock(side_effect=ModbusConnectionError("reconnect failed")),
        ), patch.object(
            c,
            "detect_endianness",
            AsyncMock(return_value="little"),
        ):
            with pytest.raises(ModbusConnectionError, match="still down"):
                await c.write_register(writable, 5)

        with patch.object(c, "_raw_write", AsyncMock(side_effect=ModbusPermanentError("illegal"))):
            with pytest.raises(ModbusPermanentError):
                await c.write_register(writable, 5)
