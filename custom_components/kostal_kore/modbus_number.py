"""Modbus-backed number entities for battery charge/discharge control.

Safety design:
- Power limits are clamped to the inverter's actual max power (register 531),
  NOT hardcoded. A 5.5kW inverter gets 5500W limits, a 20kW gets 20000W.
- Battery management mode (register 1080) is checked before creating write
  entities. If external Modbus control is not enabled on the inverter, the
  entities are created as read-only with a warning.
- Every write is validated: range, NaN/Inf, and type checks at the entity
  level before the value reaches the Modbus client.
- G3 cyclic limit registers (1280/1282) include a keepalive task that
  re-writes the value periodically to prevent fallback activation.
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any, Final

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfPower
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .modbus_client import ModbusClientError  # noqa: F401
from .modbus_coordinator import ModbusDataUpdateCoordinator
from .modbus_registers import (
    REG_ACTIVE_POWER_SETPOINT,
    REG_BAT_CHARGE_DC_ABS_POWER,
    REG_BAT_MAX_CHARGE_LIMIT,
    REG_BAT_MAX_DISCHARGE_LIMIT,
    REG_BAT_MAX_SOC,
    REG_BAT_MIN_SOC,
    REG_BATTERY_MGMT_MODE,
    REG_G3_FALLBACK_TIME,
    REG_G3_MAX_CHARGE,
    REG_G3_MAX_DISCHARGE,
    REG_INVERTER_MAX_POWER,
    ModbusRegister,
)

_LOGGER: Final = logging.getLogger(__name__)

FALLBACK_MAX_POWER: Final[int] = 20000
G3_KEEPALIVE_DIVISOR: Final[float] = 2.0
G3_KEEPALIVE_MIN_SECONDS: Final[int] = 15
G3_KEEPALIVE_MAX_SECONDS: Final[int] = 300
G3_DEFAULT_FALLBACK_SECONDS: Final[int] = 60

G3_CYCLIC_REGISTERS: Final[frozenset[str]] = frozenset({
    REG_G3_MAX_CHARGE.name,
    REG_G3_MAX_DISCHARGE.name,
})

# Register 533 is the inverter's active-power OUTPUT limit in percent
# (Wirkleistungsbegrenzung / feed-in curtailment) — the Kostal-native
# equivalent of SunSpec model 123 WMaxLimPct. It gets curtailment-specific
# state attributes (see ModbusNumberEntity.extra_state_attributes).
CURTAILMENT_REGISTER_NAME: Final[str] = REG_ACTIVE_POWER_SETPOINT.name


async def _probe_modbus_access(coordinator: ModbusDataUpdateCoordinator) -> bool:
    """Probe if battery management registers are accessible (read-only).

    Reads Min SoC (register 1042) to verify the inverter exposes battery
    management registers. Does NOT attempt to write -- the inverter may
    need specific activation sequences that vary by firmware version.
    """
    try:
        current_soc = await coordinator.client.read_register(REG_BAT_MIN_SOC)
        soc_value = float(current_soc)
        _LOGGER.info(
            "Modbus battery registers accessible (Min SoC = %.0f%%). "
            "Write access depends on inverter WebUI setting "
            "'Extern über Protokoll (Modbus TCP)'.",
            soc_value,
        )
        return True
    except Exception as err:
        _LOGGER.debug("Battery management registers not readable: %s", err)
        return False


def _build_descriptions(
    max_power: int,
) -> list[dict[str, Any]]:
    """Build number entity descriptions with dynamic power limits."""
    return [
        {
            "register": REG_BAT_CHARGE_DC_ABS_POWER,
            "name": "Battery Charge Power (Modbus)",
            "icon": "mdi:battery-charging",
            "min_value": -max_power,
            "max_value": max_power,
            "step": 100,
            "unit": UnitOfPower.WATT,
            "device_class": NumberDeviceClass.POWER,
            "entity_category": EntityCategory.CONFIG,
        },
        {
            "register": REG_BAT_MAX_CHARGE_LIMIT,
            "name": "Battery Max Charge Limit (Modbus)",
            "icon": "mdi:battery-charging-high",
            "min_value": 0,
            "max_value": max_power,
            "step": 100,
            "unit": UnitOfPower.WATT,
            "device_class": NumberDeviceClass.POWER,
            "entity_category": EntityCategory.CONFIG,
        },
        {
            "register": REG_BAT_MAX_DISCHARGE_LIMIT,
            "name": "Battery Max Discharge Limit (Modbus)",
            "icon": "mdi:battery-arrow-down",
            "min_value": 0,
            "max_value": max_power,
            "step": 100,
            "unit": UnitOfPower.WATT,
            "device_class": NumberDeviceClass.POWER,
            "entity_category": EntityCategory.CONFIG,
        },
        {
            "register": REG_BAT_MIN_SOC,
            "name": "Battery Min SoC (Modbus)",
            "icon": "mdi:battery-low",
            "min_value": 5,
            "max_value": 100,
            "step": 1,
            "unit": "%",
            "device_class": None,
            "entity_category": EntityCategory.CONFIG,
        },
        {
            "register": REG_BAT_MAX_SOC,
            "name": "Battery Max SoC (Modbus)",
            "icon": "mdi:battery-high",
            "min_value": 5,
            "max_value": 100,
            "step": 1,
            "unit": "%",
            "device_class": None,
            "entity_category": EntityCategory.CONFIG,
        },
        {
            "register": REG_ACTIVE_POWER_SETPOINT,
            "name": "Active Power Setpoint (Modbus)",
            # Inverter active-power OUTPUT limit in % (Wirkleistungsbegrenzung /
            # feed-in curtailment); 100 % = uncurtailed. See the entity's
            # extra_state_attributes for the curtailment semantics.
            "icon": "mdi:transmission-tower-export",
            "min_value": 1,
            "max_value": 100,
            "step": 1,
            "unit": "%",
            "device_class": None,
            "entity_category": EntityCategory.CONFIG,
        },
        {
            "register": REG_G3_MAX_CHARGE,
            "name": "G3 Max Battery Charge Power (Modbus)",
            "icon": "mdi:battery-charging-100",
            "min_value": 0,
            "max_value": max_power,
            "step": 100,
            "unit": UnitOfPower.WATT,
            "device_class": NumberDeviceClass.POWER,
            "entity_category": EntityCategory.CONFIG,
        },
        {
            "register": REG_G3_MAX_DISCHARGE,
            "name": "G3 Max Battery Discharge Power (Modbus)",
            "icon": "mdi:battery-arrow-down-outline",
            "min_value": 0,
            "max_value": max_power,
            "step": 100,
            "unit": UnitOfPower.WATT,
            "device_class": NumberDeviceClass.POWER,
            "entity_category": EntityCategory.CONFIG,
        },
    ]


async def create_modbus_number_entities(
    coordinator: ModbusDataUpdateCoordinator,
    entry_id: str,
    device_info: DeviceInfo,
) -> list[ModbusNumberEntity]:
    """Create Modbus-backed number entities for battery control.

    Reads the inverter's max power from register 531 to set safe limits.
    Checks battery management mode register 1080 to verify Modbus control
    is enabled on the inverter.
    """
    device_data = coordinator.device_info_data

    max_power = FALLBACK_MAX_POWER
    raw_max = device_data.get(REG_INVERTER_MAX_POWER.name)
    if raw_max is not None:
        try:
            max_power = int(raw_max)
            _LOGGER.info("Inverter max power: %d W (from register 531)", max_power)
        except (TypeError, ValueError):
            _LOGGER.warning("Invalid max power value %r, using fallback %d W", raw_max, FALLBACK_MAX_POWER)

    from .notifications import notify_modbus_probe_failed, notify_modbus_probe_success

    read_only = False
    bat_mgmt_mode = device_data.get(REG_BATTERY_MGMT_MODE.name)
    if bat_mgmt_mode is not None:
        try:
            mode_int = int(float(bat_mgmt_mode))
        except (TypeError, ValueError):
            _LOGGER.warning(
                "Invalid battery management mode value %r, treating as read-only",
                bat_mgmt_mode,
            )
            mode_int = -1
            read_only = True

        if mode_int == 0x02:
            _LOGGER.info("Battery management mode: External via MODBUS (confirmed active)")
            await notify_modbus_probe_success(coordinator.hass)
        elif mode_int == 0x00:
            registers_ok = await _probe_modbus_access(coordinator)
            if registers_ok:
                await notify_modbus_probe_success(coordinator.hass)
            else:
                read_only = True
                await notify_modbus_probe_failed(coordinator.hass)
        else:
            _LOGGER.warning(
                "Battery management mode is %s (not 'External via MODBUS'), "
                "number entities will be read-only",
                bat_mgmt_mode,
            )
            read_only = True
            await _probe_modbus_access(coordinator)

    descriptions = _build_descriptions(max_power)
    entities: list[ModbusNumberEntity] = []
    for desc in descriptions:
        register: ModbusRegister = desc["register"]
        entities.append(
            ModbusNumberEntity(
                coordinator=coordinator,
                register=register,
                name=str(desc["name"]),
                icon=str(desc["icon"]),
                min_value=float(desc["min_value"]),
                max_value=float(desc["max_value"]),
                step=float(desc["step"]),
                unit=str(desc["unit"]),
                device_class=desc.get("device_class"),
                entity_category=desc.get("entity_category"),
                entry_id=entry_id,
                device_info=device_info,
                read_only=read_only,
            )
        )
    return entities


class ModbusNumberEntity(
    CoordinatorEntity[ModbusDataUpdateCoordinator], NumberEntity
):
    """A number entity backed by a Kostal Modbus register.

    For G3 cyclic registers (1280/1282), a keepalive task automatically
    re-writes the current value to prevent fallback activation.
    """

    def __init__(
        self,
        coordinator: ModbusDataUpdateCoordinator,
        register: ModbusRegister,
        name: str,
        icon: str,
        min_value: float,
        max_value: float,
        step: float,
        unit: str,
        device_class: NumberDeviceClass | None,
        entity_category: EntityCategory | None,
        entry_id: str,
        device_info: Any,
        read_only: bool = False,
    ) -> None:
        super().__init__(coordinator)
        self._register = register
        self._read_only = read_only
        self._attr_name = name
        self._attr_icon = icon
        self._attr_native_min_value = min_value
        self._attr_native_max_value = max_value
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_entity_category = entity_category
        self._attr_mode = NumberMode.BOX
        self._attr_has_entity_name = True
        self._attr_unique_id = f"{entry_id}_modbus_{register.name}"
        self._attr_device_info = device_info
        self._keepalive_task: asyncio.Task[None] | None = None
        self._keepalive_value: float | None = None

    @property
    def available(self) -> bool:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return if entity is available."""
        return self.coordinator.data is not None and super().available

    @property
    def native_value(self) -> float | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the current value from Modbus data.

        The active-power setpoint (533) is write-only on the inverter (Kostal
        Modbus/SunSpec spec §3.3) and is not in ``MONITORING_REGISTERS``, so it
        never appears in ``coordinator.data``. For that register we fall back to
        the coordinator's last-commanded cache (populated by every write path
        and cleared on reconnect) so the entity and its curtailment attributes
        reflect the active setpoint instead of always reading unknown.
        """
        data = self.coordinator.data
        if data is not None:
            val = data.get(self._register.name)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    return None
        if self._register.name == CURTAILMENT_REGISTER_NAME:
            return self.coordinator.last_commanded(self._register.name)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Expose feed-in-curtailment semantics for the active-power setpoint.

        Register 533 is the inverter's active-power OUTPUT limit in percent
        (Wirkleistungsbegrenzung): 100 % = uncurtailed / full feed-in, < 100 %
        throttles AC output. It is the Kostal-native equivalent of SunSpec
        model 123 WMaxLimPct. The value is *volatile* — the inverter discards
        it on power-on/reset and returns to full power (fail-open), so no
        keepalive/watchdog is needed. True 0 % (zero export) is NOT reachable
        here (the floor is 1 %); use the REST ``ActivePower:ExtCtrlP:P`` = 0
        setting or a watt-based cap for that.

        The register is write-only, so the flags reflect the last value KORE
        commanded via any write path (``last_commanded_percent``); they read as
        not-curtailed/not-full until the setpoint is written once, after a
        restart, and after a reconnect (a volatile setpoint is not trusted
        across an inverter reset).
        """
        if self._register.name != CURTAILMENT_REGISTER_NAME:
            return None
        val = self.native_value
        return {
            "role": "feed_in_curtailment",
            "curtailment_active": val is not None and val < 100,
            "at_full_power": val is not None and val >= 100,
            "last_commanded_percent": self.coordinator.last_commanded(
                self._register.name
            ),
            "minimum_percent": self._attr_native_min_value,
            "zero_export_via_this_entity": False,
            "volatile_resets_to_full_power": True,
        }

    async def async_set_native_value(self, value: float) -> None:
        """Write a new value to the Modbus register with safety validation."""
        if self._read_only:
            _LOGGER.warning(
                "Write to %s blocked: inverter is not in 'External via MODBUS' mode",
                self._register.name,
            )
            return
        if math.isnan(value) or math.isinf(value):
            _LOGGER.error(
                "Refusing to write NaN/Infinity to %s", self._register.name
            )
            return
        if not (self._attr_native_min_value <= value <= self._attr_native_max_value):
            _LOGGER.error(
                "Value %s out of range [%s, %s] for %s",
                value,
                self._attr_native_min_value,
                self._attr_native_max_value,
                self._register.name,
            )
            return

        _LOGGER.info(
            "Writing %s = %s via Modbus (register %d)",
            self._register.name, value, self._register.address,
        )
        # The coordinator records the commanded value for every write path
        # (see async_write_register); write-only registers like the curtailment
        # setpoint read it back through native_value / the attributes.
        await self.coordinator.async_write_register(self._register, value)

        try:
            readback = await self.coordinator.client.read_register(self._register)
            if abs(float(readback) - value) > self._attr_native_step:
                _LOGGER.warning(
                    "Read-back mismatch for %s: wrote %s, read %s",
                    self._register.name, value, readback,
                )
        except Exception:
            _LOGGER.debug("Read-back verification skipped for %s", self._register.name)

        await self.coordinator.async_request_refresh()

        if self._register.name in G3_CYCLIC_REGISTERS:
            self._start_keepalive(value)

    def _start_keepalive(self, value: float) -> None:
        """Start or update the keepalive task for G3 cyclic registers."""
        self._keepalive_value = value
        if self._keepalive_task and not self._keepalive_task.done():
            return
        self._keepalive_task = self.hass.async_create_task(
            self._run_keepalive(),
            f"kostal_kore_keepalive_{self._register.name}",
        )

    async def _run_keepalive(self) -> None:
        """Re-write G3 limit values cyclically to prevent fallback."""
        try:
            while self._keepalive_value is not None:
                try:
                    interval = self._get_keepalive_interval()
                except (TypeError, ValueError, ZeroDivisionError):
                    interval = G3_DEFAULT_FALLBACK_SECONDS // int(G3_KEEPALIVE_DIVISOR)
                await asyncio.sleep(interval)
                if self._keepalive_value is None:
                    break
                try:
                    await self.coordinator.async_write_register(
                        self._register, self._keepalive_value
                    )
                    _LOGGER.debug(
                        "G3 keepalive: re-wrote %s = %s",
                        self._register.name, self._keepalive_value,
                    )
                except Exception as err:
                    _LOGGER.warning(
                        "G3 keepalive write failed for %s: %s",
                        self._register.name, err,
                    )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            _LOGGER.error(
                "G3 keepalive crashed for %s: %s — inverter may revert to fallback limits",
                self._register.name,
                exc,
            )

    def _get_keepalive_interval(self) -> int:
        """Calculate keepalive interval from the fallback time register."""
        fallback_seconds = G3_DEFAULT_FALLBACK_SECONDS
        if self.coordinator.data:
            raw = self.coordinator.data.get(REG_G3_FALLBACK_TIME.name)
            if raw is not None:
                try:
                    fallback_seconds = int(float(raw))
                except (TypeError, ValueError):
                    pass
        interval = int(max(G3_KEEPALIVE_MIN_SECONDS, fallback_seconds / G3_KEEPALIVE_DIVISOR))
        return min(interval, G3_KEEPALIVE_MAX_SECONDS)

    def _cancel_keepalive(self) -> None:
        """Cancel the keepalive task."""
        self._keepalive_value = None
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
        self._keepalive_task = None

    async def async_will_remove_from_hass(self) -> None:
        """Cancel keepalive on entity removal."""
        self._cancel_keepalive()
        await super().async_will_remove_from_hass()
