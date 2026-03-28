"""Switch entity to block battery charging (PV → grid instead of battery).

When ON:  REG 1038 (bat_max_charge_limit) = 0  → no charging allowed
          PV power flows to house + grid, battery only discharges
When OFF: REG 1038 (bat_max_charge_limit) = inverter max power → normal operation
          PV charges battery as usual

Use case: Block charging in the morning to maximize grid feed-in during
high-tariff hours, then enable charging during midday solar peak when
grid curtailment would otherwise waste energy.

Includes a keepalive task that re-writes REG 1038 every 15s to prevent
the Kostal G3 deadman switch from reverting to default limits.
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any, Final

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo

from .modbus_client import ModbusClientError
from .modbus_coordinator import ModbusDataUpdateCoordinator
from .modbus_registers import REGISTER_BY_NAME
from .power_limits import get_device_power_limit_w

_LOGGER: Final = logging.getLogger(__name__)

KEEPALIVE_INTERVAL: Final[float] = 15.0


class BatteryChargeBlockSwitch(SwitchEntity):
    """Switch to block battery charging from PV/grid."""

    _attr_has_entity_name = True
    _attr_name = "Block Battery Charging"
    _attr_icon = "mdi:battery-off"
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
        self._normal_limit_w = get_device_power_limit_w(coordinator)
        self._attr_unique_id = f"{entry_id}_block_battery_charging"
        self._attr_device_info = device_info
        self._is_on = False
        self._keepalive_task: asyncio.Task[None] | None = None
        self._original_charge_limit: float | None = None

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "register": "1038 (bat_max_charge_limit)",
            "value_when_on": "0 W (charging blocked)",
            "value_when_off": f"{self._normal_limit_w:.0f} W (normal)",
            "keepalive_interval": f"{KEEPALIVE_INTERVAL}s",
        }

    async def _snapshot_charge_limit(self) -> None:
        """Read and store the current charge limit before overwriting."""
        if self._original_charge_limit is not None:
            return
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

    def _restore_limit(self) -> float:
        """Return the limit to restore: original snapshot or device max as fallback."""
        limit = self._original_charge_limit if self._original_charge_limit is not None else self._normal_limit_w
        self._original_charge_limit = None
        return limit

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Block battery charging: REG 1038 = 0."""
        await self._snapshot_charge_limit()
        await self._write_block()
        self._is_on = True
        self._start_keepalive()
        self.async_write_ha_state()
        _LOGGER.info("Battery charging BLOCKED (REG 1038 = 0)")

        if self._hass_ref:
            try:
                await self._hass_ref.services.async_call(
                    "persistent_notification", "create",
                    {"title": "🔋 Akku-Ladung blockiert",
                     "message": "PV-Strom fließt ins Netz statt in den Akku.\n"
                                "Switch ausschalten um Ladung wieder zu erlauben.",
                     "notification_id": f"kostal_charge_block_{self._entry_id}"},
                )
            except Exception:  # notification is non-critical, keep broad
                _LOGGER.debug("Failed to send charge block notification")

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Restore charging to previous limit."""
        self._cancel_keepalive()
        restore = self._restore_limit()
        await self._write_normal(restore)
        self._is_on = False
        self.async_write_ha_state()
        _LOGGER.info("Battery charging RESTORED (REG 1038 = %.0f)", restore)

        if self._hass_ref:
            try:
                await self._hass_ref.services.async_call(
                    "persistent_notification", "dismiss",
                    {"notification_id": f"kostal_charge_block_{self._entry_id}"},
                )
            except Exception:
                _LOGGER.debug("Failed to dismiss charge block notification")

    async def _write_block(self) -> None:
        reg = REGISTER_BY_NAME.get("bat_max_charge_limit")
        if reg:
            await self._coord.async_write_register(reg, 0.0)

    async def _write_normal(self, watts: float | None = None) -> None:
        limit = watts if watts is not None else self._normal_limit_w
        reg = REGISTER_BY_NAME.get("bat_max_charge_limit")
        if reg:
            try:
                await self._coord.async_write_register(reg, limit)
            except (ModbusClientError, OSError, asyncio.TimeoutError, ValueError) as err:
                _LOGGER.warning("Failed to restore charging: %s", err)

    def _start_keepalive(self) -> None:
        self._cancel_keepalive()
        self._keepalive_task = self.hass.async_create_task(
            self._run_keepalive(),
            "kostal_kore_charge_block_keepalive",
        )

    def _cancel_keepalive(self) -> None:
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
        self._keepalive_task = None

    async def _run_keepalive(self) -> None:
        """Re-write REG 1038 = 0 every 15s to prevent deadman reset."""
        try:
            while self._is_on:
                await asyncio.sleep(KEEPALIVE_INTERVAL)
                if not self._is_on:
                    break
                await self._write_block()
                _LOGGER.debug("Charge block keepalive: REG 1038 = 0")
        except asyncio.CancelledError:
            return

    async def async_will_remove_from_hass(self) -> None:
        """Restore charging on entity removal."""
        self._cancel_keepalive()
        if self._is_on:
            self._is_on = False
            await self._write_normal(self._restore_limit())
            if self._hass_ref:
                try:
                    await self._hass_ref.services.async_call(
                        "persistent_notification", "dismiss",
                        {"notification_id": f"kostal_charge_block_{self._entry_id}"},
                    )
                except Exception:
                    _LOGGER.debug("Failed to dismiss charge block notification on removal")
        await super().async_will_remove_from_hass()
