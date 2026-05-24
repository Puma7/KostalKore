"""Dynamic battery charge limiter for grid feed-in optimization.

When enabled, limits battery charging so that grid feed-in stays at
the maximum allowed power. Only the surplus above the feed-in limit
goes into the battery.

Example: 20.9 kWp system, 12.4 kW feed-in limit (60% rule)
    PV produces 15 kW, house uses 0.8 kW
    → Available for grid: 15 - 0.8 = 14.2 kW
    → Feed-in limit: 12.4 kW
    → Battery charge limited to: 14.2 - 12.4 = 1.8 kW
    → Result: 12.4 kW grid feed-in + 1.8 kW battery + 0.8 kW house

When PV < feed-in limit: Battery charge = 0 (all to grid + house)
When the switch is OFF: Normal operation (battery charges freely)

Uses REG 1038 (bat_max_charge_limit) with 15s keepalive.
Reads total_dc_power and all home_from_* registers every cycle. DC PV is
scaled by INVERTER_DC_TO_AC_EFFICIENCY before comparing to AC home load.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any, Final

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory, UnitOfPower
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo

from .battery_reg_1038_owner import (
    OWNER_GRID_FEEDIN,
    acquire_reg_1038_or_raise,
    release_reg_1038,
)
from .helper import dc_pv_power_to_ac_estimate_w, sum_home_consumption_power_w
from .modbus_client import ModbusClientError
from .modbus_coordinator import ModbusDataUpdateCoordinator
from .modbus_registers import REGISTER_BY_NAME
from .power_limits import default_feed_in_limit_w, get_device_power_limit_w

_LOGGER: Final = logging.getLogger(__name__)

KEEPALIVE_INTERVAL: Final[float] = 15.0
CONTROL_INTERVAL: Final[float] = 10.0
MIN_CHARGE_POWER_W: Final[float] = 100.0


class GridFeedInLimiterSwitch(SwitchEntity):
    """Switch to enable dynamic battery charge limiting for grid feed-in optimization."""

    _attr_has_entity_name = True
    _attr_name = "Grid Feed-In Optimizer"
    _attr_icon = "mdi:transmission-tower-export"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: ModbusDataUpdateCoordinator,
        entry_id: str,
        device_info: DeviceInfo,
        hass: Any = None,
    ) -> None:
        self._coord = coordinator
        self._hass_ref = hass
        self._entry_id = entry_id
        self._device_power_limit_w = get_device_power_limit_w(coordinator)
        self._modbus_read_failed_cycles: int = 0
        self._attr_unique_id = f"{entry_id}_grid_feedin_optimizer"
        self._attr_device_info = device_info
        self._is_on = False
        self._task: asyncio.Task[None] | None = None
        self._feed_in_limit_w: float = default_feed_in_limit_w(self._device_power_limit_w)
        self._current_charge_limit: float = 0.0
        self._original_charge_limit: float | None = None
        self._restore_handled_in_turn_off: bool = False

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "feed_in_limit_w": self._feed_in_limit_w,
            "inverter_max_power_w": self._device_power_limit_w,
            "current_battery_charge_limit_w": (
                round(self._current_charge_limit, 0)
                if self._modbus_read_failed_cycles < 3
                else None
            ),
            "modbus_read_degraded": self._modbus_read_failed_cycles >= 3,
            "description": (
                f"Begrenzt Batterieladung so, dass max. {self._feed_in_limit_w:.0f}W "
                f"ins Netz eingespeist werden. Überschuss geht in die Batterie."
            ),
        }

    def set_feed_in_limit(self, watts: float) -> None:
        self._feed_in_limit_w = max(0.0, min(self._device_power_limit_w, watts))
        _LOGGER.info("Grid feed-in limit set to %.0f W", self._feed_in_limit_w)

    async def _snapshot_charge_limit(self) -> None:
        """Read and store the current charge limit before we start overwriting it."""
        if self._original_charge_limit is not None:
            return  # already snapshotted
        reg = REGISTER_BY_NAME.get("bat_max_charge_limit")
        if not reg:
            return
        try:
            val = float(await self._coord.client.read_register(reg))
            if not math.isnan(val) and not math.isinf(val) and val >= 0:
                self._original_charge_limit = val
                _LOGGER.debug("Snapshotted original charge limit: %.0f W", val)
        except (ModbusClientError, OSError, asyncio.TimeoutError, TypeError, ValueError) as err:
            _LOGGER.debug("Could not snapshot charge limit: %s", err)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._restore_handled_in_turn_off = False
        await self._snapshot_charge_limit()
        acquire_reg_1038_or_raise(self.hass, self._entry_id, OWNER_GRID_FEEDIN)
        self._is_on = True
        self._start_control()
        self.async_write_ha_state()
        _LOGGER.info("Grid Feed-In Optimizer ON (limit=%.0f W)", self._feed_in_limit_w)

    def _restore_limit(self) -> float:
        """Return the limit to restore: original snapshot or device max as fallback."""
        limit = self._original_charge_limit if self._original_charge_limit is not None else self._device_power_limit_w
        self._original_charge_limit = None
        return limit

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._is_on = False
        # Set before any await: cancelled _control_loop finally can run during
        # _write_charge_limit; without this it would call _restore_limit() again
        # after the snapshot was already consumed and fall back to device max.
        self._restore_handled_in_turn_off = True
        task = self._cancel_control()
        if isinstance(task, asyncio.Task):
            try:
                await task
            except asyncio.CancelledError:
                pass
        restore = self._restore_limit()
        await self._write_charge_limit(restore)
        release_reg_1038(self.hass, self._entry_id, OWNER_GRID_FEEDIN)
        self._current_charge_limit = 0.0
        self._modbus_read_failed_cycles = 0
        self.async_write_ha_state()
        _LOGGER.info("Grid Feed-In Optimizer OFF (charge limit restored to %.0f W)", restore)

    def _start_control(self) -> None:
        self._cancel_control()
        self._task = self.hass.async_create_task(
            self._control_loop(),
            "kostal_kore_grid_feedin_control",
        )

    def _cancel_control(self) -> asyncio.Task[None] | None:
        """Cancel the control loop task and clear ``_task``. Returns the task."""
        task = self._task
        if task is not None and not task.done():
            task.cancel()
        self._task = None
        return task

    async def _control_loop(self) -> None:
        """Dynamically adjust battery charge limit based on PV production."""
        try:
            while self._is_on:
                pv_power = await self._read_float("total_dc_power")
                # Full home consumption = PV share + battery share + grid share.
                # Using only home_from_pv (as before mqtt_bridge fix) underestimates
                # home load during battery discharge or grid consumption, causing the
                # limiter to allow more feed-in than the configured cap.
                home_pv = await self._read_float("home_from_pv")
                home_bat = await self._read_float("home_from_battery")
                home_grid = await self._read_float("home_from_grid")

                if pv_power is None:
                    self._modbus_read_failed_cycles += 1
                    await asyncio.sleep(CONTROL_INTERVAL)
                    continue

                self._modbus_read_failed_cycles = 0

                home = sum_home_consumption_power_w(
                    home_pv, home_bat, home_grid
                )
                if home is None:
                    _LOGGER.debug(
                        "FeedIn Optimizer: incomplete home_from_* — skip cycle"
                    )
                    await asyncio.sleep(CONTROL_INTERVAL)
                    continue

                ac_pv = dc_pv_power_to_ac_estimate_w(pv_power)
                available_for_grid = ac_pv - home
                surplus = available_for_grid - self._feed_in_limit_w

                if surplus > MIN_CHARGE_POWER_W:
                    new_limit = surplus
                else:
                    new_limit = 0.0

                new_limit = max(0.0, min(self._device_power_limit_w, new_limit))
                self._current_charge_limit = new_limit

                await self._write_charge_limit(new_limit)

                _LOGGER.debug(
                    "FeedIn Optimizer: PVdc=%.0fW PVac~=%.0fW Home=%.0fW "
                    "(pv=%.0f bat=%.0f grid=%.0f) "
                    "→ Available=%.0fW (charge limit=%.0fW, feed-in cap=%.0fW)",
                    pv_power,
                    ac_pv,
                    home,
                    home_pv or 0,
                    home_bat or 0,
                    home_grid or 0,
                    available_for_grid,
                    new_limit,
                    self._feed_in_limit_w,
                )

                self.async_write_ha_state()
                await asyncio.sleep(CONTROL_INTERVAL)

        except asyncio.CancelledError:
            return
        except Exception as err:
            _LOGGER.error("Grid Feed-In Optimizer error: %s", err)
        finally:
            # Always restore the inverter charge limit on exit. The previous
            # `if not self._is_on:` guard only restored on user-initiated
            # turn_off (which sets _is_on=False before the loop exits). When
            # an exception raised mid-loop, _is_on was still True, the guard
            # skipped restore, and the register stayed at whatever the last
            # write set — often 0 W or a low surplus value — silently
            # capping charge until the user toggled the optimizer again.
            # Restore is idempotent so the user-toggle path remains safe.
            self._is_on = False
            self.async_write_ha_state()
            try:
                if not getattr(self, "_restore_handled_in_turn_off", False):
                    await self._write_charge_limit(self._restore_limit())
            except Exception as restore_err:  # pragma: no cover
                _LOGGER.error(
                    "Failed to restore charge limit on optimizer exit: %s",
                    restore_err,
                )
            finally:
                self._restore_handled_in_turn_off = False
                release_reg_1038(self.hass, self._entry_id, OWNER_GRID_FEEDIN)

    async def _read_float(self, name: str) -> float | None:
        reg = REGISTER_BY_NAME.get(name)
        if not reg:
            return None
        try:
            val = float(await self._coord.client.read_register(reg))
            if math.isnan(val) or math.isinf(val):
                return None
            return val
        except Exception:
            return None

    async def _write_charge_limit(self, watts: float) -> None:
        reg = REGISTER_BY_NAME.get("bat_max_charge_limit")
        if reg:
            try:
                await self._coord.async_write_register(reg, watts)
            except Exception as err:
                _LOGGER.warning("Grid limiter write failed: %s", err)

    async def async_will_remove_from_hass(self) -> None:
        self._cancel_control()
        if self._is_on:
            self._is_on = False
            await self._write_charge_limit(self._restore_limit())
            release_reg_1038(self.hass, self._entry_id, OWNER_GRID_FEEDIN)
        await super().async_will_remove_from_hass()


class FeedInLimitNumber(NumberEntity):
    """Number entity to configure the grid feed-in limit in watts."""

    _attr_has_entity_name = True
    _attr_name = "Grid Feed-In Limit"
    _attr_icon = "mdi:transmission-tower"
    _attr_native_min_value = 0
    _attr_native_step = 100
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        limiter: GridFeedInLimiterSwitch,
        entry_id: str,
        device_info: DeviceInfo,
    ) -> None:
        self._limiter = limiter
        self._attr_unique_id = f"{entry_id}_grid_feedin_limit"
        self._attr_device_info = device_info
        self._attr_native_max_value = round(limiter._device_power_limit_w)

    @property
    def native_value(self) -> float:
        return self._limiter._feed_in_limit_w

    async def async_set_native_value(self, value: float) -> None:
        self._limiter.set_feed_in_limit(value)
        self.async_write_ha_state()
