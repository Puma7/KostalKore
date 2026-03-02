"""Optional KSEM (Kostal Smart Energy Meter) Modbus coordinator."""

from __future__ import annotations

import asyncio
import logging
import struct
from datetime import timedelta
from typing import Any, Final

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException as PyModbusException

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER: Final = logging.getLogger(__name__)

KSEM_CONNECT_TIMEOUT: Final[float] = 10.0
KSEM_READ_TIMEOUT: Final[float] = 4.0
KSEM_DEFAULT_INTERVAL: Final[timedelta] = timedelta(seconds=10)


class KsemDataUpdateCoordinator(DataUpdateCoordinator[dict[str, float]]):
    """Coordinator polling core KSEM registers via Modbus TCP."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        *,
        port: int,
        unit_id: int,
        update_interval: timedelta = KSEM_DEFAULT_INTERVAL,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Kostal KSEM",
            update_interval=update_interval,
        )
        self._host = host
        self._port = int(port)
        self._unit_id = int(unit_id)
        self._client: AsyncModbusTcpClient | None = None
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.connected

    async def async_setup(self) -> None:
        await self._ensure_connected()

    async def async_shutdown(self) -> None:
        async with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None

    async def _ensure_connected(self) -> None:
        async with self._lock:
            if self.connected:
                return
            self._client = AsyncModbusTcpClient(
                host=self._host,
                port=self._port,
                timeout=KSEM_CONNECT_TIMEOUT,
            )
            ok = await self._client.connect()
            if not ok:
                self._client = None
                raise UpdateFailed(
                    f"KSEM connection failed to {self._host}:{self._port}"
                )

    async def _read_registers(self, address: int, count: int) -> list[int]:
        if not self.connected:
            await self._ensure_connected()
        assert self._client is not None
        try:
            response = await asyncio.wait_for(
                self._client.read_holding_registers(
                    address=address,
                    count=count,
                    device_id=self._unit_id,
                ),
                timeout=KSEM_READ_TIMEOUT,
            )
            if response.isError():
                raise UpdateFailed(f"KSEM read error at {address}: {response}")
            return list(response.registers)
        except (PyModbusException, OSError, asyncio.TimeoutError) as err:
            # reset connection and let next cycle reconnect
            if self._client is not None:
                self._client.close()
                self._client = None
            raise UpdateFailed(f"KSEM read failed at {address}: {err}") from err

    async def _read_u32(self, address: int, scale: float = 1.0) -> float:
        regs = await self._read_registers(address, 2)
        raw = struct.unpack(">I", struct.pack(">HH", regs[0], regs[1]))[0]
        return float(raw) * scale

    async def _read_i32(self, address: int, scale: float = 1.0) -> float:
        regs = await self._read_registers(address, 2)
        raw = struct.unpack(">i", struct.pack(">HH", regs[0], regs[1]))[0]
        return float(raw) * scale

    async def _async_update_data(self) -> dict[str, float]:
        await self._ensure_connected()

        # Read compact but high-value set for source precedence and diagnostics.
        active_import = await self._read_u32(0, 0.1)
        active_export = await self._read_u32(2, 0.1)
        frequency = await self._read_u32(26, 0.001)
        power_factor = await self._read_i32(24, 0.001)
        l1_voltage = await self._read_u32(62, 0.001)
        l2_voltage = await self._read_u32(102, 0.001)
        l3_voltage = await self._read_u32(142, 0.001)
        l1_active = await self._read_u32(40, 0.1)
        l2_active = await self._read_u32(80, 0.1)
        l3_active = await self._read_u32(120, 0.1)

        return {
            "active_power_import_w": active_import,
            "active_power_export_w": active_export,
            "net_active_power_w": active_import - active_export,
            "frequency_hz": frequency,
            "power_factor": power_factor,
            "l1_voltage_v": l1_voltage,
            "l2_voltage_v": l2_voltage,
            "l3_voltage_v": l3_voltage,
            "l1_active_power_w": l1_active,
            "l2_active_power_w": l2_active,
            "l3_active_power_w": l3_active,
        }
