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
Reads total_dc_power and pm_total_active every cycle to adjust dynamically.
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
from homeassistant.helpers.device_registry import DeviceInfo

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
        self._device_power_limit_w = get_device_power_limit_w(coordinator)
        self._attr_unique_id = f"{entry_id}_grid_feedin_optimizer"
        self._attr_device_info = device_info
        self._is_on = False
        self._task: asyncio.Task[None] | None = None
        self._feed_in_limit_w: float = default_feed_in_limit_w(self._device_power_limit_w)
        self._current_charge_limit: float = 0.0

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "feed_in_limit_w": self._feed_in_limit_w,
            "inverter_max_power_w": self._device_power_limit_w,
            "current_battery_charge_limit_w": round(self._current_charge_limit, 0),
            "description": (
                f"Begrenzt Batterieladung so, dass max. {self._feed_in_limit_w:.0f}W "
                f"ins Netz eingespeist werden. Überschuss geht in die Batterie."
            ),
        }

    def set_feed_in_limit(self, watts: float) -> None:
        self._feed_in_limit_w = max(0.0, min(self._device_power_limit_w, watts))
        _LOGGER.info("Grid feed-in limit set to %.0f W", self._feed_in_limit_w)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._is_on = True
        self._start_control()
        self.async_write_ha_state()
        _LOGGER.info("Grid Feed-In Optimizer ON (limit=%.0f W)", self._feed_in_limit_w)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._is_on = False
        self._cancel_control()
        await self._write_charge_limit(self._device_power_limit_w)
        self._current_charge_limit = 0.0
        self.async_write_ha_state()
        _LOGGER.info("Grid Feed-In Optimizer OFF (normal charging restored)")

    def _start_control(self) -> None:
        self._cancel_control()
        self._task = self.hass.async_create_task(
            self._control_loop(),
            "kostal_kore_grid_feedin_control",
        )

    def _cancel_control(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _control_loop(self) -> None:
        """Dynamically adjust battery charge limit based on PV production."""
        try:
            while self._is_on:
                pv_power = await self._read_float("total_dc_power")
                home_consumption = await self._read_float("home_from_pv")
                grid_power = await self._read_float("pm_total_active")
                bat_power = await self._read_float("battery_cd_power")

                if pv_power is None:
                    await asyncio.sleep(CONTROL_INTERVAL)
                    continue

                home = abs(home_consumption or 0)
                # Kostal: battery_cd_power negative = charging
                current_bat_charge = abs(min(bat_power or 0, 0))

                available_for_grid = pv_power - home
                surplus = available_for_grid - self._feed_in_limit_w

                if surplus > MIN_CHARGE_POWER_W:
                    new_limit = surplus
                else:
                    new_limit = 0.0

                new_limit = max(0.0, min(self._device_power_limit_w, new_limit))
                self._current_charge_limit = new_limit

                await self._write_charge_limit(new_limit)

                _LOGGER.debug(
                    "FeedIn Optimizer: PV=%.0fW Home=%.0fW → Grid=%.0fW + Bat=%.0fW "
                    "(limit=%.0fW, feed-in cap=%.0fW)",
                    pv_power, home, available_for_grid, new_limit,
                    new_limit, self._feed_in_limit_w,
                )

                self.async_write_ha_state()
                await asyncio.sleep(CONTROL_INTERVAL)

        except asyncio.CancelledError:
            return
        except Exception as err:
            _LOGGER.error("Grid Feed-In Optimizer error: %s", err)
        finally:
            if not self._is_on:
                await self._write_charge_limit(self._device_power_limit_w)

    async def _read_float(self, name: str) -> float | None:
        reg = REGISTER_BY_NAME.get(name)
        if not reg:
            return None
        try:
            val = float(await self._coord.client.read_register(reg))
            if math.isnan(val) or math.isinf(val):
                return None
            return val
        except (ModbusClientError, OSError, asyncio.TimeoutError, TypeError, ValueError):
            return None

    async def _write_charge_limit(self, watts: float) -> None:
        reg = REGISTER_BY_NAME.get("bat_max_charge_limit")
        if reg:
            try:
                await self._coord.async_write_register(reg, watts)
            except (ModbusClientError, OSError, asyncio.TimeoutError) as err:
                _LOGGER.warning("Grid limiter write failed: %s", err)

    async def async_will_remove_from_hass(self) -> None:
        self._cancel_control()
        if self._is_on:
            await self._write_charge_limit(self._device_power_limit_w)
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
