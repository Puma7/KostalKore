"""DataUpdateCoordinator for Kostal Plenticore Modbus polling.

Periodically reads monitoring registers from the inverter via Modbus TCP
and provides the data to HA entities and the optional MQTT bridge.
"""

from __future__ import annotations

import logging
import math
import json
import time
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
    ModbusShutdownAbort,
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
        self._last_slow_data: dict[str, Any] = {}
        self._update_count: int = 0
        self._fast_error_count: int = 0
        self._last_slow_ts: float = 0.0
        self._write_audit: Any | None = None  # injected after init (WriteAuditLog)
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
        self._last_persisted_isolation_ohm: float | None = None
        self._shutting_down = False

    @property
    def client(self) -> KostalModbusClient:
        return self._client

    @property
    def device_info_data(self) -> dict[str, Any]:
        return self._device_info

    @property
    def poll_phase(self) -> int:
        """Current slow-poll phase (0 = slow poll just ran, 1–5 = fast-only cycles)."""
        return self._slow_tick

    @property
    def slow_data_age_s(self) -> float | None:
        """Seconds since the last successful slow poll, or None if none yet."""
        if self._last_slow_ts == 0.0:
            return None
        return time.monotonic() - self._last_slow_ts

    @property
    def update_count(self) -> int:
        return self._update_count

    async def async_setup(self) -> None:
        """Connect to the inverter and read initial device info."""
        await self._client.connect()
        await self._client.detect_endianness()
        await self._read_device_info()
        await self._load_register_capability_state()
        # NOTE: _restore_isolation_sample() is called from __init__.py
        # AFTER _health_monitor is injected, not here.

    async def async_shutdown(self) -> None:
        """Stop polling and permanently close the Modbus client.

        Client shutdown runs FIRST so that any in-progress TCP read sees a
        closed transport immediately when HA cancels the refresh task.
        Without this ordering, the pending asyncio.wait_for() inside
        _handle_refresh_interval can outlive HA's unload timeout, causing
        repeated FAILED_UNLOAD → setup-retry loops.
        """
        self._shutting_down = True
        await self._client.async_shutdown()
        await super().async_shutdown()

    def _cached_poll_data(self) -> dict[str, Any]:
        """Return last good poll data for graceful shutdown (no UpdateFailed)."""
        cached: dict[str, Any] = {}
        if self.data:
            cached.update(self.data)
        cached.update(self._last_slow_data)
        return cached

    async def _async_update_data(self) -> dict[str, Any]:
        """Poll monitoring registers with per-register error handling.

        - Connection lost → reconnect + re-detect endianness
        - Transient errors (busy/timeout) → already retried in client
        - Permanent errors (illegal address) → register skipped permanently
        - If ALL fast-poll registers fail → raise UpdateFailed
        """
        if self._shutting_down or self._client.closing:
            return self._cached_poll_data()
        if not self._client.connected:
            try:
                await self._client.connect()
                await self._client.detect_endianness()
                _LOGGER.info("Modbus reconnected to %s", self._client.host)
            except ModbusConnectionError as err:
                raise UpdateFailed(f"Modbus connection lost: {err}") from err

        self._update_count += 1
        data: dict[str, Any] = {}
        fast_total = 0
        fast_errors = 0
        permanent_skip = 0

        fast_regs = [r for r in MONITORING_REGISTERS if r.group in FAST_GROUPS]
        fast_total = len(fast_regs)

        try:
            batch_result = await self._client.read_registers_batch(fast_regs)
            data.update(batch_result)
            # QA-3: take a single snapshot of the suppressed-address set via the
            # public `unavailable_registers` property (returns a frozenset and has
            # no side effects). Calling `_is_suppressed()` here would mutate the
            # client's internal strike map between consecutive `sum()` calls.
            suppressed_snapshot = self._client.unavailable_registers
            fast_errors = sum(
                1 for r in fast_regs
                if r.name not in batch_result and r.address not in suppressed_snapshot
            )
            permanent_skip = sum(
                1 for r in fast_regs if r.address in suppressed_snapshot
            )
        except ModbusShutdownAbort:
            return self._cached_poll_data()
        except ModbusConnectionError as err:
            if self._shutting_down or self._client.closing:
                return self._cached_poll_data()
            raise UpdateFailed(f"Modbus connection lost: {err}") from err

        if fast_errors > 0:
            self._fast_error_count += fast_errors

        if fast_total > 0 and fast_errors >= fast_total:
            raise UpdateFailed(
                f"All {fast_total} fast-poll registers failed – inverter may be unreachable"
            )
        if fast_total > 0 and permanent_skip >= fast_total and not data:
            _LOGGER.debug(
                "All %d fast-poll registers permanently unavailable on this model",
                fast_total,
            )

        # Always merge last known slow values so entities that depend on
        # ENERGY/CONTROL/BATTERY_MGMT registers are not unavailable on the
        # 5 out of 6 ticks where we skip the slow poll.
        data.update(self._last_slow_data)

        self._slow_tick += 1
        if self._slow_tick >= 6:
            self._slow_tick = 0
            slow_regs = [r for r in MONITORING_REGISTERS if r.group in SLOW_GROUPS]
            try:
                slow_result = await self._client.read_registers_batch(slow_regs)
                self._last_slow_data = slow_result
                self._last_slow_ts = time.monotonic()
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
        import time

        from .helper import (
            ISOLATION_PERSIST_MAX_AGE_SECONDS,
            is_isolation_sentinel_ohm,
        )

        try:
            stored = await self._isolation_store.async_load()
        except Exception:
            return
        if not stored:
            return
        iso_ohm = stored.get("isolation_ohm")
        if iso_ohm is None:
            return
        saved_at = stored.get("saved_at")
        if saved_at is not None:
            try:
                age = time.time() - float(saved_at)
            except (TypeError, ValueError):
                age = ISOLATION_PERSIST_MAX_AGE_SECONDS + 1.0
            if age > ISOLATION_PERSIST_MAX_AGE_SECONDS:
                _LOGGER.debug(
                    "Skipping stale persisted isolation sample (age %.0fs)",
                    age,
                )
                return
        try:
            iso_float = float(iso_ohm)
        except (TypeError, ValueError):
            return
        if is_isolation_sentinel_ohm(iso_float):
            return
        if self._health_monitor is not None and hasattr(self._health_monitor, "isolation"):
            self._health_monitor.isolation.record(iso_float)
            _LOGGER.debug(
                "Restored last isolation resistance: %.0f Ω", iso_float
            )

    async def _save_isolation_sample(self, iso_ohm: float) -> None:
        """Persist the most recent isolation resistance value."""
        import time

        from .helper import is_isolation_sentinel_ohm

        if is_isolation_sentinel_ohm(iso_ohm):
            return
        if self._last_persisted_isolation_ohm == iso_ohm:
            return
        try:
            await self._isolation_store.async_save(
                {"isolation_ohm": iso_ohm, "saved_at": time.time()}
            )
            self._last_persisted_isolation_ohm = iso_ohm
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
        self,
        register: ModbusRegister,
        value: Any,
        *,
        audit_source: str = "modbus_coord",
    ) -> None:
        """Write a value to a control register with safety validation.

        audit_source lets callers (MQTT bridge, proxy) tag the event so the
        write-audit log distinguishes origin paths.
        """
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
            if self._write_audit is not None:
                from .write_audit import WriteEvent
                self._write_audit.log(WriteEvent(
                    ts=time.monotonic(),
                    source=audit_source,
                    key=register.name,
                    value=value,
                    result="ok",
                ))
        except Exception as err:
            _LOGGER.error("Modbus write failed for %s: %s", register.name, err)
            if self._write_audit is not None:
                from .write_audit import WriteEvent
                self._write_audit.log(WriteEvent(
                    ts=time.monotonic(),
                    source=audit_source,
                    key=register.name,
                    value=value,
                    result="error",
                    detail=str(err),
                ))
            raise

    async def async_write_by_name(self, name: str, value: Any) -> None:
        """Write a value to a register identified by name."""
        await self._client.write_by_name(name, value)
        _LOGGER.info("Wrote %s = %s via Modbus", name, value)

    async def async_write_by_address(
        self, address: int, value: Any, *, audit_source: str = "modbus_coord"
    ) -> None:
        """Write a value to a register identified by address.

        Audits the operation so direct address-write callers (currently only
        the Modbus TCP proxy) surface in the write log.
        """
        try:
            await self._client.write_by_address(address, value)
            _LOGGER.info("Wrote address %d = %s via Modbus", address, value)
            if self._write_audit is not None:
                from .write_audit import WriteEvent
                self._write_audit.log(WriteEvent(
                    ts=time.monotonic(),
                    source=audit_source,
                    key=f"addr:{address}",
                    value=value,
                    result="ok",
                ))
        except Exception as err:
            if self._write_audit is not None:
                from .write_audit import WriteEvent
                self._write_audit.log(WriteEvent(
                    ts=time.monotonic(),
                    source=audit_source,
                    key=f"addr:{address}",
                    value=value,
                    result="error",
                    detail=str(err),
                ))
            raise
