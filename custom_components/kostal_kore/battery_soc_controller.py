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
import time
from typing import Any, Final

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


class BatterySocController:
    """Manages battery charge/discharge to reach a target SoC."""

    def __init__(
        self,
        coordinator: ModbusDataUpdateCoordinator,
        hass: Any = None,
    ) -> None:
        self._coord = coordinator
        self._hass = hass
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
        self._status: str = "idle"
        self._last_write: float = 0.0

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

    async def set_target(self, soc: float | None) -> None:
        """Set target SoC. None or <SAFE_MIN_SOC = stop controller.

        Values are clamped to SAFE_MIN_SOC..SAFE_MAX_SOC (10-95%).
        """
        if soc is not None and soc < SAFE_MIN_SOC:
            soc = None
        if soc is not None:
            soc = max(SAFE_MIN_SOC, min(SAFE_MAX_SOC, soc))

        self._target_soc = soc

        if soc is None:
            await self._stop()
            return

        _LOGGER.info("SoC Controller: target = %.0f%%", soc)
        await self._notify(
            "Ziel-SoC gesetzt",
            f"Batterie wird auf {soc:.0f}% gesteuert "
            f"(Bereich: {SAFE_MIN_SOC:.0f}-{SAFE_MAX_SOC:.0f}%).\n"
            f"Max. Laden: {self._max_charge_w:.0f} W\n"
            f"Max. Entladen: {self._max_discharge_w:.0f} W",
        )

        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self._run_loop())

    async def _stop(self) -> None:
        """Stop the controller and reset to automatic."""
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
        _LOGGER.info("SoC Controller: stopped, automatic mode")

    async def stop(self) -> None:
        """Public stop method."""
        await self._stop()

    # ------------------------------------------------------------------
    # Main control loop
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        """Main loop: monitor SoC and control battery until target reached.

        Stop logic handles Pylontech SoC jumps (e.g. 18% → 9%):
            Charging:    stop if current_soc >= target  (at or above)
            Discharging: stop if current_soc <= target  (at or below)
        """
        was_charging = False
        consecutive_read_fails = 0
        consecutive_write_fails = 0
        try:
            while self._target_soc is not None:
                current_soc = await self._read_soc()

                # NaN protection: treat NaN/Inf as read failure
                if current_soc is not None:
                    import math
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
                target_reached = False
                if need_charge is False and need_discharge is False:
                    target_reached = True
                elif was_charging and current_soc >= target:
                    target_reached = True
                elif not was_charging and need_discharge is False and current_soc <= target:
                    target_reached = True

                if target_reached:
                    self._status = f"target_reached ({current_soc:.0f}%)"
                    _LOGGER.info(
                        "SoC Controller: Ziel erreicht! SoC=%.0f%% (Ziel=%.0f%%)",
                        current_soc, target,
                    )
                    await self._write_normal()
                    await self._notify(
                        "Ziel-SoC erreicht",
                        f"Batterie bei {current_soc:.0f}% (Ziel: {target:.0f}%).\n"
                        f"Automatik-Modus wiederhergestellt.",
                    )
                    self._target_soc = None
                    self._status = "idle"
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
            await self._write_normal()

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
            except Exception:
                pass
        return True

    async def _write_normal(self) -> None:
        """Reset to automatic mode."""
        restore_limit = self._device_power_limit_w
        for name, val in (
            ("bat_charge_dc_abs_power", 0.0),
            ("bat_max_charge_limit", restore_limit),
            ("bat_max_discharge_limit", restore_limit),
        ):
            reg = REGISTER_BY_NAME.get(name)
            if reg:
                try:
                    await self._coord.async_write_register(reg, val)
                except Exception:
                    pass

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

    async def _notify(self, title: str, msg: str) -> None:
        if not self._hass:
            return
        try:
            await self._hass.services.async_call(
                "persistent_notification", "create",
                {"title": f"🔋 {title}", "message": msg,
                 "notification_id": "kostal_soc_controller"},
            )
        except Exception:
            pass
