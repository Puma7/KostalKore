"""Automatic battery SoC controller.

Provides a simple "set target SoC" interface for end users. The controller
automatically charges from grid or discharges to grid to reach the target,
then returns to normal operation.

User interface (3 entities):
    number.XXX_battery_target_soc          → Target SoC (10-100%)
    number.XXX_battery_max_charge_power    → Max charge rate (W)
    number.XXX_battery_max_discharge_power → Max discharge rate (W)

When target_soc is set:
    - If current SoC < target → charge from grid at max_charge_power
    - If current SoC > target → discharge to grid at max_discharge_power
    - If current SoC == target (±1%) → stop, return to automatic

Respects the Kostal deadman switch by re-writing every 15s.
Uses the evcc-compatible register strategy (REG 1034).
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any, Final

from .battery_reg_1038_owner import (
    OWNER_SOC_CONTROLLER,
    acquire_reg_1038_or_raise,
    release_reg_1038,
)
from .helper import optional_float
from .modbus_client import ModbusClientError
from .modbus_coordinator import ModbusDataUpdateCoordinator
from .modbus_registers import REGISTER_BY_NAME
from .power_limits import (
    DEFAULT_CONTROL_LIMIT_W,
    clamp_control_power_w,
    get_device_power_limit_w,
)

_LOGGER: Final = logging.getLogger(__name__)

KEEPALIVE_INTERVAL: Final[float] = 15.0
POLL_INTERVAL: Final[float] = 10.0
DEFAULT_MAX_CHARGE_W: Final[float] = 5000.0
DEFAULT_MAX_DISCHARGE_W: Final[float] = 5000.0
SAFE_MIN_SOC: Final[float] = 10.0
SAFE_MAX_SOC: Final[float] = 95.0
MAX_BATTERY_TEMP_C: Final[float] = 48.0
MAX_CONSECUTIVE_FAILURES: Final[int] = 5
# Setpoint-divergence detection (read-only diagnostic): warn when the battery
# moves opposite to the commanded direction for this many consecutive poll
# cycles FOLLOWING SUCCESSFUL WRITES — a likely sign another controller (e.g.
# the inverter's native Smart AC Charge, default-on since FW 3.05, or a native
# battery schedule) is overriding us. The warning latches once per divergence
# episode and re-arms only after the same number of consecutive clean cycles
# (hysteresis), so an oscillating override cannot spam the log.
DIVERGENCE_DEADBAND_W: Final[float] = 200.0
DIVERGENCE_CYCLES: Final[int] = 4


class BatterySocController:
    """Manages battery charge/discharge to reach a target SoC."""

    def __init__(
        self,
        coordinator: ModbusDataUpdateCoordinator,
        hass: Any = None,
        entry_id: str = "",
    ) -> None:
        self._coord = coordinator
        self._hass = hass
        self._entry_id = entry_id
        self._device_power_limit_w = get_device_power_limit_w(
            coordinator, fallback_w=DEFAULT_CONTROL_LIMIT_W
        )
        self._target_soc: float | None = None
        self._max_charge_w: float = clamp_control_power_w(
            DEFAULT_MAX_CHARGE_W, device_limit_w=self._device_power_limit_w
        )
        self._max_discharge_w: float = clamp_control_power_w(
            DEFAULT_MAX_DISCHARGE_W, device_limit_w=self._device_power_limit_w
        )
        self._task: asyncio.Task[None] | None = None
        self._task_lock = asyncio.Lock()
        self._status: str = "idle"
        self._last_write: float = 0.0
        self._original_charge_limit: float | None = None
        self._original_discharge_limit: float | None = None
        self._divergence_count: int = 0
        self._divergence_warned: bool = False
        self._divergence_clear_count: int = 0

    @property
    def target_soc(self) -> float | None:
        return self._target_soc

    @property
    def max_charge_power(self) -> float:
        return self._max_charge_w

    @property
    def max_discharge_power(self) -> float:
        return self._max_discharge_w

    @property
    def status(self) -> str:
        return self._status

    @property
    def active(self) -> bool:
        return self._target_soc is not None and self._task is not None

    @property
    def device_power_limit(self) -> float:
        """Return inverter-specific power cap used for control entities."""
        return self._device_power_limit_w

    def set_max_charge_power(self, watts: float) -> None:
        self._max_charge_w = clamp_control_power_w(
            watts, device_limit_w=self._device_power_limit_w
        )
        _LOGGER.info("SoC Controller: max charge power = %.0f W", self._max_charge_w)

    def set_max_discharge_power(self, watts: float) -> None:
        self._max_discharge_w = clamp_control_power_w(
            watts, device_limit_w=self._device_power_limit_w
        )
        _LOGGER.info("SoC Controller: max discharge power = %.0f W", self._max_discharge_w)

    def _is_battery_test_running(self) -> bool:
        """Check if a battery test is currently running."""
        if self._hass is None:
            return False
        try:
            from .const import DOMAIN
            entry_store = self._hass.data.get(DOMAIN, {}).get(self._entry_id, {})
            battery_test = entry_store.get("battery_test")
            return battery_test is not None and battery_test.running
        except Exception:
            return False

    async def set_target(self, soc: float | None) -> None:
        """Set target SoC. None or <SAFE_MIN_SOC = stop controller.

        Values are clamped to SAFE_MIN_SOC..SAFE_MAX_SOC (10-95%).
        """
        if soc is not None and self._is_battery_test_running():
            _LOGGER.warning(
                "SoC Controller: cannot start — battery test is running"
            )
            return

        if soc is not None and soc < SAFE_MIN_SOC:
            soc = None
        if soc is not None:
            soc = max(SAFE_MIN_SOC, min(SAFE_MAX_SOC, soc))

        self._target_soc = soc

        if soc is None:
            await self._stop()
            return

        acquire_reg_1038_or_raise(self._hass, self._entry_id, OWNER_SOC_CONTROLLER)
        try:
            _LOGGER.info("SoC Controller: target = %.0f%%", soc)
            await self._notify(
                "Ziel-SoC gesetzt",
                f"Batterie wird auf {soc:.0f}% gesteuert "
                f"(Bereich: {SAFE_MIN_SOC:.0f}-{SAFE_MAX_SOC:.0f}%).\n"
                f"Max. Laden: {self._max_charge_w:.0f} W\n"
                f"Max. Entladen: {self._max_discharge_w:.0f} W",
            )

            # Guard task creation to prevent duplicate control loops when
            # two set_target() calls race across an await boundary.
            async with self._task_lock:
                if self._task is None or self._task.done():
                    if self._hass is not None:
                        self._task = self._hass.async_create_task(
                            self._run_loop(),
                            "kostal_kore_soc_controller",
                        )
                    else:
                        self._task = asyncio.ensure_future(self._run_loop())
        except BaseException:
            hass = getattr(self, "_hass", None)
            entry_id = getattr(self, "_entry_id", "")
            async with self._task_lock:
                task_running = self._task is not None and not self._task.done()
            if hass is not None and entry_id and not task_running:
                release_reg_1038(hass, entry_id, OWNER_SOC_CONTROLLER)
            raise

    async def _stop(self) -> None:
        """Stop the controller and reset to automatic."""
        async with self._task_lock:
            if self._task and not self._task.done():
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            self._task = None
        self._target_soc = None
        self._status = "idle"
        await self._write_normal()
        hass = getattr(self, "_hass", None)
        entry_id = getattr(self, "_entry_id", "")
        if hass is not None and entry_id:
            release_reg_1038(hass, entry_id, OWNER_SOC_CONTROLLER)
        _LOGGER.info("SoC Controller: stopped, automatic mode")

    async def stop(self) -> None:
        """Public stop method."""
        await self._stop()

    # ------------------------------------------------------------------
    # Main control loop
    # ------------------------------------------------------------------

    async def _snapshot_limits(self) -> None:
        """Read current charge/discharge limits before taking control."""
        for name, attr in (
            ("bat_max_charge_limit", "_original_charge_limit"),
            ("bat_max_discharge_limit", "_original_discharge_limit"),
        ):
            reg = REGISTER_BY_NAME.get(name)
            if not reg:
                continue
            try:
                val = float(await self._coord.client.read_register(reg))
                if not (math.isnan(val) or math.isinf(val)):
                    setattr(self, attr, val)
                    _LOGGER.debug("SoC Controller: snapshot %s = %.0f W", name, val)
            except (ModbusClientError, OSError, asyncio.TimeoutError, TypeError, ValueError) as err:
                _LOGGER.debug("SoC Controller: could not snapshot %s: %s", name, err)

    async def _run_loop(self) -> None:
        """Main loop: monitor SoC and control battery until target reached.

        Stop logic handles Pylontech SoC jumps (e.g. 18% → 9%):
            Charging:    stop if current_soc >= target  (at or above)
            Discharging: stop if current_soc <= target  (at or below)
        """
        await self._snapshot_limits()
        # Tri-state direction tracker. None = no action taken yet — the
        # overshoot stop branches MUST NOT fire on the first iteration,
        # otherwise (SoC < target, was_charging=False) would short-circuit
        # via "not was_charging and current_soc <= target" and the controller
        # would exit before any _write_charge call. True/False are set ONLY
        # after we actually wrote a charge / discharge command this cycle.
        was_charging: bool | None = None
        consecutive_read_fails = 0
        consecutive_write_fails = 0
        try:
            while self._target_soc is not None:
                current_soc = await self._read_soc()

                # NaN protection: treat NaN/Inf as read failure
                if current_soc is not None:
                    if math.isnan(current_soc) or math.isinf(current_soc):
                        current_soc = None

                if current_soc is None:
                    consecutive_read_fails += 1
                    self._status = f"error: SoC nicht lesbar ({consecutive_read_fails}x)"
                    if consecutive_read_fails >= MAX_CONSECUTIVE_FAILURES:
                        _LOGGER.error(
                            "SoC Controller: %d consecutive read failures, stopping",
                            consecutive_read_fails,
                        )
                        await self._notify(
                            "SoC Controller gestoppt",
                            f"SoC konnte {consecutive_read_fails}x nicht gelesen werden.\n"
                            f"Automatik-Modus wiederhergestellt.",
                        )
                        return
                    await asyncio.sleep(POLL_INTERVAL)
                    continue
                consecutive_read_fails = 0

                target = self._target_soc
                need_charge = current_soc < target
                need_discharge = current_soc > target

                # ── SAFE STOP: directional, handles SoC jumps ──
                # Use `is True` / `is False` so the overshoot branches only
                # trigger once was_charging has been EXPLICITLY set by a
                # previous write — never on the initial None state.
                target_reached = False
                if need_charge is False and need_discharge is False:
                    target_reached = True
                elif was_charging is True and current_soc >= target:
                    target_reached = True
                elif was_charging is False and current_soc <= target:
                    # We were discharging last cycle and SoC has reached / passed
                    # the target downward → done. (need_discharge is implicitly
                    # False here because current_soc <= target.)
                    target_reached = True

                if target_reached:
                    self._status = f"target_reached ({current_soc:.0f}%)"
                    _LOGGER.info(
                        "SoC Controller: Ziel erreicht! SoC=%.0f%% (Ziel=%.0f%%)",
                        current_soc, target,
                    )
                    await self._notify(
                        "Ziel-SoC erreicht",
                        f"Batterie bei {current_soc:.0f}% (Ziel: {target:.0f}%).\n"
                        f"Automatik-Modus wiederhergestellt.",
                    )
                    # finally block handles _target_soc=None, _task=None, _write_normal()
                    return

                # ── SAFETY CHECKS ──
                temp = await self._read_temp()
                if temp is not None and temp > MAX_BATTERY_TEMP_C:
                    self._status = f"paused: Temperatur {temp:.0f}°C"
                    await self._write_normal()
                    await asyncio.sleep(30)
                    continue

                inv_state = await self._read_inv_state()
                if inv_state in (0, 1, 10, 15):
                    self._status = f"paused: WR State={inv_state}"
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

                # ── CONTROL ──
                write_ok = False
                if need_charge:
                    was_charging = True
                    self._status = f"charging ({current_soc:.0f}% → {target:.0f}%)"
                    write_ok = await self._write_charge(self._max_charge_w)
                elif need_discharge:
                    was_charging = False
                    self._status = f"discharging ({current_soc:.0f}% → {target:.0f}%)"
                    write_ok = await self._write_discharge(self._max_discharge_w)

                if write_ok:
                    consecutive_write_fails = 0
                else:
                    consecutive_write_fails += 1
                    if consecutive_write_fails >= MAX_CONSECUTIVE_FAILURES:
                        _LOGGER.error(
                            "SoC Controller: %d consecutive write failures, stopping",
                            consecutive_write_fails,
                        )
                        await self._notify(
                            "SoC Controller gestoppt",
                            f"Register-Schreibfehler {consecutive_write_fails}x hintereinander.\n"
                            f"Automatik-Modus wiederhergestellt.",
                        )
                        return

                # Read-only: warn if another controller is overriding our
                # setpoint. Only meaningful after a SUCCESSFUL write — after a
                # failed write the battery never received our command, and
                # blaming "another controller" would misdirect diagnosis away
                # from the real fault (our own write failures).
                if write_ok:
                    await self._check_setpoint_divergence(need_charge, need_discharge)

                # Sleep, but cap at time-to-next-keepalive
                if self._last_write > 0:
                    elapsed = time.monotonic() - self._last_write
                    sleep = min(POLL_INTERVAL, KEEPALIVE_INTERVAL - elapsed - 1)
                else:
                    sleep = 2.0
                await asyncio.sleep(max(1.0, sleep))

        except asyncio.CancelledError:
            return
        except Exception as err:
            _LOGGER.error("SoC Controller error: %s", err)
            self._status = f"error: {err}"
        finally:
            self._target_soc = None
            self._task = None
            self._status = self._status if "error" in self._status else "idle"
            # Divergence latch is per control session: without this reset a
            # session that ended while diverging would suppress (warned=True)
            # or prematurely trigger (stale count) the warning in the NEXT
            # session, breaking the "consecutive cycles" semantics.
            self._divergence_count = 0
            self._divergence_clear_count = 0
            self._divergence_warned = False
            await self._write_normal()
            hass = getattr(self, "_hass", None)
            entry_id = getattr(self, "_entry_id", "")
            if hass is not None and entry_id:
                release_reg_1038(hass, entry_id, OWNER_SOC_CONTROLLER)

    # ------------------------------------------------------------------
    # Register I/O
    # ------------------------------------------------------------------

    async def _write_charge(self, power: float) -> bool:
        """Charge from grid: REG 1034 = -power (Kostal: negative=charge)."""
        reg = REGISTER_BY_NAME.get("bat_charge_dc_abs_power")
        if not reg:
            return False
        try:
            await self._coord.async_write_register(reg, float(-abs(power)))
            self._last_write = time.monotonic()
            return True
        except Exception as err:
            _LOGGER.warning("SoC Controller charge write failed: %s", err)
            return False

    async def _write_discharge(self, power: float) -> bool:
        """Discharge to grid: REG 1034 = +power, REG 1038 = 0."""
        reg1034 = REGISTER_BY_NAME.get("bat_charge_dc_abs_power")
        reg1038 = REGISTER_BY_NAME.get("bat_max_charge_limit")
        if not reg1034:
            return False
        try:
            await self._coord.async_write_register(reg1034, float(abs(power)))
            self._last_write = time.monotonic()
        except Exception as err:
            _LOGGER.warning("SoC Controller discharge write failed: %s", err)
            return False
        if reg1038:
            try:
                await self._coord.async_write_register(reg1038, 0.0)
            except Exception as err:
                _LOGGER.debug("SoC Controller: secondary reg1038 write failed: %s", err)
        return True

    async def _write_normal(self) -> None:
        """Reset to automatic mode, restoring original limits if available."""
        if self._coord.client.closing or not self._coord.client.connected:
            _LOGGER.debug(
                "SoC Controller: skip register restore (Modbus unavailable)"
            )
            return
        fallback = self._device_power_limit_w
        charge_limit = self._original_charge_limit if self._original_charge_limit is not None else fallback
        discharge_limit = self._original_discharge_limit if self._original_discharge_limit is not None else fallback
        for name, val in (
            ("bat_charge_dc_abs_power", 0.0),
            ("bat_max_charge_limit", charge_limit),
            ("bat_max_discharge_limit", discharge_limit),
        ):
            reg = REGISTER_BY_NAME.get(name)
            if reg:
                try:
                    await self._coord.async_write_register(reg, val)
                except Exception as err:
                    _LOGGER.warning("SoC Controller: failed to reset %s: %s", name, err)
        self._original_charge_limit = None
        self._original_discharge_limit = None

    async def _read_soc(self) -> float | None:
        reg = REGISTER_BY_NAME.get("battery_soc")
        if not reg:
            return None
        try:
            return float(await self._coord.client.read_register(reg))
        except Exception:
            return None

    async def _read_temp(self) -> float | None:
        reg = REGISTER_BY_NAME.get("battery_temperature")
        if not reg:
            return None
        try:
            return float(await self._coord.client.read_register(reg))
        except Exception:
            return None

    async def _read_inv_state(self) -> int | None:
        reg = REGISTER_BY_NAME.get("inverter_state")
        if not reg:
            return None
        try:
            return int(await self._coord.client.read_register(reg))
        except Exception:
            return None

    def _read_battery_power(self) -> float | None:
        """Actual battery charge/discharge power in W (+discharge / -charge).

        Read from the Modbus coordinator's cache (``battery_cd_power`` is in a
        FAST-polled group, refreshed every ~5s) instead of issuing an extra
        serial Modbus transaction per control cycle — 5s-stale data is well
        within tolerance for the multi-cycle divergence heuristic.
        """
        data = self._coord.data
        if not data:
            return None
        return optional_float(data.get("battery_cd_power"))

    async def _check_setpoint_divergence(
        self, need_charge: bool, need_discharge: bool
    ) -> None:
        """Warn once per episode if the battery moves against the command.

        Read-only diagnostic — never affects the control loop.
        ``battery_cd_power`` is +discharge / -charge; sustained opposite
        movement after successful writes is a likely sign that another
        controller (e.g. the inverter's native Smart AC Charge, default-on
        since FW 3.05, or a native battery schedule) is overriding KORE.
        The warning latches after DIVERGENCE_CYCLES diverging cycles and
        re-arms only after DIVERGENCE_CYCLES consecutive clean cycles, so an
        override oscillating around the deadband cannot spam the log.
        """
        try:
            power = self._read_battery_power()
            if power is None:
                return
            diverging = (need_charge and power > DIVERGENCE_DEADBAND_W) or (
                need_discharge and power < -DIVERGENCE_DEADBAND_W
            )
            if not diverging:
                self._divergence_count = 0
                if self._divergence_warned:
                    self._divergence_clear_count += 1
                    if self._divergence_clear_count >= DIVERGENCE_CYCLES:
                        self._divergence_warned = False
                        self._divergence_clear_count = 0
                return
            self._divergence_clear_count = 0
            self._divergence_count += 1
            if (
                self._divergence_count >= DIVERGENCE_CYCLES
                and not self._divergence_warned
            ):
                self._divergence_warned = True
                direction = "charge" if need_charge else "discharge"
                _LOGGER.warning(
                    "SoC Controller: battery is moving opposite to the commanded "
                    "%s for %d cycles (measured %.0f W). Another controller may be "
                    "active — e.g. the inverter's native Smart AC Charge (default-on "
                    "since FW 3.05) or a native battery schedule. Disable it for "
                    "reliable external battery control.",
                    direction,
                    self._divergence_count,
                    power,
                )
                # Also surface via persistent notification — users who don't
                # tail the log otherwise only see a battery that won't follow.
                await self._notify(
                    "Batteriesteuerung wird übersteuert?",
                    f"Die Batterie läuft seit {self._divergence_count} Zyklen "
                    f"entgegen dem Befehl ({direction}, gemessen {power:.0f} W).\n"
                    "Mögliche Ursache: native Batteriesteuerung des Wechselrichters "
                    "(z. B. Smart AC Charge, ab FW 3.05 standardmäßig aktiv) oder "
                    "ein natives Ladeprogramm. Für zuverlässige externe Steuerung "
                    "diese Funktion deaktivieren.",
                )
        except Exception:  # diagnostic must never affect the control loop
            _LOGGER.debug("SoC Controller: divergence check failed", exc_info=True)

    async def _notify(self, title: str, msg: str) -> None:
        if not self._hass:
            return
        try:
            await self._hass.services.async_call(
                "persistent_notification", "create",
                {"title": f"🔋 {title}", "message": msg,
                 "notification_id": f"kostal_soc_controller_{self._entry_id}"},
            )
        except Exception:  # notification is non-critical, keep broad
            _LOGGER.debug("Failed to send SoC controller notification")
