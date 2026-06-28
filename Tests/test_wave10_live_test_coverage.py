"""Coverage tests for live_test.py."""

from __future__ import annotations

import asyncio  # noqa: F401
import struct
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import custom_components.kostal_kore.live_test as live
from custom_components.kostal_kore.modbus_registers import (
    Access,
    DataType,
    ModbusRegister,
    RegisterGroup,
)


def _reg(
    address: int,
    name: str,
    *,
    data_type: DataType,
    count: int,
    group: RegisterGroup,
    access: Access = Access.RO,
    unit: str | None = None,
) -> ModbusRegister:
    return ModbusRegister(address, name, name, data_type, count, access, group, unit)


class _Response:
    def __init__(self, *, registers: list[int] | None = None, error: bool = False, exception_code: int | None = None):
        self.registers = registers or []
        self._error = error
        self.exception_code = exception_code

    def isError(self) -> bool:
        return self._error


def _install_client(client: object):
    client_mod = types.ModuleType("pymodbus.client")
    client_mod.AsyncModbusTcpClient = MagicMock(return_value=client)
    pymodbus_mod = types.ModuleType("pymodbus")
    pymodbus_mod.client = client_mod
    return patch.dict(sys.modules, {"pymodbus": pymodbus_mod, "pymodbus.client": client_mod})


def _u16(value: int) -> list[int]:
    return [value & 0xFFFF]


def _f32(value: float) -> list[int]:
    raw = struct.pack(">f", value)
    return list(struct.unpack(">HH", raw))


@pytest.mark.asyncio
async def test_run_test_connection_failure_and_connection_exception(capsys) -> None:
    """Connection failures should be reported and saved without continuing."""
    client = SimpleNamespace(connect=AsyncMock(return_value=False))
    with _install_client(client), patch.object(live, "_save_report") as save_report:
        await live.run_test("1.2.3.4", 1502, 71, None)
    report = save_report.call_args.args[0]
    assert report["connection"]["status"] == "failed"
    assert "TCP connection to 1.2.3.4:1502 failed" in report["errors"][0]
    assert "FAILED: Could not establish TCP connection" in capsys.readouterr().out

    client = SimpleNamespace(connect=AsyncMock(side_effect=RuntimeError("boom")))
    with _install_client(client), patch.object(live, "_save_report") as save_report:
        await live.run_test("1.2.3.4", 1502, 71, "report.json")
    report = save_report.call_args.args[0]
    assert report["connection"]["status"] == "error"
    assert report["connection"]["error"] == "boom"


@pytest.mark.asyncio
async def test_run_test_success_warnings_and_error_paths(capsys) -> None:
    """Main diagnostic flow should cover skip, unavailable, error and warning summaries."""
    registers = (
        _reg(10, "rw_skip", data_type=DataType.UINT16, count=1, group=RegisterGroup.CONTROL, access=Access.RW),
        _reg(11, "battery_type", data_type=DataType.UINT16, count=1, group=RegisterGroup.BATTERY),
        _reg(12, "battery_mgmt_mode", data_type=DataType.UINT16, count=1, group=RegisterGroup.BATTERY_MGMT),
        _reg(13, "ok_power", data_type=DataType.FLOAT32, count=2, group=RegisterGroup.POWER, unit="W"),
        _reg(14, "not_available", data_type=DataType.UINT16, count=1, group=RegisterGroup.ENERGY),
        _reg(15, "error_reg", data_type=DataType.UINT16, count=1, group=RegisterGroup.DEVICE_INFO),
        _reg(16, "exception_reg", data_type=DataType.UINT16, count=1, group=RegisterGroup.PHASE),
    )
    responses = {
        5: _Response(registers=_u16(1)),
        11: _Response(registers=_u16(0x0004)),
        12: _Response(registers=_u16(0)),
        13: _Response(registers=_f32(123.5)),
        14: _Response(error=True, exception_code=2),
        15: _Response(error=True, exception_code=4),
    }

    async def _read_holding_registers(*, address: int, count: int, device_id: int):
        if address == 16:
            raise RuntimeError("broken register")
        return responses[address]

    client = SimpleNamespace(
        connect=AsyncMock(return_value=True),
        read_holding_registers=AsyncMock(side_effect=_read_holding_registers),
        close=MagicMock(),
    )

    with _install_client(client), patch.object(live, "ALL_REGISTERS", registers), patch(
        "custom_components.kostal_kore.live_test.asyncio.sleep", new=AsyncMock()
    ), patch.object(live, "_save_report") as save_report:
        await live.run_test("1.2.3.4", 1502, 71, None)

    report = save_report.call_args.args[0]
    assert report["endianness"]["value"] == "big"
    assert report["registers"]["rw_skip"]["status"] == "skipped_writable"
    assert report["registers"]["battery_type"]["display"] == "BYD"
    assert report["registers"]["not_available"]["status"] == "error"
    assert report["registers"]["error_reg"]["exception_message"] == "SERVER DEVICE FAILURE"
    assert report["registers"]["exception_reg"]["status"] == "exception"
    assert report["summary"]["battery_type"] == "BYD"
    assert report["summary"]["battery_mgmt_mode"] == "No external battery management"
    assert report["summary"]["warnings"]
    assert report["summary"]["errors"] == 2
    assert client.close.called
    output = capsys.readouterr().out
    assert "Battery Mgmt Mode: No external battery management" in output
    assert "ERRORS" in output


@pytest.mark.asyncio
async def test_run_test_unexpected_endianness_and_clean_success_summary(capsys) -> None:
    """Remaining endianness branches and summary paths should be exercised."""
    warning_regs = (
        _reg(20, "battery_mgmt_mode", data_type=DataType.UINT16, count=1, group=RegisterGroup.BATTERY_MGMT),
        _reg(21, "not_available", data_type=DataType.UINT16, count=1, group=RegisterGroup.ENERGY),
    )
    warning_responses = {
        5: _Response(registers=_u16(7)),
        20: _Response(registers=_u16(0)),
        21: _Response(error=True, exception_code=2),
    }

    async def _warning_reads(*, address: int, count: int, device_id: int):
        return warning_responses[address]

    warning_client = SimpleNamespace(
        connect=AsyncMock(return_value=True),
        read_holding_registers=AsyncMock(side_effect=_warning_reads),
        close=MagicMock(),
    )
    with _install_client(warning_client), patch.object(live, "ALL_REGISTERS", warning_regs), patch(
        "custom_components.kostal_kore.live_test.asyncio.sleep", new=AsyncMock()
    ), patch.object(live, "_save_report"):
        await live.run_test("1.2.3.4", 1502, 71, None)
    warning_output = capsys.readouterr().out
    assert "Unexpected byte order value 7" in warning_output
    assert "TESTS PASSED WITH WARNINGS" in warning_output

    ok_regs = (
        _reg(30, "battery_mgmt_mode", data_type=DataType.UINT16, count=1, group=RegisterGroup.BATTERY_MGMT),
        _reg(31, "inverter_state", data_type=DataType.UINT16, count=1, group=RegisterGroup.POWER),
    )
    ok_responses = {
        30: _Response(registers=_u16(2)),
        31: _Response(registers=_u16(6)),
    }

    async def _ok_reads(*, address: int, count: int, device_id: int):
        if address == 5:
            raise RuntimeError("endianness failed")
        return ok_responses[address]

    ok_client = SimpleNamespace(
        connect=AsyncMock(return_value=True),
        read_holding_registers=AsyncMock(side_effect=_ok_reads),
        close=MagicMock(),
    )
    with _install_client(ok_client), patch.object(live, "ALL_REGISTERS", ok_regs), patch(
        "custom_components.kostal_kore.live_test.asyncio.sleep", new=AsyncMock()
    ), patch.object(live, "_save_report") as save_report:
        await live.run_test("1.2.3.4", 1502, 71, None)
    report = save_report.call_args.args[0]
    assert report["endianness"]["status"] == "error"
    assert report["summary"]["warnings"] == []
    ok_output = capsys.readouterr().out
    assert "Endianness detection failed" in ok_output
    assert "ALL TESTS PASSED" in ok_output

    default_regs = (
        _reg(40, "ok_power", data_type=DataType.FLOAT32, count=2, group=RegisterGroup.POWER, unit="W"),
        _reg(41, "not_available_a", data_type=DataType.UINT16, count=1, group=RegisterGroup.ENERGY),
        _reg(42, "not_available_b", data_type=DataType.UINT16, count=1, group=RegisterGroup.ENERGY),
    )
    default_responses = {
        5: _Response(error=True, exception_code=4),
        40: _Response(registers=_f32(5.0)),
        41: _Response(error=True, exception_code=2),
        42: _Response(error=True, exception_code=2),
    }

    async def _default_reads(*, address: int, count: int, device_id: int):
        return default_responses[address]

    default_client = SimpleNamespace(
        connect=AsyncMock(return_value=True),
        read_holding_registers=AsyncMock(side_effect=_default_reads),
        close=MagicMock(),
    )
    with _install_client(default_client), patch.object(live, "ALL_REGISTERS", default_regs), patch(
        "custom_components.kostal_kore.live_test.asyncio.sleep", new=AsyncMock()
    ), patch.object(live, "_save_report") as save_report:
        await live.run_test("1.2.3.4", 1502, 71, None)
    default_report = save_report.call_args.args[0]
    assert default_report["endianness"]["status"] == "default"
    assert default_report["summary"]["warnings"][0] == "Battery management mode register could not be read."
    assert "many expected registers are not accessible" in default_report["summary"]["warnings"][1]

    little_regs = (
        _reg(50, "battery_mgmt_mode", data_type=DataType.UINT16, count=1, group=RegisterGroup.BATTERY_MGMT),
    )
    little_responses = {
        5: _Response(registers=_u16(0)),
        50: _Response(registers=_u16(2)),
    }

    async def _little_reads(*, address: int, count: int, device_id: int):
        return little_responses[address]

    little_client = SimpleNamespace(
        connect=AsyncMock(return_value=True),
        read_holding_registers=AsyncMock(side_effect=_little_reads),
        close=MagicMock(),
    )
    with _install_client(little_client), patch.object(live, "ALL_REGISTERS", little_regs), patch(
        "custom_components.kostal_kore.live_test.asyncio.sleep", new=AsyncMock()
    ), patch.object(live, "_save_report") as save_report:
        await live.run_test("1.2.3.4", 1502, 71, None)
    assert save_report.call_args.args[0]["endianness"]["value"] == "little"


def test_decode_format_save_and_main(tmp_path, monkeypatch) -> None:
    """Helper functions and CLI main should cover all remaining branches."""
    assert live._decode(struct.pack(">H", 12), _reg(1, "u16", data_type=DataType.UINT16, count=1, group=RegisterGroup.POWER), "big") == 12
    assert live._decode(struct.pack(">h", -5), _reg(2, "s16", data_type=DataType.SINT16, count=1, group=RegisterGroup.POWER), "big") == -5
    assert live._decode(struct.pack(">I", 0x01020304), _reg(3, "u32", data_type=DataType.UINT32, count=2, group=RegisterGroup.POWER), "big") == 0x01020304
    assert live._decode(struct.pack(">HH", 0x0102, 0x0304), _reg(4, "u32l", data_type=DataType.UINT32, count=2, group=RegisterGroup.POWER), "little") == 0x03040102
    assert live._decode(struct.pack(">i", -12), _reg(5, "s32", data_type=DataType.SINT32, count=2, group=RegisterGroup.POWER), "big") == -12
    assert live._decode(struct.pack(">HH", 0xFFFF, 0xFFF0), _reg(6, "s32l", data_type=DataType.SINT32, count=2, group=RegisterGroup.POWER), "little") < 0
    assert abs(live._decode(struct.pack(">f", 12.5), _reg(7, "f32", data_type=DataType.FLOAT32, count=2, group=RegisterGroup.POWER), "big") - 12.5) < 0.001
    little_float_raw = struct.pack(">HH", *_f32(12.5)[::-1])
    assert abs(live._decode(little_float_raw, _reg(7, "f32l", data_type=DataType.FLOAT32, count=2, group=RegisterGroup.POWER), "little") - 12.5) < 0.001
    assert live._decode(b"ABCD", _reg(8, "str", data_type=DataType.STRING, count=2, group=RegisterGroup.POWER), "big") == "ABCD"
    assert live._decode(struct.pack(">H", 1), _reg(9, "bool", data_type=DataType.BOOL, count=1, group=RegisterGroup.POWER), "big") is True
    assert live._decode(b"\x05", _reg(10, "u8", data_type=DataType.UINT8, count=1, group=RegisterGroup.POWER), "big") == 5
    assert live._decode(b"\xAA\xBB", _reg(11, "raw", data_type="raw", count=1, group=RegisterGroup.POWER), "big") == "aabb"

    assert live._format_value(_reg(12, "inverter_state", data_type=DataType.UINT16, count=1, group=RegisterGroup.POWER), 6) == "FeedIn"
    assert live._format_value(_reg(13, "battery_type", data_type=DataType.UINT16, count=1, group=RegisterGroup.BATTERY), 0x0004) == "BYD"
    assert live._format_value(_reg(14, "battery_mgmt_mode", data_type=DataType.UINT16, count=1, group=RegisterGroup.BATTERY_MGMT), 2) == "External via MODBUS"
    assert live._format_value(_reg(15, "float_large", data_type=DataType.FLOAT32, count=2, group=RegisterGroup.POWER), 12345.6) == "12,346"
    assert live._format_value(_reg(16, "float_small", data_type=DataType.FLOAT32, count=2, group=RegisterGroup.POWER), 12.3456) == "12.35"
    assert live._format_value(_reg(17, "other", data_type=DataType.STRING, count=1, group=RegisterGroup.POWER), "abc") == "abc"

    report = {"ok": True}
    explicit = tmp_path / "report.json"
    live._save_report(report, str(explicit))
    assert explicit.exists()

    monkeypatch.chdir(tmp_path)
    live._save_report(report, None)
    assert Path("live_test_report.json").exists()

    with patch.object(sys, "argv", ["live_test.py", "--host", "1.2.3.4", "--port", "1502", "--unit-id", "71", "--output", "x.json"]), patch(
        "custom_components.kostal_kore.live_test.asyncio.run"
    ) as run_mock, patch.object(live, "run_test", new=MagicMock(return_value="sentinel-coro")) as run_test:
        live.main()
    run_test.assert_called_once_with("1.2.3.4", 1502, 71, "x.json")
    run_mock.assert_called_once_with("sentinel-coro")

    captured: list[object] = []

    def _fake_asyncio_run(coro: object) -> None:
        captured.append(coro)
        coro.close()

    with patch.object(sys, "argv", ["live_test.py", "--host", "1.2.3.4"]), patch(
        "asyncio.run", side_effect=_fake_asyncio_run
    ):
        source = Path(live.__file__).read_text(encoding="utf-8")
        exec(
            compile(source, str(Path(live.__file__)), "exec"),
            {
                "__name__": "__main__",
                "__package__": "custom_components.kostal_kore",
                "__file__": str(Path(live.__file__)),
                "__builtins__": __builtins__,
            },
        )
    assert captured
