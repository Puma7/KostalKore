"""DataUpdateCoordinator for Kostal Plenticore Modbus polling.

Periodically reads monitoring registers from the inverter via Modbus TCP
and provides the data to HA entities and the optional MQTT bridge.
"""

from __future__ import annotations

import logging
import math
import json
from datetime import timedelta
from typing import Any, Final

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .modbus_client import (
    KostalModbusClient,
    ModbusClientError,
    ModbusConnectionError,
    ModbusPermanentError,
    ModbusTransientError,
)
from .modbus_registers import (
    ALL_REGISTERS,
    MONITORING_REGISTERS,
    Access,
    ModbusRegister,
    RegisterGroup,
    REG_INVERTER_STATE,
    REG_INVERTER_MAX_POWER,
    REG_SERIAL_NUMBER,
    REG_PRODUCT_NAME,
    REG_SW_VERSION,
    REG_NUM_PV_STRINGS,
    REG_NUM_BIDIRECTIONAL,
    REG_BATTERY_TYPE,
    REG_BATTERY_MGMT_MODE,
)

_LOGGER: Final = logging.getLogger(__name__)

FAST_POLL_INTERVAL: Final[timedelta] = timedelta(seconds=5)
SLOW_POLL_INTERVAL: Final[timedelta] = timedelta(seconds=30)
DEVICE_INFO_POLL_INTERVAL: Final[timedelta] = timedelta(minutes=5)
CAPABILITY_STORE_VERSION: Final[int] = 1

FAST_GROUPS: Final[frozenset[RegisterGroup]] = frozenset({
    RegisterGroup.POWER,
    RegisterGroup.PHASE,
    RegisterGroup.BATTERY,
    RegisterGroup.POWERMETER,
})

SLOW_GROUPS: Final[frozenset[RegisterGroup]] = frozenset({
    RegisterGroup.ENERGY,
    RegisterGroup.CONTROL,
    RegisterGroup.BATTERY_MGMT,
    RegisterGroup.BATTERY_LIMIT_G3,
    RegisterGroup.IO_BOARD,
})


class ModbusDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that polls Kostal Plenticore registers via Modbus TCP.

    Data is returned as a flat dict mapping register name → decoded value.
    Write operations are exposed for control registers.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: KostalModbusClient,
        update_interval: timedelta = FAST_POLL_INTERVAL,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Kostal Modbus",
            update_interval=update_interval,
        )
        self._client = client
        self._slow_tick = 0
        self._device_info_tick = 0
        self._device_info: dict[str, Any] = {}
        self._capability_store = Store[dict[str, Any]](
            hass,
            CAPABILITY_STORE_VERSION,
            f"kostal_kore_modbus_caps_{client.host}_{client.port}",
        )
        self._last_saved_capability_state: str = ""
        self._isolation_store = Store[dict[str, Any]](
            hass,
            1,
            f"kostal_kore_isolation_{client.host}_{client.port}",
        )
        self._health_monitor: Any | None = None  # injected after init if available

    @property
    def client(self) -> KostalModbusClient:
        return self._client

    @property
    def device_info_data(self) -> dict[str, Any]:
        return self._device_info

    async def async_setup(self) -> None:
        """Connect to the inverter and read initial device info."""
        await self._client.connect()
        await self._client.detect_endianness()
        await self._read_device_info()
        await self._load_register_capability_state()
        # NOTE: _restore_isolation_sample() is called from __init__.py
        # AFTER _health_monitor is injected, not here.

    async def async_shutdown(self) -> None:
        """Disconnect from the inverter."""
        await self._client.disconnect()

    async def _async_update_data(self) -> dict[str, Any]:
        """Poll monitoring registers with per-register error handling.

        - Connection lost → reconnect + re-detect endianness
        - Transient errors (busy/timeout) → already retried in client
        - Permanent errors (illegal address) → register skipped permanently
        - If ALL fast-poll registers fail → raise UpdateFailed
        """
        if not self._client.connected:
            try:
                await self._client.connect()
                await self._client.detect_endianness()
                _LOGGER.info("Modbus reconnected to %s", self._client.host)
            except ModbusConnectionError as err:
                raise UpdateFailed(f"Modbus connection lost: {err}") from err

        data: dict[str, Any] = {}
        fast_total = 0
        fast_errors = 0
        permanent_skip = 0

        fast_regs = [r for r in MONITORING_REGISTERS if r.group in FAST_GROUPS]
        fast_total = len(fast_regs)

        try:
            batch_result = await self._client.read_registers_batch(fast_regs)
            data.update(batch_result)
            # Single snapshot of suppressed addresses – no side effects, no TOCTOU. // GEÄNDERT
            suppressed_snapshot = self._client.unavailable_registers
            fast_errors = sum(
                1 for r in fast_regs
                if r.name not in batch_result and r.address not in suppressed_snapshot
            )
            permanent_skip = sum(
                1 for r in fast_regs if r.address in suppressed_snapshot
            )
        except ModbusConnectionError as err:
            raise UpdateFailed(f"Modbus connection lost: {err}") from err

        if fast_total > 0 and fast_errors >= fast_total:
            raise UpdateFailed(
                f"All {fast_total} fast-poll registers failed – inverter may be unreachable"
            )
        if fast_total > 0 and permanent_skip >= fast_total and not data:
            _LOGGER.debug(
                "All %d fast-poll registers permanently unavailable on this model",
                fast_total,
            )

        self._slow_tick += 1
        if self._slow_tick >= 6:
            self._slow_tick = 0
            slow_regs = [r for r in MONITORING_REGISTERS if r.group in SLOW_GROUPS]
            try:
                slow_result = await self._client.read_registers_batch(slow_regs)
                data.update(slow_result)
            except ModbusConnectionError as err:
                _LOGGER.debug("Slow-poll connection lost: %s", err)
            except ModbusClientError as err:
                _LOGGER.debug("Slow-poll batch failed: %s", err)

        self._device_info_tick += 1
        if self._device_info_tick >= 60:
            self._device_info_tick = 0
            await self._read_device_info()

        await self._save_register_capability_state_if_changed()
        return data

    def _capability_signature(self) -> str:
        sw_version = str(self._device_info.get("sw_version", "unknown"))
        return (
            f"{self._client.host}:{self._client.port}:{self._client.unit_id}:{sw_version}"
        )

    async def _load_register_capability_state(self) -> None:
        """Load persisted unavailable-register state if signature still matches."""
        try:
            stored = await self._capability_store.async_load()
        except Exception as err:
            _LOGGER.debug("Could not load Modbus capability cache: %s", err)
            return
        if not stored:
            return
        if stored.get("signature") != self._capability_signature():
            _LOGGER.debug("Ignoring stale Modbus capability cache (signature mismatch)")
            return
        raw_state = stored.get("state")
        if isinstance(raw_state, dict):
            try:
                self._client.import_unavailable_state(raw_state)
                self._last_saved_capability_state = json.dumps(raw_state, sort_keys=True)
                _LOGGER.debug("Loaded persisted Modbus capability state")
            except Exception as err:
                _LOGGER.debug("Invalid persisted Modbus capability state: %s", err)

    async def _save_register_capability_state_if_changed(self) -> None:
        """Persist unavailable-register state if it changed."""
        raw_state = self._client.export_unavailable_state()
        encoded_state = json.dumps(raw_state, sort_keys=True)
        if encoded_state == self._last_saved_capability_state:
            return
        payload = {
            "signature": self._capability_signature(),
            "state": raw_state,
        }
        try:
            await self._capability_store.async_save(payload)
            self._last_saved_capability_state = encoded_state
        except Exception as err:
            _LOGGER.debug("Could not persist Modbus capability state: %s", err)

    async def _restore_isolation_sample(self) -> None:
        """Seed the health-monitor deque with the last persisted isolation value."""
        try:
            stored = await self._isolation_store.async_load()
        except Exception:
            return
        if not stored:
            return
        iso_ohm = stored.get("isolation_ohm")
        if iso_ohm is None:
            return
        if self._health_monitor is not None and hasattr(self._health_monitor, "isolation"):
            try:
                self._health_monitor.isolation.record(float(iso_ohm))
                _LOGGER.debug(
                    "Restored last isolation resistance: %.0f Ω", float(iso_ohm)
                )
            except (TypeError, ValueError):
                pass

    async def _save_isolation_sample(self, iso_ohm: float) -> None:
        """Persist the most recent isolation resistance value."""
        try:
            await self._isolation_store.async_save({"isolation_ohm": iso_ohm})
        except Exception as err:
            _LOGGER.debug("Could not persist isolation resistance: %s", err)

    async def _read_device_info(self) -> None:
        """Read static device information registers."""
        info_regs = [
            REG_SERIAL_NUMBER, REG_PRODUCT_NAME, REG_SW_VERSION,
            REG_NUM_PV_STRINGS, REG_NUM_BIDIRECTIONAL,
            REG_INVERTER_STATE, REG_BATTERY_TYPE,
            REG_INVERTER_MAX_POWER, REG_BATTERY_MGMT_MODE,
        ]
        for reg in info_regs:
            try:
                self._device_info[reg.name] = await self._client.read_register(reg)
            except ModbusClientError:
                _LOGGER.debug("Could not read device info register %s", reg.name)

    async def async_write_register(
        self, register: ModbusRegister, value: Any
    ) -> None:
        """Write a value to a control register with safety validation."""
        if register.access != Access.RW:
            raise ValueError(f"Register {register.name} is read-only")

        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            raise ValueError(
                f"Refusing to write NaN/Infinity to register {register.name}"
            )

        try:
            await self._client.write_register(register, value)
            _LOGGER.info(
                "Wrote %s = %s to inverter via Modbus", register.name, value
            )
        except ModbusClientError as err:
            _LOGGER.error("Modbus write failed for %s: %s", register.name, err)
            raise

    async def async_write_by_name(self, name: str, value: Any) -> None:
        """Write a value to a register identified by name."""
        await self._client.write_by_name(name, value)
        _LOGGER.info("Wrote %s = %s via Modbus", name, value)

    async def async_write_by_address(self, address: int, value: Any) -> None:
        """Write a value to a register identified by address."""
        await self._client.write_by_address(address, value)
        _LOGGER.info("Wrote address %d = %s via Modbus", address, value)
