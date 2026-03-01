"""Modbus TCP proxy server for Kostal Plenticore.

Exposes the inverter's Modbus registers on a local TCP port so that
external systems (evcc, SolarAssistant, …) can read and write registers
*without* opening their own connection to the inverter.

Architecture::

    evcc / other ──Modbus TCP──► THIS PROXY (port 502/5502)
                                    │
                        cache hit ──► serve from coordinator cache (fast)
                        cache miss ──► forward to inverter via client (SunSpec etc.)
                        writes ──► forward to inverter via coordinator

Only ONE real Modbus TCP connection exists (from our plugin to the
inverter). Known registers are served from the coordinator's polling
cache. Unknown addresses (e.g. SunSpec registers at 40000+) are
transparently forwarded to the inverter through the existing connection.
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time
from typing import Any, Final

from .modbus_registers import (
    ALL_REGISTERS,
    Access,
    DataType,
    ModbusRegister,
    REGISTER_BY_ADDRESS,
    DEFAULT_UNIT_ID,
)

_LOGGER: Final = logging.getLogger(__name__)

DEFAULT_PROXY_PORT: Final[int] = 5502
DEFAULT_PROXY_BIND: Final[str] = "127.0.0.1"
MBAP_HEADER_SIZE: Final[int] = 7

FC_READ_HOLDING: Final[int] = 0x03
FC_READ_INPUT: Final[int] = 0x04
FC_WRITE_SINGLE: Final[int] = 0x06
FC_WRITE_MULTIPLE: Final[int] = 0x10

# Build a sorted list of (start_address, register) for range lookups
_SORTED_REGISTERS: Final[list[tuple[int, ModbusRegister]]] = sorted(
    ((r.address, r) for r in ALL_REGISTERS),
    key=lambda x: x[0],
)


def _encode_value(value: Any, register: ModbusRegister, endianness: str = "little") -> bytes:
    """Re-encode a decoded Python value back to raw Modbus register bytes."""
    import math

    dt = register.data_type

    if dt == DataType.UINT16:
        return struct.pack(">H", int(value) if value is not None else 0)

    if dt == DataType.SINT16:
        return struct.pack(">h", int(value) if value is not None else 0)

    if dt == DataType.UINT32:
        v = int(value) if value is not None else 0
        if endianness == "big":
            return struct.pack(">I", v)
        lo = v & 0xFFFF
        hi = (v >> 16) & 0xFFFF
        return struct.pack(">HH", lo, hi)

    if dt == DataType.SINT32:
        v = int(value) if value is not None else 0
        if endianness == "big":
            return struct.pack(">i", v)
        if v < 0:
            v += 0x100000000
        lo = v & 0xFFFF
        hi = (v >> 16) & 0xFFFF
        return struct.pack(">HH", lo, hi)

    if dt == DataType.FLOAT32:
        fv = float(value) if value is not None else 0.0
        if math.isnan(fv) or math.isinf(fv):
            fv = 0.0
        if endianness == "big":
            return struct.pack(">f", fv)
        packed = struct.pack(">f", fv)
        hi, lo = struct.unpack(">HH", packed)
        return struct.pack(">HH", lo, hi)

    if dt == DataType.STRING:
        s = str(value) if value is not None else ""
        raw = s.encode("ascii", errors="replace")
        padded = raw.ljust(register.count * 2, b"\x00")[:register.count * 2]
        return padded

    if dt == DataType.BOOL:
        return struct.pack(">H", 1 if value else 0)

    if dt == DataType.UINT8:
        return struct.pack(">H", int(value) if value is not None else 0)

    return b"\x00" * (register.count * 2)


def _build_register_image(
    start_addr: int,
    quantity: int,
    data: dict[str, Any],
    endianness: str,
) -> bytes | None:
    """Build raw register bytes for a read request from cached data.

    Returns *quantity * 2* bytes or None if the range is completely unknown.
    """
    image = bytearray(quantity * 2)
    found_any = False

    for base_addr, reg in _SORTED_REGISTERS:
        reg_end = base_addr + reg.count
        if base_addr >= start_addr + quantity:
            break
        if reg_end <= start_addr:
            continue

        val = data.get(reg.name)
        if val is None:
            continue

        try:
            raw = _encode_value(val, reg, endianness)
        except (ValueError, OverflowError, struct.error):
            continue

        for i in range(reg.count):
            abs_addr = base_addr + i
            if start_addr <= abs_addr < start_addr + quantity:
                offset = (abs_addr - start_addr) * 2
                reg_byte_offset = i * 2
                if reg_byte_offset + 2 <= len(raw):
                    image[offset : offset + 2] = raw[reg_byte_offset : reg_byte_offset + 2]
                    found_any = True

    return bytes(image) if found_any else None


WRITE_LOCK_SECONDS: Final[float] = 5.0
BATTERY_CONTROL_REGISTERS: Final[frozenset[int]] = frozenset({
    1034, 1038, 1040, 1042, 1044,  # Section 3.4 battery management
    1280, 1282, 1284, 1286, 1288,  # Section 3.5 G3 battery limits
})


class ModbusTcpProxyServer:
    """Modbus TCP proxy with write arbitration for multi-client safety.

    Read requests: served from coordinator cache or forwarded to inverter.

    Write requests: Battery control registers (1034, 1038, 1040, etc.)
    are subject to arbitration. When the internal SoC controller is active,
    external writes to battery registers are REJECTED with a Modbus
    exception (0x06 = Server Device Busy) and a log warning. This prevents
    conflicting control signals from evcc and our own controller.

    Non-battery writes are always forwarded.
    """

    def __init__(
        self,
        coordinator: Any,
        port: int = DEFAULT_PROXY_PORT,
        bind_host: str = DEFAULT_PROXY_BIND,
        unit_id: int = DEFAULT_UNIT_ID,
        endianness: str = "little",
        soc_controller: Any = None,
        installer_access: bool = False,
    ) -> None:
        self._coordinator = coordinator
        self._port = port
        self._bind_host = bind_host
        self._unit_id = unit_id
        self._endianness = endianness
        self._soc_controller = soc_controller
        self._installer_access = installer_access
        self._server: asyncio.Server | None = None
        self._clients: set[asyncio.Task[None]] = set()
        self._last_ext_write: dict[int, float] = {}  # address → timestamp

    @property
    def port(self) -> int:
        return self._port

    @property
    def bind_host(self) -> str:
        return self._bind_host

    @property
    def running(self) -> bool:
        return self._server is not None and self._server.is_serving()

    async def start(self) -> None:
        """Start listening for Modbus TCP connections."""
        self._server = await asyncio.start_server(
            self._handle_client, self._bind_host, self._port
        )
        _LOGGER.info(
            "Modbus TCP proxy started on %s:%d (unit_id=%d, endianness=%s)",
            self._bind_host,
            self._port,
            self._unit_id,
            self._endianness,
        )

    async def stop(self) -> None:
        """Stop the proxy server and disconnect all clients."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        for task in self._clients:
            task.cancel()
        self._clients.clear()

        _LOGGER.info("Modbus TCP proxy stopped")

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle a single client connection."""
        peer = writer.get_extra_info("peername")
        _LOGGER.info("Modbus proxy: client connected from %s", peer)
        task = asyncio.current_task()
        if task is not None:
            self._clients.add(task)

        try:
            while True:
                header = await reader.readexactly(MBAP_HEADER_SIZE)
                txn_id, proto_id, length, unit_id = struct.unpack(">HHHB", header)

                if proto_id != 0:
                    _LOGGER.debug("Non-Modbus protocol ID %d, ignoring", proto_id)
                    await reader.readexactly(length - 1)
                    continue

                if length < 2 or length > 260:
                    _LOGGER.debug("Proxy: invalid MBAP length %d, dropping", length)
                    if length > 1:
                        await reader.readexactly(min(length - 1, 260))
                    continue

                pdu = await reader.readexactly(length - 1)
                response_pdu = await self._process_pdu(pdu, unit_id)

                resp_length = len(response_pdu) + 1
                resp_header = struct.pack(">HHHB", txn_id, 0, resp_length, unit_id)
                writer.write(resp_header + response_pdu)
                await writer.drain()

        except asyncio.IncompleteReadError:
            _LOGGER.debug("Modbus proxy: client %s disconnected", peer)
        except asyncio.CancelledError:
            pass
        except Exception as err:
            _LOGGER.warning("Modbus proxy: client %s error: %s", peer, err)
        finally:
            writer.close()
            if task is not None:
                self._clients.discard(task)

    async def _process_pdu(self, pdu: bytes, unit_id: int) -> bytes:
        """Process a Modbus PDU and return a response PDU."""
        if len(pdu) < 1:
            return self._error_response(0, 0x01)

        fc = pdu[0]

        if fc in (FC_READ_HOLDING, FC_READ_INPUT):
            return await self._handle_read(pdu)
        elif fc == FC_WRITE_SINGLE:
            return await self._handle_write_single(pdu)
        elif fc == FC_WRITE_MULTIPLE:
            return await self._handle_write_multiple(pdu)
        else:
            return self._error_response(fc, 0x01)

    async def _handle_read(self, pdu: bytes) -> bytes:
        """Handle FC 03/04: Read Holding/Input Registers.

        Strategy:
        1. Try to serve from the coordinator's cached register data (fast path).
        2. On cache miss (e.g. SunSpec registers at 40000+), forward the raw
           read to the real inverter through the existing Modbus connection.
        """
        if len(pdu) < 5:
            return self._error_response(pdu[0], 0x03)

        fc = pdu[0]
        start_addr = struct.unpack(">H", pdu[1:3])[0]
        quantity = struct.unpack(">H", pdu[3:5])[0]

        if quantity < 1 or quantity > 125:
            return self._error_response(fc, 0x03)

        data = self._coordinator.data or {}
        image = _build_register_image(start_addr, quantity, data, self._endianness)

        if image is not None:
            byte_count = quantity * 2
            return struct.pack(">BB", fc, byte_count) + image

        # Cache miss → forward to the real inverter (SunSpec, unknown ranges)
        raw = await self._forward_read(start_addr, quantity)
        if raw is not None:
            byte_count = len(raw)
            return struct.pack(">BB", fc, byte_count) + raw

        return self._error_response(fc, 0x02)

    async def _forward_read(
        self, start_addr: int, quantity: int
    ) -> bytes | None:
        """Forward a register read to the real inverter via the coordinator's client."""
        client = getattr(self._coordinator, "client", None)
        if client is None or not getattr(client, "connected", False):
            _LOGGER.debug(
                "Proxy: cannot forward read at %d (client unavailable)", start_addr,
            )
            return None

        try:
            raw: bytes = await client._raw_read(start_addr, quantity)
            _LOGGER.debug(
                "Proxy: forwarded read at addr=%d qty=%d (%d bytes)",
                start_addr, quantity, len(raw),
            )
            return raw
        except Exception as err:
            _LOGGER.debug(
                "Proxy: forwarded read failed at addr=%d: %s", start_addr, err,
            )
            return None

    def _check_write_arbitration(self, address: int, peer: str = "") -> bytes | None:
        """Check if a write to this address is allowed.

        Returns a Modbus error response if blocked, None if allowed.
        Battery control registers are blocked when the internal SoC
        controller is active (returns 0x06 = Server Device Busy).
        """
        if address not in BATTERY_CONTROL_REGISTERS:
            return None

        ctrl = self._soc_controller
        if ctrl is not None and getattr(ctrl, "active", False):
            _LOGGER.warning(
                "Proxy: BLOCKED external write to battery register %d "
                "(SoC Controller is active, target=%.0f%%). "
                "Stop the SoC Controller first to allow external control.",
                address, ctrl.target_soc or 0,
            )
            return None  # We return 0x06 below per function code

        return None

    async def _handle_write_single(self, pdu: bytes) -> bytes:
        """Handle FC 06: Write Single Register with arbitration."""
        if len(pdu) < 5:
            return self._error_response(FC_WRITE_SINGLE, 0x03)

        address = struct.unpack(">H", pdu[1:3])[0]
        value = struct.unpack(">H", pdu[3:5])[0]

        if address in BATTERY_CONTROL_REGISTERS and not self._installer_access:
            _LOGGER.warning(
                "Proxy: REJECTED write to installer-protected register %d "
                "(service code missing in integration config).",
                address,
            )
            return self._error_response(FC_WRITE_SINGLE, 0x03)

        # Arbitration: block battery writes if SoC controller is active
        if address in BATTERY_CONTROL_REGISTERS:
            ctrl = self._soc_controller
            if ctrl is not None and getattr(ctrl, "active", False):
                _LOGGER.warning(
                    "Proxy: REJECTED write to reg %d = %d (SoC Controller active, "
                    "target=%.0f%%). External client should retry later.",
                    address, value, ctrl.target_soc or 0,
                )
                return self._error_response(FC_WRITE_SINGLE, 0x06)

        self._last_ext_write[address] = time.monotonic()

        reg = REGISTER_BY_ADDRESS.get(address)
        if reg is not None and reg.access == Access.RW:
            if reg.count > 1:
                _LOGGER.debug(
                    "Proxy: FC06 rejected for %d-register %s (use FC16)",
                    reg.count, reg.name,
                )
                return self._error_response(FC_WRITE_SINGLE, 0x03)
            try:
                await self._coordinator.async_write_by_address(address, value)
                _LOGGER.info("Proxy: write reg %d = %d (external)", address, value)
                return pdu[:5]
            except Exception as err:
                _LOGGER.warning("Proxy write failed at address %d: %s", address, err)
                return self._error_response(FC_WRITE_SINGLE, 0x04)

        raw_result = await self._forward_write_single(address, value)
        if raw_result:
            return pdu[:5]
        return self._error_response(FC_WRITE_SINGLE, 0x02)

    async def _forward_write_single(self, address: int, value: int) -> bool:
        """Forward a single-register write to the real inverter."""
        client = getattr(self._coordinator, "client", None)
        if client is None or not getattr(client, "connected", False):
            return False
        try:
            data = struct.pack(">H", value)
            await client._raw_write(address, data, 1)
            _LOGGER.debug("Proxy: forwarded write-single addr=%d val=%d", address, value)
            return True
        except Exception as err:
            _LOGGER.debug("Proxy: forwarded write-single failed at addr=%d: %s", address, err)
            return False

    async def _handle_write_multiple(self, pdu: bytes) -> bytes:
        """Handle FC 16: Write Multiple Registers with arbitration."""
        if len(pdu) < 6:
            return self._error_response(FC_WRITE_MULTIPLE, 0x03)

        start_addr = struct.unpack(">H", pdu[1:3])[0]
        quantity = struct.unpack(">H", pdu[3:5])[0]
        byte_count = pdu[5]

        if len(pdu) < 6 + byte_count:
            return self._error_response(FC_WRITE_MULTIPLE, 0x03)

        write_range = range(start_addr, start_addr + quantity)
        touches_battery_control = any(
            address in BATTERY_CONTROL_REGISTERS for address in write_range
        )

        if touches_battery_control and not self._installer_access:
            _LOGGER.warning(
                "Proxy: REJECTED write-multiple to installer-protected range "
                "%d..%d (service code missing in integration config).",
                start_addr,
                start_addr + quantity - 1,
            )
            return self._error_response(FC_WRITE_MULTIPLE, 0x03)

        # Arbitration: block battery writes if SoC controller is active
        if touches_battery_control:
            ctrl = self._soc_controller
            if ctrl is not None and getattr(ctrl, "active", False):
                _LOGGER.warning(
                    "Proxy: REJECTED write-multiple to reg %d qty %d "
                    "(SoC Controller active, target=%.0f%%). "
                    "External client should retry later.",
                    start_addr, quantity, ctrl.target_soc or 0,
                )
                return self._error_response(FC_WRITE_MULTIPLE, 0x06)

        self._last_ext_write[start_addr] = time.monotonic()
        reg_values = pdu[6 : 6 + byte_count]

        reg = REGISTER_BY_ADDRESS.get(start_addr)
        if reg is not None and reg.access == Access.RW and quantity == reg.count:
            try:
                decoded = self._decode_for_write(reg, reg_values)
                await self._coordinator.async_write_register(reg, decoded)
                _LOGGER.info(
                    "Proxy: write-multiple reg %d = %s (external)", start_addr, decoded,
                )
                return struct.pack(">BHH", FC_WRITE_MULTIPLE, start_addr, quantity)
            except Exception as err:
                _LOGGER.warning(
                    "Proxy write-multiple failed at address %d: %s", start_addr, err
                )
                return self._error_response(FC_WRITE_MULTIPLE, 0x04)

        raw_result = await self._forward_write_multiple(start_addr, quantity, reg_values)
        if raw_result:
            return struct.pack(">BHH", FC_WRITE_MULTIPLE, start_addr, quantity)
        return self._error_response(FC_WRITE_MULTIPLE, 0x02)

    async def _forward_write_multiple(
        self, start_addr: int, quantity: int, data: bytes
    ) -> bool:
        """Forward a multi-register write to the real inverter."""
        client = getattr(self._coordinator, "client", None)
        if client is None or not getattr(client, "connected", False):
            return False
        try:
            await client._raw_write(start_addr, data, quantity)
            _LOGGER.debug(
                "Proxy: forwarded write-multiple addr=%d qty=%d", start_addr, quantity,
            )
            return True
        except Exception as err:
            _LOGGER.debug(
                "Proxy: forwarded write-multiple failed at addr=%d: %s", start_addr, err,
            )
            return False

    def _decode_for_write(self, reg: ModbusRegister, raw: bytes) -> Any:
        """Decode raw register bytes from a write request into a Python value."""
        dt = reg.data_type

        if dt == DataType.UINT16:
            return struct.unpack(">H", raw[:2])[0]
        if dt == DataType.SINT16:
            return struct.unpack(">h", raw[:2])[0]
        if dt == DataType.FLOAT32:
            if self._endianness == "big":
                return struct.unpack(">f", raw[:4])[0]
            hi, lo = struct.unpack(">HH", raw[:4])
            return struct.unpack(">f", struct.pack(">HH", lo, hi))[0]
        if dt == DataType.UINT32:
            if self._endianness == "big":
                return struct.unpack(">I", raw[:4])[0]
            hi, lo = struct.unpack(">HH", raw[:4])
            return (lo << 16) | hi

        return struct.unpack(">H", raw[:2])[0]

    @staticmethod
    def _error_response(fc: int, exception_code: int) -> bytes:
        """Build a Modbus exception response PDU."""
        return struct.pack(">BB", fc | 0x80, exception_code)
