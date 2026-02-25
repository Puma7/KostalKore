"""Async Modbus TCP client for Kostal Plenticore inverters.

Wraps pymodbus ``AsyncModbusTcpClient`` with Kostal-specific encoding,
endianness handling, and error translation.
"""

from __future__ import annotations

import asyncio
import logging
import struct
from typing import Any, Final

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException as PyModbusException

from .modbus_registers import (
    Access,
    DataType,
    ModbusRegister,
    MONITORING_REGISTERS,
    REGISTER_BY_ADDRESS,
    REGISTER_BY_NAME,
    DEFAULT_MODBUS_PORT,
    DEFAULT_UNIT_ID,
    REG_BYTE_ORDER,
)

_LOGGER: Final = logging.getLogger(__name__)

CONNECT_TIMEOUT: Final[float] = 10.0
READ_TIMEOUT: Final[float] = 5.0


class ModbusClientError(Exception):
    """Base error for Modbus client operations."""


class ModbusConnectionError(ModbusClientError):
    """Raised when the Modbus TCP connection fails."""


class ModbusReadError(ModbusClientError):
    """Raised when a register read fails."""


class ModbusWriteError(ModbusClientError):
    """Raised when a register write fails."""


class KostalModbusClient:
    """Async Modbus TCP client for Kostal Plenticore inverters.

    Handles connection management, register encoding/decoding, and
    endianness detection. Thread-safe via asyncio lock.
    """

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_MODBUS_PORT,
        unit_id: int = DEFAULT_UNIT_ID,
        endianness: str = "little",
    ) -> None:
        self._host = host
        self._port = port
        self._unit_id = unit_id
        self._endianness = endianness
        self._client: AsyncModbusTcpClient | None = None
        self._lock = asyncio.Lock()

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.connected

    async def connect(self) -> bool:
        """Establish TCP connection to the inverter."""
        async with self._lock:
            if self.connected:
                return True
            try:
                self._client = AsyncModbusTcpClient(
                    host=self._host,
                    port=self._port,
                    timeout=CONNECT_TIMEOUT,
                )
                result = await self._client.connect()
                if not result:
                    raise ModbusConnectionError(
                        f"Failed to connect to {self._host}:{self._port}"
                    )
                _LOGGER.info(
                    "Modbus TCP connected to %s:%s (unit %s)",
                    self._host, self._port, self._unit_id,
                )
                return True
            except (OSError, PyModbusException, asyncio.TimeoutError) as err:
                self._client = None
                raise ModbusConnectionError(
                    f"Connection to {self._host}:{self._port} failed: {err}"
                ) from err

    async def disconnect(self) -> None:
        """Close the TCP connection."""
        async with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None
                _LOGGER.debug("Modbus TCP disconnected from %s", self._host)

    async def detect_endianness(self) -> str:
        """Read the byte order register and return 'little' or 'big'."""
        raw = await self._raw_read(REG_BYTE_ORDER.address, 1)
        value = struct.unpack(">H", raw)[0]
        detected = "big" if value == 1 else "little"
        self._endianness = detected
        _LOGGER.info("Detected endianness: %s", detected)
        return detected

    # ------------------------------------------------------------------
    # Public read/write API
    # ------------------------------------------------------------------

    async def read_register(self, register: ModbusRegister) -> Any:
        """Read and decode a single register or register block."""
        raw = await self._raw_read(register.address, register.count)
        return self._decode(raw, register)

    async def write_register(self, register: ModbusRegister, value: Any) -> None:
        """Encode and write a value to a register or register block."""
        if register.access != Access.RW:
            raise ModbusWriteError(f"Register {register.name} is read-only")
        encoded = self._encode(value, register)
        await self._raw_write(register.address, encoded, register.count)

    async def read_by_name(self, name: str) -> Any:
        """Read a register by its symbolic name."""
        reg = REGISTER_BY_NAME.get(name)
        if reg is None:
            raise ModbusReadError(f"Unknown register name: {name}")
        return await self.read_register(reg)

    async def write_by_name(self, name: str, value: Any) -> None:
        """Write a register by its symbolic name."""
        reg = REGISTER_BY_NAME.get(name)
        if reg is None:
            raise ModbusWriteError(f"Unknown register name: {name}")
        await self.write_register(reg, value)

    async def read_by_address(self, address: int) -> Any:
        """Read a register by its numeric address."""
        reg = REGISTER_BY_ADDRESS.get(address)
        if reg is None:
            raise ModbusReadError(f"Unknown register address: {address}")
        return await self.read_register(reg)

    async def write_by_address(self, address: int, value: Any) -> None:
        """Write a register by its numeric address."""
        reg = REGISTER_BY_ADDRESS.get(address)
        if reg is None:
            raise ModbusWriteError(f"Unknown register address: {address}")
        await self.write_register(reg, value)

    async def read_monitoring(self) -> dict[str, Any]:
        """Read all monitoring registers and return name→value dict."""
        result: dict[str, Any] = {}
        for reg in MONITORING_REGISTERS:
            try:
                result[reg.name] = await self.read_register(reg)
            except ModbusReadError:
                _LOGGER.debug("Skipping unavailable register %s", reg.name)
        return result

    # ------------------------------------------------------------------
    # Raw I/O (lock-protected)
    # ------------------------------------------------------------------

    async def _raw_read(self, address: int, count: int) -> bytes:
        """Read holding registers and return raw bytes."""
        async with self._lock:
            if not self.connected:
                raise ModbusConnectionError("Not connected")
            assert self._client is not None
            try:
                resp = await self._client.read_holding_registers(
                    address=address, count=count, device_id=self._unit_id,
                )
                if resp.isError():
                    raise ModbusReadError(
                        f"Error reading register {address}: {resp}"
                    )
                raw = b""
                for reg_val in resp.registers:
                    raw += struct.pack(">H", reg_val)
                return raw
            except PyModbusException as err:
                raise ModbusReadError(
                    f"Read failed at address {address}: {err}"
                ) from err

    async def _raw_write(
        self, address: int, data: bytes, count: int
    ) -> None:
        """Write raw bytes to holding registers."""
        async with self._lock:
            if not self.connected:
                raise ModbusConnectionError("Not connected")
            assert self._client is not None
            try:
                registers = [
                    struct.unpack(">H", data[i : i + 2])[0]
                    for i in range(0, len(data), 2)
                ]
                if count == 1:
                    resp = await self._client.write_register(
                        address=address,
                        value=registers[0],
                        device_id=self._unit_id,
                    )
                else:
                    resp = await self._client.write_registers(
                        address=address,
                        values=registers,
                        device_id=self._unit_id,
                    )
                if resp.isError():
                    raise ModbusWriteError(
                        f"Error writing register {address}: {resp}"
                    )
            except PyModbusException as err:
                raise ModbusWriteError(
                    f"Write failed at address {address}: {err}"
                ) from err

    # ------------------------------------------------------------------
    # Encoding / decoding
    # ------------------------------------------------------------------

    def _decode(self, raw: bytes, register: ModbusRegister) -> Any:
        """Decode raw bytes according to register data type and endianness."""
        dt = register.data_type

        if dt == DataType.UINT16:
            return struct.unpack(">H", raw[:2])[0]

        if dt == DataType.SINT16:
            return struct.unpack(">h", raw[:2])[0]

        if dt == DataType.UINT32:
            if self._endianness == "big":
                return struct.unpack(">I", raw[:4])[0]
            hi, lo = struct.unpack(">HH", raw[:4])
            return (lo << 16) | hi

        if dt == DataType.SINT32:
            if self._endianness == "big":
                return struct.unpack(">i", raw[:4])[0]
            hi, lo = struct.unpack(">HH", raw[:4])
            val = (lo << 16) | hi
            if val >= 0x80000000:
                val -= 0x100000000
            return val

        if dt == DataType.FLOAT32:
            if self._endianness == "big":
                return struct.unpack(">f", raw[:4])[0]
            hi, lo = struct.unpack(">HH", raw[:4])
            return struct.unpack(">f", struct.pack(">HH", lo, hi))[0]

        if dt == DataType.STRING:
            return raw.decode("ascii", errors="replace").rstrip("\x00").strip()

        if dt == DataType.BOOL:
            return struct.unpack(">H", raw[:2])[0] != 0

        if dt == DataType.UINT8:
            return raw[0]

        raise ModbusReadError(f"Unsupported data type: {dt}")

    def _encode(self, value: Any, register: ModbusRegister) -> bytes:
        """Encode a value to raw bytes according to register data type."""
        dt = register.data_type

        if dt == DataType.UINT16:
            return struct.pack(">H", int(value))

        if dt == DataType.SINT16:
            return struct.pack(">h", int(value))

        if dt == DataType.UINT32:
            v = int(value)
            if self._endianness == "big":
                return struct.pack(">I", v)
            hi = v & 0xFFFF
            lo = (v >> 16) & 0xFFFF
            return struct.pack(">HH", hi, lo)

        if dt == DataType.FLOAT32:
            fv = float(value)
            if self._endianness == "big":
                return struct.pack(">f", fv)
            packed = struct.pack(">f", fv)
            hi, lo = struct.unpack(">HH", packed)
            return struct.pack(">HH", lo, hi)

        if dt == DataType.BOOL:
            return struct.pack(">H", 1 if value else 0)

        raise ModbusWriteError(f"Cannot encode data type: {dt}")
