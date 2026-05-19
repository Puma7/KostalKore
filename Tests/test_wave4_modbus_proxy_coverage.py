"""Wave-4 coverage tests for Modbus proxy protocol and forwarding paths."""

from __future__ import annotations

import asyncio
import math
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kostal_plenticore.modbus_proxy import (
    FC_READ_HOLDING,
    FC_WRITE_MULTIPLE,
    FC_WRITE_SINGLE,
    ModbusTcpProxyServer,
    _build_register_image,
    _encode_value,
)
from kostal_plenticore.modbus_registers import (
    Access,
    DataType,
    ModbusRegister,
    REG_ACTIVE_POWER_SETPOINT,
    REG_BAT_CHARGE_DC_ABS_POWER,
    RegisterGroup,
)


def _make_proxy(
    *,
    installer_access: bool = True,
    soc_active: bool = False,
    data: dict | None = None,
    client: object | None = None,
) -> ModbusTcpProxyServer:
    coordinator = MagicMock()
    coordinator.data = data or {}
    coordinator.async_write_by_address = AsyncMock()
    coordinator.async_write_register = AsyncMock()
    coordinator.client = client

    soc_controller = SimpleNamespace(active=soc_active, target_soc=55.0)
    return ModbusTcpProxyServer(
        coordinator,
        installer_access=installer_access,
        soc_controller=soc_controller,
    )


def test_modbus_proxy_encode_and_build_image_paths() -> None:
    uint16_reg = ModbusRegister(
        10, "u16", "u16", DataType.UINT16, 1, Access.RW, RegisterGroup.CONTROL
    )
    sint16_reg = ModbusRegister(
        11, "s16", "s16", DataType.SINT16, 1, Access.RW, RegisterGroup.CONTROL
    )
    uint32_reg = ModbusRegister(
        12, "u32", "u32", DataType.UINT32, 2, Access.RW, RegisterGroup.CONTROL
    )
    sint32_reg = ModbusRegister(
        14, "s32", "s32", DataType.SINT32, 2, Access.RW, RegisterGroup.CONTROL
    )
    float_reg = ModbusRegister(
        16, "flt", "flt", DataType.FLOAT32, 2, Access.RW, RegisterGroup.CONTROL
    )
    string_reg = ModbusRegister(
        18, "txt", "txt", DataType.STRING, 2, Access.RW, RegisterGroup.CONTROL
    )
    bool_reg = ModbusRegister(
        20, "flag", "flag", DataType.BOOL, 1, Access.RW, RegisterGroup.CONTROL
    )
    uint8_reg = ModbusRegister(
        21, "u8", "u8", DataType.UINT8, 1, Access.RW, RegisterGroup.CONTROL
    )
    unknown_reg = ModbusRegister(  # type: ignore[arg-type]
        22, "unknown", "unknown", "mystery", 1, Access.RW, RegisterGroup.CONTROL
    )

    assert _encode_value(7, uint16_reg) == struct.pack(">H", 7)
    assert _encode_value(-3, sint16_reg) == struct.pack(">h", -3)
    assert _encode_value(0x12345678, uint32_reg, "big") == struct.pack(">I", 0x12345678)
    assert _encode_value(0x12345678, uint32_reg, "little") == struct.pack(">HH", 0x5678, 0x1234)
    assert _encode_value(-2, sint32_reg, "little") == struct.pack(">HH", 0xFFFE, 0xFFFF)
    assert _encode_value(2, sint32_reg, "little") == struct.pack(">HH", 2, 0)
    assert _encode_value(-2, sint32_reg, "big") == struct.pack(">i", -2)
    assert _encode_value(float("nan"), float_reg) == struct.pack(">HH", 0, 0)
    assert _encode_value(float("inf"), float_reg, "big") == struct.pack(">f", 0.0)
    assert _encode_value("AB", string_reg) == b"AB\x00\x00"
    assert _encode_value(True, bool_reg) == struct.pack(">H", 1)
    assert _encode_value(255, uint8_reg) == struct.pack(">H", 255)
    assert _encode_value("ignored", unknown_reg) == b"\x00\x00"

    with patch(
        "kostal_plenticore.modbus_proxy._SORTED_REGISTERS",
        [(10, uint16_reg), (12, uint32_reg)],
    ):
        # HIGH-02 fix: partial coverage (gap at register 11 because no
        # register covers address 11) must NOT return a zero-filled image —
        # the caller would otherwise serve fabricated zeros to evcc/EMS.
        # Expectation is now None so the proxy falls back to forward-read.
        assert (
            _build_register_image(
                10, 4, {"u16": 7, "u32": 0x12345678}, "little"
            )
            is None
        )
        # Full coverage of the requested range still returns a populated image.
        # Request addr 12..13 → exactly the u32 (count=2) covers both.
        image = _build_register_image(
            12, 2, {"u32": 0x12345678}, "little"
        )
        assert image == struct.pack(">HH", 0x5678, 0x1234)
        # Single-register request at addr 10 also fully covered by u16.
        assert _build_register_image(10, 1, {"u16": 7}, "little") == struct.pack(">H", 7)

        assert _build_register_image(10, 4, {}, "little") is None
        assert _build_register_image(10, 4, {"u16": 70000}, "little") is None

    edge_reg = ModbusRegister(
        8, "edge", "edge", DataType.UINT32, 2, Access.RW, RegisterGroup.CONTROL
    )
    with patch(
        "kostal_plenticore.modbus_proxy._SORTED_REGISTERS",
        [(8, edge_reg)],
    ):
        with patch(
            "kostal_plenticore.modbus_proxy._encode_value",
            return_value=b"\x12\x34\x56\x78",
        ):
            # Slicing the second half of a 2-register encode for a 1-register
            # request: fully covered, returns the matching slice.
            assert _build_register_image(9, 1, {"edge": 1}, "little") == b"\x56\x78"
        with patch(
            "kostal_plenticore.modbus_proxy._encode_value",
            return_value=b"\x12\x34",
        ):
            # HIGH-02 fix: when the encoded payload is too short to cover the
            # full requested range, return None instead of zero-padding the
            # uncovered byte (which would surface to evcc as a fake "0").
            assert _build_register_image(8, 2, {"edge": 1}, "little") is None


@pytest.mark.asyncio
async def test_modbus_proxy_start_stop_client_loop_and_dispatch() -> None:
    proxy = _make_proxy()

    fake_server = MagicMock()
    fake_server.is_serving.return_value = True
    fake_server.wait_closed = AsyncMock(return_value=None)

    with patch(
        "kostal_plenticore.modbus_proxy.asyncio.start_server",
        AsyncMock(return_value=fake_server),
    ):
        await proxy.start()

    assert proxy.running is True
    assert proxy.port > 0

    blocker = asyncio.Event()
    client_task = asyncio.create_task(blocker.wait())
    proxy._clients.add(client_task)
    await proxy.stop()
    assert proxy.running is False
    assert client_task.cancelled()
    fake_server.close.assert_called_once()
    fake_server.wait_closed.assert_awaited_once()

    proxy = _make_proxy()
    fake_server = MagicMock()
    fake_server.wait_closed = AsyncMock(side_effect=asyncio.TimeoutError())
    proxy._server = fake_server
    await proxy.stop()
    fake_server.close.assert_called_once()
    fake_server.wait_closed.assert_awaited_once()

    await _make_proxy().stop()

    assert await proxy._process_pdu(b"", proxy._unit_id) == struct.pack(">BB", 0x80, 0x01)
    assert await proxy._process_pdu(b"\x03\x00\x00\x00\x01", proxy._unit_id + 1) == struct.pack(">BB", 0x83, 0x0B)
    assert await proxy._process_pdu(b"\x99\x00\x00\x00\x01", proxy._unit_id) == struct.pack(">BB", 0x99 | 0x80, 0x01)

    with (
        patch.object(proxy, "_handle_read", AsyncMock(return_value=b"read")) as handle_read,
        patch.object(proxy, "_handle_write_single", AsyncMock(return_value=b"single")) as handle_single,
        patch.object(proxy, "_handle_write_multiple", AsyncMock(return_value=b"multi")) as handle_multi,
    ):
        assert await proxy._process_pdu(b"\x03\x00\x00\x00\x01", proxy._unit_id) == b"read"
        assert await proxy._process_pdu(b"\x06\x00\x00\x00\x01", proxy._unit_id) == b"single"
        assert await proxy._process_pdu(b"\x10\x00\x00\x00\x01\x02\x00\x00", proxy._unit_id) == b"multi"
        handle_read.assert_awaited_once()
        handle_single.assert_awaited_once()
        handle_multi.assert_awaited_once()

    request_pdu = struct.pack(">BHH", FC_READ_HOLDING, 2, 1)
    mbap_header = struct.pack(">HHHB", 1, 0, len(request_pdu) + 1, proxy._unit_id)
    reader = MagicMock()
    reader.readexactly = AsyncMock(
        side_effect=[
            mbap_header,
            request_pdu,
            asyncio.IncompleteReadError(partial=b"", expected=7),
        ]
    )
    writer = MagicMock()
    writer.get_extra_info.return_value = ("127.0.0.1", 5502)
    writer.drain = AsyncMock(return_value=None)

    with patch.object(proxy, "_process_pdu", AsyncMock(return_value=b"\x03\x02\x00\x01")):
        await proxy._handle_client(reader, writer)

    writer.write.assert_called_once_with(
        struct.pack(">HHHB", 1, 0, 5, proxy._unit_id) + b"\x03\x02\x00\x01"
    )
    writer.close.assert_called_once()

    writer = MagicMock()
    writer.get_extra_info.return_value = ("127.0.0.1", 5502)
    writer.drain = AsyncMock(return_value=None)

    reader = MagicMock()
    reader.readexactly = AsyncMock(
        side_effect=[
            struct.pack(">HHHB", 2, 1, 2, proxy._unit_id),
            b"\x00",
            struct.pack(">HHHB", 3, 0, 1, proxy._unit_id),
            struct.pack(">HHHB", 3, 0, 400, proxy._unit_id),
            bytes(260),
            asyncio.IncompleteReadError(partial=b"", expected=7),
        ]
    )
    with patch("kostal_plenticore.modbus_proxy.asyncio.current_task", return_value=None):
        await proxy._handle_client(reader, writer)

    writer.close.assert_called_once()

    cancelled_reader = MagicMock()
    cancelled_reader.readexactly = AsyncMock(side_effect=asyncio.CancelledError())
    cancelled_writer = MagicMock()
    cancelled_writer.get_extra_info.return_value = ("127.0.0.1", 5502)
    cancelled_writer.drain = AsyncMock(return_value=None)
    await proxy._handle_client(cancelled_reader, cancelled_writer)
    cancelled_writer.close.assert_called_once()

    error_reader = MagicMock()
    error_reader.readexactly = AsyncMock(side_effect=RuntimeError("reader boom"))
    error_writer = MagicMock()
    error_writer.get_extra_info.return_value = ("127.0.0.1", 5502)
    error_writer.drain = AsyncMock(return_value=None)
    await proxy._handle_client(error_reader, error_writer)
    error_writer.close.assert_called_once()


@pytest.mark.asyncio
async def test_modbus_proxy_read_and_forward_paths() -> None:
    proxy = _make_proxy(data={"modbus_enable": True})

    assert await proxy._handle_read(b"\x03\x00") == struct.pack(">BB", 0x83, 0x03)
    assert await proxy._handle_read(struct.pack(">BHH", FC_READ_HOLDING, 2, 0)) == struct.pack(">BB", 0x83, 0x03)

    response = await proxy._handle_read(struct.pack(">BHH", FC_READ_HOLDING, 2, 1))
    assert response == b"\x03\x02\x00\x01"

    with patch.object(proxy, "_forward_read", AsyncMock(return_value=b"\x12\x34\x56\x78")):
        response = await proxy._handle_read(struct.pack(">BHH", FC_READ_HOLDING, 40000, 2))
    assert response == b"\x03\x04\x12\x34\x56\x78"

    with patch.object(proxy, "_forward_read", AsyncMock(return_value=None)):
        response = await proxy._handle_read(struct.pack(">BHH", FC_READ_HOLDING, 40000, 2))
    assert response == struct.pack(">BB", 0x83, 0x04)

    assert await proxy._forward_read(40000, 2) is None

    client = SimpleNamespace(connected=True, _raw_read=AsyncMock(return_value=b"\x00\x01"))
    proxy = _make_proxy(client=client)
    assert await proxy._forward_read(40000, 1) == b"\x00\x01"

    client = SimpleNamespace(connected=True, _raw_read=AsyncMock(side_effect=RuntimeError("boom")))
    proxy = _make_proxy(client=client)
    assert await proxy._forward_read(40000, 1) is None



@pytest.mark.asyncio
async def test_modbus_proxy_write_single_paths() -> None:
    proxy = _make_proxy(installer_access=True)

    assert await proxy._handle_write_single(b"\x06\x00") == struct.pack(">BB", FC_WRITE_SINGLE | 0x80, 0x03)

    proxy = _make_proxy(installer_access=False)
    pdu = struct.pack(">BHH", FC_WRITE_SINGLE, 1034, 1)
    assert await proxy._handle_write_single(pdu) == struct.pack(">BB", FC_WRITE_SINGLE | 0x80, 0x03)

    proxy = _make_proxy(installer_access=True, soc_active=True)
    assert await proxy._handle_write_single(pdu) == struct.pack(">BB", FC_WRITE_SINGLE | 0x80, 0x06)

    proxy = _make_proxy(installer_access=True)
    assert await proxy._handle_write_single(pdu) == struct.pack(">BB", FC_WRITE_SINGLE | 0x80, 0x03)

    pdu = struct.pack(">BHH", FC_WRITE_SINGLE, REG_ACTIVE_POWER_SETPOINT.address, 42)
    response = await proxy._handle_write_single(pdu)
    assert response == pdu[:5]
    proxy._coordinator.async_write_by_address.assert_awaited_once_with(REG_ACTIVE_POWER_SETPOINT.address, 42)

    proxy = _make_proxy(installer_access=True)
    proxy._coordinator.async_write_by_address.side_effect = RuntimeError("write failed")
    assert await proxy._handle_write_single(pdu) == struct.pack(">BB", FC_WRITE_SINGLE | 0x80, 0x04)

    proxy = _make_proxy(installer_access=True)
    with patch.object(proxy, "_forward_write_single", AsyncMock(return_value=True)):
        assert await proxy._handle_write_single(struct.pack(">BHH", FC_WRITE_SINGLE, 60000, 7)) == struct.pack(">BHH", FC_WRITE_SINGLE, 60000, 7)[:5]
    with patch.object(proxy, "_forward_write_single", AsyncMock(return_value=False)):
        assert await proxy._handle_write_single(struct.pack(">BHH", FC_WRITE_SINGLE, 60000, 7)) == struct.pack(">BB", FC_WRITE_SINGLE | 0x80, 0x04)

    assert await proxy._forward_write_single(60000, 1) is False

    client = SimpleNamespace(connected=True, _raw_write=AsyncMock(return_value=None))
    proxy = _make_proxy(client=client)
    assert await proxy._forward_write_single(60000, 1) is True

    client = SimpleNamespace(connected=True, _raw_write=AsyncMock(side_effect=RuntimeError("boom")))
    proxy = _make_proxy(client=client)
    assert await proxy._forward_write_single(60000, 1) is False


@pytest.mark.asyncio
async def test_modbus_proxy_write_multiple_and_decode_paths() -> None:
    proxy = _make_proxy(installer_access=True)

    assert await proxy._handle_write_multiple(b"\x10\x00") == struct.pack(">BB", FC_WRITE_MULTIPLE | 0x80, 0x03)
    assert await proxy._handle_write_multiple(struct.pack(">BHHB", FC_WRITE_MULTIPLE, 1034, 0, 0)) == struct.pack(">BB", FC_WRITE_MULTIPLE | 0x80, 0x03)
    assert await proxy._handle_write_multiple(struct.pack(">BHHBHH", FC_WRITE_MULTIPLE, 1034, 2, 5, 0, 0)) == struct.pack(">BB", FC_WRITE_MULTIPLE | 0x80, 0x03)
    assert await proxy._handle_write_multiple(b"\x10\x04\x0A\x00\x02\x04\x00") == struct.pack(">BB", FC_WRITE_MULTIPLE | 0x80, 0x03)

    proxy = _make_proxy(installer_access=False)
    battery_pdu = struct.pack(">BHHBHH", FC_WRITE_MULTIPLE, 1034, 2, 4, 0, 0)
    assert await proxy._handle_write_multiple(battery_pdu) == struct.pack(">BB", FC_WRITE_MULTIPLE | 0x80, 0x03)

    proxy = _make_proxy(installer_access=True, soc_active=True)
    assert await proxy._handle_write_multiple(battery_pdu) == struct.pack(">BB", FC_WRITE_MULTIPLE | 0x80, 0x06)

    proxy = _make_proxy(installer_access=True)
    reg_values = _encode_value(1.5, REG_BAT_CHARGE_DC_ABS_POWER, "little")
    success_pdu = struct.pack(
        ">BHHB", FC_WRITE_MULTIPLE, REG_BAT_CHARGE_DC_ABS_POWER.address, REG_BAT_CHARGE_DC_ABS_POWER.count, len(reg_values)
    ) + reg_values
    response = await proxy._handle_write_multiple(success_pdu)
    assert response == struct.pack(">BHH", FC_WRITE_MULTIPLE, REG_BAT_CHARGE_DC_ABS_POWER.address, REG_BAT_CHARGE_DC_ABS_POWER.count)
    written_reg, written_value = proxy._coordinator.async_write_register.await_args.args
    assert written_reg == REG_BAT_CHARGE_DC_ABS_POWER
    assert written_value == pytest.approx(1.5, rel=1e-6)

    proxy = _make_proxy(installer_access=True)
    proxy._coordinator.async_write_register.side_effect = RuntimeError("multi failed")
    assert await proxy._handle_write_multiple(success_pdu) == struct.pack(">BB", FC_WRITE_MULTIPLE | 0x80, 0x04)

    proxy = _make_proxy(installer_access=True)
    unknown_pdu = struct.pack(">BHHBHH", FC_WRITE_MULTIPLE, 60000, 2, 4, 0, 0)
    with patch.object(proxy, "_forward_write_multiple", AsyncMock(return_value=True)):
        assert await proxy._handle_write_multiple(unknown_pdu) == struct.pack(">BHH", FC_WRITE_MULTIPLE, 60000, 2)
    with patch.object(proxy, "_forward_write_multiple", AsyncMock(return_value=False)):
        assert await proxy._handle_write_multiple(unknown_pdu) == struct.pack(">BB", FC_WRITE_MULTIPLE | 0x80, 0x04)

    assert await proxy._forward_write_multiple(60000, 2, b"\x00\x00\x00\x00") is False

    client = SimpleNamespace(connected=True, _raw_write=AsyncMock(return_value=None))
    proxy = _make_proxy(client=client)
    assert await proxy._forward_write_multiple(60000, 2, b"\x00\x00\x00\x00") is True

    client = SimpleNamespace(connected=True, _raw_write=AsyncMock(side_effect=RuntimeError("boom")))
    proxy = _make_proxy(client=client)
    assert await proxy._forward_write_multiple(60000, 2, b"\x00\x00\x00\x00") is False

    little_proxy = _make_proxy()
    big_proxy = ModbusTcpProxyServer(MagicMock(), endianness="big")
    uint16_reg = ModbusRegister(1, "u16", "u16", DataType.UINT16, 1, Access.RW, RegisterGroup.CONTROL)
    sint16_reg = ModbusRegister(2, "s16", "s16", DataType.SINT16, 1, Access.RW, RegisterGroup.CONTROL)
    float_reg = ModbusRegister(3, "flt", "flt", DataType.FLOAT32, 2, Access.RW, RegisterGroup.CONTROL)
    uint32_reg = ModbusRegister(5, "u32", "u32", DataType.UINT32, 2, Access.RW, RegisterGroup.CONTROL)
    bool_reg = ModbusRegister(7, "flag", "flag", DataType.BOOL, 1, Access.RW, RegisterGroup.CONTROL)

    assert little_proxy._decode_for_write(uint16_reg, struct.pack(">H", 7)) == 7
    assert little_proxy._decode_for_write(sint16_reg, struct.pack(">h", -3)) == -3
    assert big_proxy._decode_for_write(float_reg, struct.pack(">f", 1.25)) == pytest.approx(1.25, rel=1e-6)
    little_float_raw = struct.pack(">HH", *struct.unpack(">HH", struct.pack(">f", 1.25))[::-1])
    assert little_proxy._decode_for_write(float_reg, little_float_raw) == pytest.approx(1.25, rel=1e-6)
    assert big_proxy._decode_for_write(uint32_reg, struct.pack(">I", 0x12345678)) == 0x12345678
    assert little_proxy._decode_for_write(uint32_reg, struct.pack(">HH", 0x5678, 0x1234)) == 0x12345678
    assert little_proxy._decode_for_write(bool_reg, struct.pack(">H", 1)) == 1
    assert ModbusTcpProxyServer._error_response(FC_WRITE_SINGLE, 0x04) == struct.pack(">BB", FC_WRITE_SINGLE | 0x80, 0x04)
