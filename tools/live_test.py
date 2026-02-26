#!/usr/bin/env python3
"""Kostal Plenticore Live Test & Diagnostic Tool.

100% READ-ONLY -- this script NEVER writes to the inverter.
Run this BEFORE enabling Modbus in the HA integration to verify
that everything works correctly with your specific inverter.

Usage:
    python tools/live_test.py --host 192.168.1.2
    python tools/live_test.py --host 192.168.1.2 --port 1502 --unit-id 71
    python tools/live_test.py --host 192.168.1.2 --output report.json

The script will:
1. Connect to the inverter via Modbus TCP
2. Detect endianness (byte order)
3. Read ALL available registers (read-only)
4. Classify each register as OK / unavailable / error
5. Print a complete diagnostic report
6. Optionally save to JSON for developer analysis
"""

from __future__ import annotations

import argparse
import asyncio
import json
import struct
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from kostal_plenticore.modbus_registers import (
    ALL_REGISTERS,
    BATTERY_MGMT_MODES,
    BATTERY_TYPES,
    INVERTER_STATES,
    Access,
    DataType,
    ModbusRegister,
    RegisterGroup,
)


async def run_test(host: str, port: int, unit_id: int, output: str | None) -> None:
    from pymodbus.client import AsyncModbusTcpClient

    report: dict[str, any] = {
        "timestamp": datetime.now().isoformat(),
        "host": host,
        "port": port,
        "unit_id": unit_id,
        "tool_version": "2.7.0",
        "connection": {},
        "endianness": {},
        "registers": {},
        "summary": {},
        "errors": [],
    }

    print("=" * 70)
    print("  KOSTAL PLENTICORE LIVE DIAGNOSTIC TOOL")
    print("  100% READ-ONLY -- no writes to inverter")
    print("=" * 70)
    print()

    # Step 1: Connect
    print(f"[1/5] Connecting to {host}:{port} (Unit-ID {unit_id})...")
    try:
        client = AsyncModbusTcpClient(host=host, port=port, timeout=10.0)
        connected = await client.connect()
        if not connected:
            print(f"  FAILED: Could not establish TCP connection to {host}:{port}")
            report["connection"]["status"] = "failed"
            report["errors"].append(f"TCP connection to {host}:{port} failed")
            _save_report(report, output)
            return
        print(f"  OK: TCP connection established")
        report["connection"]["status"] = "ok"
    except Exception as e:
        print(f"  FAILED: {e}")
        report["connection"]["status"] = "error"
        report["connection"]["error"] = str(e)
        report["errors"].append(f"Connection error: {e}")
        _save_report(report, output)
        return

    # Step 2: Detect endianness
    print(f"\n[2/5] Detecting byte order...")
    try:
        resp = await client.read_holding_registers(address=5, count=1, device_id=unit_id)
        if resp.isError():
            print(f"  WARNING: Could not read byte order register (using little-endian default)")
            endianness = "little"
            report["endianness"]["status"] = "default"
        else:
            val = resp.registers[0]
            endianness = "big" if val == 1 else "little"
            print(f"  OK: Byte order = {endianness} ({'ABCD' if endianness == 'big' else 'CDAB'})")
            report["endianness"]["status"] = "detected"
        report["endianness"]["value"] = endianness
    except Exception as e:
        print(f"  WARNING: Endianness detection failed: {e} (using little-endian)")
        endianness = "little"
        report["endianness"]["status"] = "error"
        report["endianness"]["error"] = str(e)

    # Step 3: Read ALL registers
    print(f"\n[3/5] Reading {len(ALL_REGISTERS)} registers...")
    ok_count = 0
    skip_count = 0
    error_count = 0

    for reg in ALL_REGISTERS:
        if reg.access == Access.RW and reg.group in (
            RegisterGroup.CONTROL, RegisterGroup.BATTERY_MGMT,
            RegisterGroup.BATTERY_LIMIT_G3, RegisterGroup.IO_BOARD,
        ):
            # Skip writable control registers in read-only mode
            report["registers"][reg.name] = {
                "address": reg.address,
                "status": "skipped_writable",
                "reason": "Control register - skipped in read-only mode",
            }
            skip_count += 1
            continue

        try:
            resp = await client.read_holding_registers(
                address=reg.address, count=reg.count, device_id=unit_id,
            )
            if resp.isError():
                exc_code = getattr(resp, "exception_code", None)
                exc_msg = {
                    1: "ILLEGAL FUNCTION",
                    2: "ILLEGAL DATA ADDRESS (register not on this model)",
                    3: "ILLEGAL DATA VALUE",
                    4: "SERVER DEVICE FAILURE",
                    6: "SERVER DEVICE BUSY",
                }.get(exc_code, f"Exception 0x{exc_code:02X}" if exc_code else "Unknown")

                report["registers"][reg.name] = {
                    "address": reg.address,
                    "status": "error",
                    "exception_code": exc_code,
                    "exception_message": exc_msg,
                }
                if exc_code == 2:
                    skip_count += 1
                else:
                    error_count += 1
                    report["errors"].append(f"{reg.name} (addr {reg.address}): {exc_msg}")
                continue

            raw = b""
            for rv in resp.registers:
                raw += struct.pack(">H", rv)

            value = _decode(raw, reg, endianness)
            display = _format_value(reg, value)

            report["registers"][reg.name] = {
                "address": reg.address,
                "status": "ok",
                "value": value if not isinstance(value, float) or not (value != value) else None,
                "display": display,
                "data_type": reg.data_type.value,
                "unit": reg.unit,
                "group": reg.group.value,
            }
            ok_count += 1

        except Exception as e:
            report["registers"][reg.name] = {
                "address": reg.address,
                "status": "exception",
                "error": str(e),
            }
            error_count += 1
            report["errors"].append(f"{reg.name} (addr {reg.address}): {e}")

        await asyncio.sleep(0.05)

    print(f"  OK: {ok_count} readable, {skip_count} skipped/unavailable, {error_count} errors")

    # Step 4: Print report
    print(f"\n[4/5] Diagnostic Report")
    print("-" * 70)

    groups_order = [
        RegisterGroup.DEVICE_INFO, RegisterGroup.POWER, RegisterGroup.PHASE,
        RegisterGroup.BATTERY, RegisterGroup.ENERGY, RegisterGroup.POWERMETER,
    ]

    for group in groups_order:
        group_regs = {
            name: data for name, data in report["registers"].items()
            if data.get("group") == group.value and data.get("status") == "ok"
        }
        if not group_regs:
            continue

        print(f"\n  === {group.value.upper()} ===")
        for name, data in group_regs.items():
            display = data.get("display", str(data.get("value", "?")))
            unit = data.get("unit", "")
            print(f"    {name:40s} = {display:>15s} {unit}")

    # Unavailable registers
    unavailable = [
        name for name, data in report["registers"].items()
        if data.get("status") == "error" and data.get("exception_code") == 2
    ]
    if unavailable:
        print(f"\n  === NOT AVAILABLE ON THIS MODEL ({len(unavailable)}) ===")
        for name in unavailable:
            print(f"    {name}")

    # Errors
    if report["errors"]:
        print(f"\n  === ERRORS ({len(report['errors'])}) ===")
        for err in report["errors"]:
            print(f"    ! {err}")

    # Step 5: Summary
    report["summary"] = {
        "registers_ok": ok_count,
        "registers_skipped": skip_count,
        "registers_error": error_count,
        "total_registers": len(ALL_REGISTERS),
        "endianness": endianness,
        "errors": len(report["errors"]),
    }

    # Battery chemistry detection
    bat_type_data = report["registers"].get("battery_type", {})
    if bat_type_data.get("status") == "ok":
        bat_type_val = bat_type_data.get("value")
        bat_name = BATTERY_TYPES.get(bat_type_val, f"Unknown (0x{bat_type_val:04X})" if bat_type_val else "Unknown")
        report["summary"]["battery_type"] = bat_name
        print(f"\n  Battery Type: {bat_name}")

    bat_mgmt = report["registers"].get("battery_mgmt_mode", {})
    if bat_mgmt.get("status") == "ok":
        mode_val = bat_mgmt.get("value")
        mode_name = BATTERY_MGMT_MODES.get(mode_val, f"Unknown ({mode_val})")
        report["summary"]["battery_mgmt_mode"] = mode_name
        modbus_ok = "MODBUS" in mode_name.upper()
        print(f"  Battery Mgmt Mode: {mode_name} {'✓' if modbus_ok else '✗ MODBUS NOT ENABLED!'}")

    print(f"\n[5/5] Summary")
    print(f"  Registers OK:          {ok_count}")
    print(f"  Skipped/Unavailable:   {skip_count}")
    print(f"  Errors:                {error_count}")
    print(f"  Endianness:            {endianness}")

    if error_count == 0:
        print(f"\n  ✓ ALL TESTS PASSED -- safe to enable Modbus in HA integration")
    else:
        print(f"\n  ✗ {error_count} ERRORS -- check log above before enabling Modbus")

    await client.close()

    _save_report(report, output)


def _decode(raw: bytes, reg: ModbusRegister, endianness: str) -> any:
    dt = reg.data_type
    if dt == DataType.UINT16:
        return struct.unpack(">H", raw[:2])[0]
    if dt == DataType.SINT16:
        return struct.unpack(">h", raw[:2])[0]
    if dt == DataType.UINT32:
        if endianness == "big":
            return struct.unpack(">I", raw[:4])[0]
        hi, lo = struct.unpack(">HH", raw[:4])
        return (lo << 16) | hi
    if dt == DataType.SINT32:
        if endianness == "big":
            return struct.unpack(">i", raw[:4])[0]
        hi, lo = struct.unpack(">HH", raw[:4])
        val = (lo << 16) | hi
        return val - 0x100000000 if val >= 0x80000000 else val
    if dt == DataType.FLOAT32:
        if endianness == "big":
            return struct.unpack(">f", raw[:4])[0]
        hi, lo = struct.unpack(">HH", raw[:4])
        return struct.unpack(">f", struct.pack(">HH", lo, hi))[0]
    if dt == DataType.STRING:
        return raw.decode("ascii", errors="replace").rstrip("\x00").strip()
    if dt == DataType.BOOL:
        return struct.unpack(">H", raw[:2])[0] != 0
    if dt == DataType.UINT8:
        return raw[0]
    return raw.hex()


def _format_value(reg: ModbusRegister, value: any) -> str:
    if reg.name == "inverter_state" and isinstance(value, int):
        return INVERTER_STATES.get(value, str(value))
    if reg.name == "battery_type" and isinstance(value, int):
        return BATTERY_TYPES.get(value, f"0x{value:04X}")
    if reg.name == "battery_mgmt_mode" and isinstance(value, int):
        return BATTERY_MGMT_MODES.get(value, str(value))
    if isinstance(value, float):
        if abs(value) > 10000:
            return f"{value:,.0f}"
        return f"{value:.2f}"
    return str(value)


def _save_report(report: dict, output: str | None) -> None:
    if output:
        path = Path(output)
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"\n  Report saved to: {path.absolute()}")
    else:
        default_path = Path("live_test_report.json")
        default_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"\n  Report saved to: {default_path.absolute()}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Kostal Plenticore Live Diagnostic Tool (READ-ONLY)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python tools/live_test.py --host 192.168.1.2
  python tools/live_test.py --host 192.168.1.2 --port 1502 --unit-id 71
  python tools/live_test.py --host 192.168.1.2 --output my_report.json

The tool is 100% read-only and will NEVER write to the inverter.
Run this before enabling Modbus in the Home Assistant integration.
""",
    )
    parser.add_argument("--host", required=True, help="Inverter IP address")
    parser.add_argument("--port", type=int, default=1502, help="Modbus TCP port (default: 1502)")
    parser.add_argument("--unit-id", type=int, default=71, help="Modbus unit ID (default: 71)")
    parser.add_argument("--output", help="Output JSON file path")

    args = parser.parse_args()
    asyncio.run(run_test(args.host, args.port, args.unit_id, args.output))


if __name__ == "__main__":
    main()
