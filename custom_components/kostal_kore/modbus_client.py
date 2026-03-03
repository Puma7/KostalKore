"""Async Modbus TCP client for Kostal Plenticore inverters.

Wraps pymodbus ``AsyncModbusTcpClient`` with Kostal-specific encoding,
endianness handling, error classification, and automatic retry for
transient faults (inverter busy, timeouts).

Error handling strategy:
- ILLEGAL FUNCTION (01): register not writable → permanent, no retry
- ILLEGAL DATA ADDRESS (02): register not on this model → permanent, skip
- ILLEGAL DATA VALUE (03): bad value sent → permanent, no retry
- SERVER DEVICE FAILURE (04): inverter internal error → retry once
- SERVER DEVICE BUSY (06): inverter busy → retry with backoff
- Connection lost / timeout → reconnect + retry
"""

from __future__ import annotations

import asyncio
import logging
import math
import struct
from enum import IntEnum
from typing import Any, Final

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import (
    ConnectionException as PyConnectionException,
    ModbusException as PyModbusException,
)

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
RETRY_DELAY_BUSY: Final[float] = 2.0
RETRY_DELAY_FAILURE: Final[float] = 1.0
MAX_RETRIES: Final[int] = 5

UNAVAILABLE_STRIKES_THRESHOLD: Final[int] = 3
UNAVAILABLE_RESET_INTERVAL: Final[int] = 120
OUTLIER_ABS_LIMIT_DEFAULT: Final[float] = 10_000_000.0

# Registers that hold identifiers/metadata/firmware, not telemetry measurements.
# These can have arbitrary large numeric values and must not be outlier-filtered.
OUTLIER_EXEMPT_ADDRESSES: Final[frozenset[int]] = frozenset({
    525,   # battery_model_id
    527,   # battery_serial_alt
    529,   # battery_operation_mode
    586,   # battery_fw_version
    515,   # fw_maincontroller
})

# Per-register absolute outlier limits that override the default.
OUTLIER_ABS_LIMIT_OVERRIDES: Final[dict[int, float]] = {
    120: 100_000_000.0,    # isolation_resistance – 0xFFFF kΩ sentinel = 65535000 Ω
    577: 100_000_000_000.0,  # generation_energy – lifetime Wh can reach GWh range
    104: 100_000_000.0,    # em_state – status register, not a measurement
}


class ModbusExceptionCode(IntEnum):
    """Modbus standard exception codes (from Kostal docs Section 2.1.7)."""

    ILLEGAL_FUNCTION = 0x01
    ILLEGAL_DATA_ADDRESS = 0x02
    ILLEGAL_DATA_VALUE = 0x03
    SERVER_DEVICE_FAILURE = 0x04
    ACKNOWLEDGE = 0x05
    SERVER_DEVICE_BUSY = 0x06
    MEMORY_PARITY_ERROR = 0x08
    GATEWAY_PATH_UNAVAILABLE = 0x0A
    GATEWAY_TARGET_FAILED = 0x0B


EXCEPTION_MESSAGES: Final[dict[int, str]] = {
    0x01: "Illegal function – register does not support this operation",
    0x02: "Illegal data address – register not available on this inverter model",
    0x03: "Illegal data value – value rejected by inverter",
    0x04: "Server device failure – inverter internal error",
    0x05: "Acknowledge – request received, processing",
    0x06: "Server device busy – inverter is processing another request, retry later",
    0x08: "Memory parity error – inverter memory fault",
    0x0A: "Gateway path unavailable",
    0x0B: "Gateway target device failed to respond",
}

TRANSIENT_CODES: Final[frozenset[int]] = frozenset({
    ModbusExceptionCode.SERVER_DEVICE_FAILURE,
    ModbusExceptionCode.SERVER_DEVICE_BUSY,
    ModbusExceptionCode.ACKNOWLEDGE,
})

PERMANENT_CODES: Final[frozenset[int]] = frozenset({
    ModbusExceptionCode.ILLEGAL_FUNCTION,
    ModbusExceptionCode.ILLEGAL_DATA_ADDRESS,
    ModbusExceptionCode.ILLEGAL_DATA_VALUE,
})


class ModbusClientError(Exception):
    """Base error for Modbus client operations."""


class ModbusConnectionError(ModbusClientError):
    """Raised when the Modbus TCP connection fails."""


class ModbusReadError(ModbusClientError):
    """Raised when a register read fails."""


class ModbusWriteError(ModbusClientError):
    """Raised when a register write fails."""


class ModbusTransientError(ModbusClientError):
    """Raised on transient faults that should be retried (busy, timeout)."""


class ModbusPermanentError(ModbusClientError):
    """Raised on permanent faults that should NOT be retried (illegal address)."""


def _classify_exception_response(resp: Any) -> ModbusClientError:
    """Classify a Modbus exception response into the right error type."""
    exc_code = getattr(resp, "exception_code", None)
    if exc_code is None:
        return ModbusReadError(f"Unknown Modbus error: {resp}")

    message = EXCEPTION_MESSAGES.get(exc_code, f"Unknown exception code 0x{exc_code:02X}")
    func_code = getattr(resp, "function_code", 0)

    if exc_code in TRANSIENT_CODES:
        return ModbusTransientError(
            f"Modbus transient error (func=0x{func_code:02X}, exc=0x{exc_code:02X}): {message}"
        )
    if exc_code in PERMANENT_CODES:
        return ModbusPermanentError(
            f"Modbus permanent error (func=0x{func_code:02X}, exc=0x{exc_code:02X}): {message}"
        )
    return ModbusReadError(
        f"Modbus error (func=0x{func_code:02X}, exc=0x{exc_code:02X}): {message}"
    )


class KostalModbusClient:
    """Async Modbus TCP client for Kostal Plenticore inverters.

    Handles connection management, register encoding/decoding, endianness
    detection, and automatic retry for transient faults.
    """

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_MODBUS_PORT,
        unit_id: int = DEFAULT_UNIT_ID,
        endianness: str = "little",
        request_scheduler: Any = None,
        outlier_policy: str = "keep_last",
    ) -> None:
        self._host = host
        self._port = port
        self._unit_id = unit_id
        self._endianness = endianness
        self._client: AsyncModbusTcpClient | None = None
        self._lock = asyncio.Lock()
        self._scheduler = request_scheduler
        self._unavailable_strikes: dict[int, int] = {}
        self._unavailable_suppressed: dict[int, float] = {}
        self._last_exc_code: int | None = None
        self._last_good_values: dict[int, Any] = {}
        self._outlier_policy = outlier_policy

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def unit_id(self) -> int:
        return self._unit_id

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.connected

    @property
    def endianness(self) -> str:
        return self._endianness

    @property
    def unavailable_registers(self) -> frozenset[int]:
        """Registers currently suppressed due to repeated ILLEGAL_DATA_ADDRESS."""
        return frozenset(self._unavailable_suppressed.keys())

    def reset_unavailable(self) -> None:
        """Reset all suppressed registers so they are retried on next poll.

        Call this after a firmware update or inverter swap.
        """
        count = len(self._unavailable_suppressed)
        self._unavailable_strikes.clear()
        self._unavailable_suppressed.clear()
        if count > 0:
            _LOGGER.info(
                "Reset %d suppressed registers – they will be retried on next poll",
                count,
            )

    def export_unavailable_state(self) -> dict[str, dict[str, int]]:
        """Export unavailable-register strikes for persistence.

        Suppression timestamps are intentionally not persisted because they are
        based on time.monotonic() and are not portable across restarts.
        """
        return {
            "strikes": {str(addr): int(v) for addr, v in self._unavailable_strikes.items()},
        }

    def import_unavailable_state(self, state: dict[str, dict[str, float | int]]) -> None:
        """Import persisted unavailable-register strikes.

        Legacy payloads may include a "suppressed" map. It is ignored because
        monotonic timestamps cannot be safely restored after restart.
        """
        strikes = state.get("strikes", {})
        self._unavailable_strikes = {int(addr): int(v) for addr, v in strikes.items()}
        self._unavailable_suppressed = {}

    def _is_suppressed(self, address: int) -> bool:
        """Check if a register is temporarily suppressed."""
        suppressed_at = self._unavailable_suppressed.get(address)
        if suppressed_at is None:
            return False
        import time
        if time.monotonic() - suppressed_at > UNAVAILABLE_RESET_INTERVAL * self._unavailable_strikes.get(address, 1):
            del self._unavailable_suppressed[address]
            _LOGGER.debug("Register %d suppression expired, will retry", address)
            return False
        return True

    def _record_unavailable_strike(self, address: int, name: str) -> None:
        """Record one ILLEGAL_DATA_ADDRESS strike for a register."""
        import time
        strikes = self._unavailable_strikes.get(address, 0) + 1
        self._unavailable_strikes[address] = strikes
        if strikes >= UNAVAILABLE_STRIKES_THRESHOLD:
            self._unavailable_suppressed[address] = time.monotonic()
            _LOGGER.info(
                "Register %s (addr %d) returned ILLEGAL_DATA_ADDRESS %d times "
                "– suppressing for %d poll cycles (auto-resets after %ds)",
                name, address, strikes,
                UNAVAILABLE_STRIKES_THRESHOLD,
                UNAVAILABLE_RESET_INTERVAL * strikes,
            )
        else:
            _LOGGER.debug(
                "Register %s (addr %d) ILLEGAL_DATA_ADDRESS strike %d/%d",
                name, address, strikes, UNAVAILABLE_STRIKES_THRESHOLD,
            )

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

    async def reconnect(self) -> bool:
        """Force-close and re-establish the connection."""
        await self.disconnect()
        return await self.connect()

    async def detect_endianness(self) -> str:
        """Read the byte order register and return 'little' or 'big'."""
        raw = await self._raw_read(REG_BYTE_ORDER.address, 1)
        value = struct.unpack(">H", raw)[0]
        detected = "big" if value == 1 else "little"
        self._endianness = detected
        _LOGGER.info("Detected endianness: %s", detected)
        return detected

    # ------------------------------------------------------------------
    # Public read/write API with retry
    # ------------------------------------------------------------------

    async def read_register(self, register: ModbusRegister) -> Any:
        """Read and decode a register with retry on transient errors.

        Strike system for ILLEGAL_DATA_ADDRESS:
        - 1st/2nd occurrence: logged as debug, register still polled next cycle
        - 3rd occurrence: register suppressed for increasing cooldown periods
        - Suppression auto-expires so firmware updates are picked up
        - Manual reset via reset_unavailable() after inverter swap
        """
        if self._is_suppressed(register.address):
            raise ModbusPermanentError(
                f"Register {register.name} (addr {register.address}) "
                f"is temporarily suppressed ({self._unavailable_strikes.get(register.address, 0)} strikes)"
            )

        for attempt in range(1 + MAX_RETRIES):
            try:
                raw = await self._raw_read(register.address, register.count)
                if register.address in self._unavailable_strikes:
                    self._unavailable_strikes.pop(register.address, None)
                    _LOGGER.info(
                        "Register %s (addr %d) is available again after previous failures",
                        register.name, register.address,
                    )
                decoded = self._decode(raw, register)
                filtered = self._apply_quality_filter(register, decoded)
                self._last_good_values[register.address] = filtered
                return filtered
            except ModbusPermanentError:
                if self._last_exc_code == ModbusExceptionCode.ILLEGAL_DATA_ADDRESS:
                    self._record_unavailable_strike(register.address, register.name)
                raise
            except ModbusTransientError as err:
                if attempt < MAX_RETRIES:
                    delay = RETRY_DELAY_BUSY if "busy" in str(err).lower() else RETRY_DELAY_FAILURE
                    _LOGGER.debug(
                        "Transient error reading %s, retry %d/%d in %.1fs: %s",
                        register.name, attempt + 1, MAX_RETRIES, delay, err,
                    )
                    await asyncio.sleep(delay)
                else:
                    raise ModbusReadError(
                        f"Read failed after {MAX_RETRIES} retries for {register.name}: {err}"
                    ) from err
            except (ModbusConnectionError, PyConnectionException) as err:
                if attempt < MAX_RETRIES:
                    _LOGGER.warning(
                        "Connection lost reading %s, reconnecting (attempt %d/%d)",
                        register.name, attempt + 1, MAX_RETRIES,
                    )
                    try:
                        await self.reconnect()
                        await self.detect_endianness()
                    except ModbusConnectionError:
                        pass
                else:
                    raise

        raise ModbusReadError(f"Read failed for {register.name} after all retries")

    def _apply_quality_filter(self, register: ModbusRegister, value: Any) -> Any:
        """Apply per-register quality guards (sentinel/outlier filtering)."""
        # Known inverter quirk: register 575 can return 32767 as invalid/sentinel.
        if register.address == 575:
            try:
                if int(value) >= 32767:
                    previous = self._last_good_values.get(register.address)
                    if previous is not None and self._outlier_policy == "keep_last":
                        _LOGGER.debug(
                            "Register %s returned sentinel %s, keeping previous value %s",
                            register.name,
                            value,
                            previous,
                        )
                        return previous
                    raise ModbusReadError(
                        f"Register {register.name} returned invalid sentinel value {value}"
                    )
            except (TypeError, ValueError):
                pass

        # Generic outlier guard for numeric telemetry.
        if isinstance(value, (int, float)):
            numeric_value = float(value)
            if math.isnan(numeric_value) or math.isinf(numeric_value):
                previous = self._last_good_values.get(register.address)
                if previous is not None and self._outlier_policy == "keep_last":
                    return previous
                raise ModbusReadError(
                    f"Register {register.name} returned NaN/Infinity"
                )
            if register.address not in OUTLIER_EXEMPT_ADDRESSES:
                abs_limit = OUTLIER_ABS_LIMIT_OVERRIDES.get(
                    register.address, OUTLIER_ABS_LIMIT_DEFAULT
                )
                if abs(numeric_value) > abs_limit:
                    previous = self._last_good_values.get(register.address)
                    if previous is not None and self._outlier_policy == "keep_last":
                        _LOGGER.debug(
                            "Register %s outlier %s exceeds abs limit, keeping %s",
                            register.name,
                            numeric_value,
                            previous,
                        )
                        return previous
                    raise ModbusReadError(
                        f"Register {register.name} value {numeric_value} exceeds absolute outlier limit"
                    )

        return value

    async def write_register(self, register: ModbusRegister, value: Any) -> None:
        """Encode and write a value with retry on transient errors."""
        if register.access != Access.RW:
            raise ModbusWriteError(f"Register {register.name} is read-only")
        encoded = self._encode(value, register)

        for attempt in range(1 + MAX_RETRIES):
            try:
                await self._raw_write(register.address, encoded, register.count)
                return
            except ModbusPermanentError:
                raise
            except ModbusTransientError as err:
                if attempt < MAX_RETRIES:
                    delay = RETRY_DELAY_BUSY if "busy" in str(err).lower() else RETRY_DELAY_FAILURE
                    _LOGGER.debug(
                        "Transient error writing %s, retry %d/%d in %.1fs: %s",
                        register.name, attempt + 1, MAX_RETRIES, delay, err,
                    )
                    await asyncio.sleep(delay)
                else:
                    raise ModbusWriteError(
                        f"Write failed after {MAX_RETRIES} retries for {register.name}: {err}"
                    ) from err
            except (ModbusConnectionError, PyConnectionException) as err:
                if attempt < MAX_RETRIES:
                    _LOGGER.warning(
                        "Connection lost writing %s, reconnecting (attempt %d/%d)",
                        register.name, attempt + 1, MAX_RETRIES,
                    )
                    try:
                        await self.reconnect()
                        await self.detect_endianness()
                    except ModbusConnectionError:
                        pass
                else:
                    raise

        raise ModbusWriteError(f"Write failed for {register.name} after all retries")

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
        """Read all monitoring registers and return name->value dict."""
        result: dict[str, Any] = {}
        for reg in MONITORING_REGISTERS:
            try:
                result[reg.name] = await self.read_register(reg)
            except ModbusPermanentError:
                pass
            except ModbusReadError:
                _LOGGER.debug("Skipping unavailable register %s", reg.name)
        return result

    # ------------------------------------------------------------------
    # Raw I/O (lock-protected)
    # ------------------------------------------------------------------

    async def _raw_read(self, address: int, count: int) -> bytes:
        """Read holding registers and return raw bytes."""
        if self._scheduler is not None:
            async with self._scheduler.request("modbus_read"):
                return await self._raw_read_inner(address, count)
        return await self._raw_read_inner(address, count)

    async def _raw_read_inner(self, address: int, count: int) -> bytes:
        async with self._lock:
            if not self.connected:
                raise ModbusConnectionError("Not connected")
            assert self._client is not None
            try:
                resp = await asyncio.wait_for(
                    self._client.read_holding_registers(
                        address=address, count=count, device_id=self._unit_id,
                    ),
                    timeout=READ_TIMEOUT,
                )
                if resp.isError():
                    exc_code = getattr(resp, "exception_code", None)
                    self._last_exc_code = exc_code
                    raise _classify_exception_response(resp)
                raw = b""
                for reg_val in resp.registers:
                    raw += struct.pack(">H", reg_val)
                return raw
            except asyncio.TimeoutError as err:
                raise ModbusTransientError(
                    f"Timeout reading register {address} (>{READ_TIMEOUT}s)"
                ) from err
            except (PyConnectionException, OSError) as err:
                self._client = None
                raise ModbusConnectionError(
                    f"Connection lost reading register {address}: {err}"
                ) from err
            except PyModbusException as err:
                raise ModbusReadError(
                    f"Read failed at address {address}: {err}"
                ) from err

    async def _raw_write(
        self, address: int, data: bytes, count: int
    ) -> None:
        """Write raw bytes to holding registers."""
        if self._scheduler is not None:
            async with self._scheduler.request("modbus_write"):
                return await self._raw_write_inner(address, data, count)
        return await self._raw_write_inner(address, data, count)

    async def _raw_write_inner(
        self, address: int, data: bytes, count: int
    ) -> None:
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
                    coro = self._client.write_register(
                        address=address,
                        value=registers[0],
                        device_id=self._unit_id,
                    )
                else:
                    coro = self._client.write_registers(
                        address=address,
                        values=registers,
                        device_id=self._unit_id,
                    )
                resp = await asyncio.wait_for(coro, timeout=READ_TIMEOUT)
                if resp.isError():
                    raise _classify_exception_response(resp)
            except asyncio.TimeoutError as err:
                raise ModbusTransientError(
                    f"Timeout writing register {address} (>{READ_TIMEOUT}s)"
                ) from err
            except (PyConnectionException, OSError) as err:
                self._client = None
                raise ModbusConnectionError(
                    f"Connection lost writing register {address}: {err}"
                ) from err
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
        try:
            return self._encode_inner(value, register)
        except (ValueError, OverflowError, struct.error) as err:
            raise ModbusWriteError(
                f"Cannot encode value {value!r} for register {register.name}: {err}"
            ) from err

    def _encode_inner(self, value: Any, register: ModbusRegister) -> bytes:
        """Inner encoding logic, may raise struct/value errors."""
        dt = register.data_type

        if dt == DataType.UINT16:
            return struct.pack(">H", int(value))

        if dt == DataType.SINT16:
            return struct.pack(">h", int(value))

        if dt == DataType.UINT32:
            v = int(value)
            if self._endianness == "big":
                return struct.pack(">I", v)
            lo_word = v & 0xFFFF
            hi_word = (v >> 16) & 0xFFFF
            return struct.pack(">HH", lo_word, hi_word)

        if dt == DataType.FLOAT32:
            fv = float(value)
            if math.isnan(fv) or math.isinf(fv):
                raise ModbusWriteError(
                    f"Refusing to write NaN/Infinity to register {register.name}"
                )
            if self._endianness == "big":
                return struct.pack(">f", fv)
            packed = struct.pack(">f", fv)
            hi, lo = struct.unpack(">HH", packed)
            return struct.pack(">HH", lo, hi)

        if dt == DataType.BOOL:
            return struct.pack(">H", 1 if value else 0)

        raise ModbusWriteError(f"Cannot encode data type: {dt}")
