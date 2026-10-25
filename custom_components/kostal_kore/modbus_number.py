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
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .modbus_client import (  # noqa: F401
    READBACK_ABOVE_COMMAND,
    READBACK_BELOW_COMMAND,
    READBACK_UNREADABLE,
    ModbusClientError,
    ReadbackUnconfirmedError,
)
from .modbus_coordinator import SLOW_GROUPS, ModbusDataUpdateCoordinator
from .modbus_registers import (
    CONTROL_READBACK_REGISTERS,
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
from .notifications import clear_control_lease_notice, notify_control_lease_lost

_LOGGER: Final = logging.getLogger(__name__)

FALLBACK_MAX_POWER: Final[int] = 20000
G3_KEEPALIVE_DIVISOR: Final[float] = 2.0
G3_KEEPALIVE_MIN_SECONDS: Final[int] = 15
G3_KEEPALIVE_MAX_SECONDS: Final[int] = 300
G3_DEFAULT_FALLBACK_SECONDS: Final[int] = 60
READBACK_ATTEMPTS: Final[int] = 3
READBACK_RETRY_SECONDS: Final[float] = 0.2
# Power limits (W) read back slightly off the written value: the inverter keeps
# them as a battery current and reports current x present voltage (1000 W reads
# 997.9-999.9 W on a G3 and drifts with the battery voltage). Accept that drift,
# but stay below half an input step so an ignored one-step command still fails.
READBACK_POWER_REL_TOL: Final[float] = 0.005
READBACK_POWER_MIN_ABS_TOL_W: Final[float] = 5.0

# Outcome of the last command written through the entity (state attribute).
COMMAND_CONFIRMED: Final[str] = "confirmed"
COMMAND_LIMITED: Final[str] = "limited"
COMMAND_INVALIDATED: Final[str] = "invalidated"
# Why a power limit may read back off the command (log hint). On a G3, REG
# 1038 reads the limit in effect: a forced charge (Battery Manual Charge)
# lifts it to the inverter maximum, the BMS lowers it near full charge.
_READBACK_HINTS: Final[dict[str, str]] = {
    READBACK_ABOVE_COMMAND: "reads above the command, e.g. lifted by a forced charge",
    READBACK_BELOW_COMMAND: "reads below the command, e.g. an effective BMS limit",
}
# REG 1038/1040 read min(command, BMS limit): the BMS lowers the charge limit
# as the battery fills and the discharge limit as it empties (HV8). A read
# below the command is therefore the command applied, limited by the BMS.
EFFECTIVE_LIMIT_REGISTERS: Final[frozenset[str]] = frozenset({
    REG_BAT_MAX_CHARGE_LIMIT.name,
    REG_BAT_MAX_DISCHARGE_LIMIT.name,
})

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
        self._english_name = name
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
        self._keepalive_generation: int | None = None
        self._keepalive_incidents = 0
        # (command revision, outcome, last read-back) of the last entity write.
        self._last_command: tuple[int, str, float | None] | None = None
        # (command revision, command still in effect) after a raised limit was
        # rejected as ignored, so a retry of it is compared with that command.
        self._ignored_baseline: tuple[int, float] | None = None

    @property
    def available(self) -> bool:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return if entity is available."""
        return (self.coordinator.data is not None and super().available
                and not (self._register.group in SLOW_GROUPS
                         and self.coordinator.slow_poll_stale is True))

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
            return self._command_attributes()
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

    def _command_attributes(self) -> dict[str, Any] | None:
        """Expose the last commanded value next to the value read back.

        The entity state is what the inverter reports, which for REG 1038 on
        a G3 is the limit in effect, not the command. The outcome and the
        read-back belong to the last write through this entity and are only
        shown while that write is still the last command on any path (same
        command revision, so a later write of the same value hides them).
        """
        commanded = self.coordinator.last_commanded(self._register.name)
        if commanded is None:
            return None
        attrs: dict[str, Any] = {"last_commanded_value": commanded}
        revision = self.coordinator.command_revision(self._register.name)
        if self._last_command is not None and self._last_command[0] == revision:
            attrs["last_command_outcome"] = self._last_command[1]
            attrs["last_command_readback"] = self._last_command[2]
        return attrs

    async def async_set_native_value(self, value: float) -> None:
        """Write a new value to the Modbus register with safety validation."""
        if self._read_only:
            _LOGGER.warning(
                "Write to %s blocked: inverter is not in 'External via MODBUS' mode",
                self._register.name,
            )
            raise ServiceValidationError("Inverter is not in external Modbus control mode")
        if math.isnan(value) or math.isinf(value):
            _LOGGER.error(
                "Refusing to write NaN/Infinity to %s", self._register.name
            )
            raise ServiceValidationError("Modbus number value must be finite")
        if not (self._attr_native_min_value <= value <= self._attr_native_max_value):
            _LOGGER.error(
                "Value %s out of range [%s, %s] for %s",
                value,
                self._attr_native_min_value,
                self._attr_native_max_value,
                self._register.name,
            )
            raise ServiceValidationError("Modbus number value is outside its allowed range")

        _LOGGER.info(
            "Writing %s = %s via Modbus (register %d)",
            self._register.name, value, self._register.address,
        )
        # The coordinator records the commanded value for every write path
        # (see async_write_register); write-only registers like the curtailment
        # setpoint read it back through native_value / the attributes.
        if self._register.name in G3_CYCLIC_REGISTERS:
            self._cancel_keepalive()
        previous = self.coordinator.last_commanded(self._register.name)
        # The coordinator also records the rejected write; a retry of it must
        # still be compared with the command in effect before it.
        if (self._ignored_baseline is not None
                and self._ignored_baseline[0] == self.coordinator.command_revision(self._register.name)):
            previous = self._ignored_baseline[1]
        await self.coordinator.async_write_register(self._register, value)
        write_generation = self.coordinator.client.connection_generation
        revision = self.coordinator.command_revision(self._register.name)

        verified = False
        limited = False
        read_failed = False
        observed: float | None = None
        if self._register.name != CURTAILMENT_REGISTER_NAME:
            for attempt in range(READBACK_ATTEMPTS):
                if attempt:
                    await asyncio.sleep(READBACK_RETRY_SECONDS)
                try:
                    readback = await self.coordinator.client.read_register(self._register)
                    observed = float(readback)
                    retained: set[int] = getattr(self.coordinator.client, "retained_quality_addresses", set())
                    if (self.coordinator.client.connection_generation != write_generation
                            or self._register.address in retained or not math.isfinite(observed)):
                        read_failed = True
                        break
                    # UI steps are input increments, not device precision. An
                    # ignored one-step command must not count as confirmation.
                    verified = self._readback_matches(observed, value)
                    if verified:
                        break
                    if attempt == READBACK_ATTEMPTS - 1 and self._readback_limited(observed, value, previous):
                        limited = True
                        _LOGGER.info(
                            "%s applied: wrote %s, the inverter currently allows %s "
                            "(limit in effect, e.g. BMS taper near full or empty)",
                            self._register.name, value, readback,
                        )
                    elif attempt == READBACK_ATTEMPTS - 1:
                        _LOGGER.warning(
                            "Read-back mismatch for %s: wrote %s, read %s (%s)",
                            self._register.name, value, readback,
                            _READBACK_HINTS[self._readback_outcome(observed, value)],
                        )
                except Exception:
                    _LOGGER.debug("Read-back verification failed for %s", self._register.name)
                    # The client already retries transient transport failures;
                    # do not multiply terminal failures or suppression strikes.
                    read_failed = True
                    break
            if read_failed:
                observed = None

        outcome = self._readback_outcome(observed, value)
        if self._register.name in G3_CYCLIC_REGISTERS and not verified:
            self._record_last_command(revision, outcome, observed)
            notify_control_lease_lost(getattr(self, "hass", None),
                                      getattr(self.coordinator, "_communication_entry_id", ""),
                                      control=f"modbus_{self._register.name}")
            raise ReadbackUnconfirmedError(
                "Cyclic limit readback is unconfirmed; keepalive was not started",
                register=self._register.name, commanded=value,
                observed=observed, outcome=outcome,
            )

        if not verified and not limited and self._register.name != CURTAILMENT_REGISTER_NAME:
            # A transport acknowledgement does not establish that the inverter
            # accepted the limit. Publish a device refresh, then fail the call so
            # controllers cannot treat an ignored command as an applied limit.
            # The outcome tells them whether the limit reads higher or lower.
            self._record_last_command(revision, outcome, observed)
            if (outcome == READBACK_BELOW_COMMAND and previous is not None
                    and self._register.name in EFFECTIVE_LIMIT_REGISTERS):
                self._ignored_baseline = (revision, previous)
            if not read_failed:
                await self.coordinator.async_request_refresh()
            raise ReadbackUnconfirmedError(
                f"Readback is unconfirmed for {self._register.name} "
                f"(wrote {value:g}, read {'n/a' if observed is None else f'{observed:g}'}: {outcome})",
                register=self._register.name, commanded=value,
                observed=observed, outcome=outcome,
            )

        # A debounced request may return before the actual poll. Monitored
        # controls need the completed poll and its per-register fresh cache.
        await self.coordinator.async_refresh()

        refresh_confirmed = True
        # The outcome below is decided on the completed poll when there is one,
        # so the attribute reports that value next to it.
        decided_readback = observed
        if self._register in CONTROL_READBACK_REGISTERS:
            try:
                newest_value = self.coordinator.proxy_cache_data.get(self._register.name)
                newest = float(newest_value) if newest_value is not None else math.nan
                matched = math.isfinite(newest) and self._readback_matches(newest, value)
                limited = not matched and self._readback_limited(newest, value, previous)
                refresh_confirmed = matched or limited
                decided_readback = newest
            except (TypeError, ValueError):
                refresh_confirmed = False
            retained = getattr(self.coordinator.client, "retained_quality_addresses", set())
            if self._register.address in retained:
                refresh_confirmed = False

        if (self.coordinator.client.connection_generation != write_generation
                or not self.coordinator.client.connected
                or not self.coordinator.last_update_success or not refresh_confirmed):
            self._record_last_command(revision, COMMAND_INVALIDATED, observed)
            if self._register.name in G3_CYCLIC_REGISTERS:
                notify_control_lease_lost(getattr(self, "hass", None),
                                          getattr(self.coordinator, "_communication_entry_id", ""),
                                          control=f"modbus_{self._register.name}")
            raise ModbusClientError("Control confirmation was invalidated during refresh")

        self._record_last_command(
            revision, COMMAND_LIMITED if limited else COMMAND_CONFIRMED, decided_readback)
        self._ignored_baseline = None
        if self._register.name in G3_CYCLIC_REGISTERS:
            self._start_keepalive(value)
            clear_control_lease_notice(getattr(self, "hass", None),
                                       getattr(self.coordinator, "_communication_entry_id", ""),
                                       control=f"modbus_{self._register.name}")

    def _record_last_command(self, revision: int, outcome: str, observed: float | None) -> None:
        """Remember a write's outcome unless a newer write already finished first."""
        if self._last_command is None or revision >= self._last_command[0]:
            self._last_command = (revision, outcome, observed)

    @staticmethod
    def _readback_outcome(observed: float | None, value: float) -> str:
        """Classify an unconfirmed read-back relative to the command."""
        if observed is None:
            return READBACK_UNREADABLE
        return READBACK_ABOVE_COMMAND if observed > value else READBACK_BELOW_COMMAND

    def _readback_limited(self, observed: float, value: float, previous: float | None) -> bool:
        """Return True if a read below the command is the BMS-limited command.

        A raised limit that still reads the previous command was ignored, not
        limited: the BMS limit does not happen to sit on the old command.
        """
        if self._register.name not in EFFECTIVE_LIMIT_REGISTERS or not 0 <= observed < value:
            return False
        return previous is None or previous >= value or not self._readback_matches(observed, previous)

    def _readback_matches(self, observed: float, value: float) -> bool:
        """Return True if a read-back confirms the commanded value."""
        # A zero read-back never confirms a nonzero limit, whatever the tolerance.
        if value == 0 or observed == 0:
            return observed == value
        abs_tol = 1e-6
        if self._register.unit == "W":
            abs_tol = max(READBACK_POWER_MIN_ABS_TOL_W, abs(value) * READBACK_POWER_REL_TOL)
            step = float(getattr(self, "_attr_native_step", 0) or 0)
            if step > 0:
                # A half-step miss never confirms; isclose() alone is inclusive.
                if abs(observed - value) >= step / 2:
                    return False
                abs_tol = min(abs_tol, step / 2)
        return math.isclose(observed, value, rel_tol=1e-6, abs_tol=abs_tol)

    def _start_keepalive(self, value: float) -> None:
        """Start or update the keepalive task for G3 cyclic registers."""
        self._keepalive_value = value
        self._keepalive_generation = self.coordinator.client.connection_generation
        self._keepalive_incidents = self.coordinator.communication.incidents
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
                if (not self.coordinator.client.connected
                        or self.coordinator.last_update_success is False
                        or self.coordinator.slow_poll_stale is True
                        or bool(self.coordinator.communication.faults)
                        or self.coordinator.communication.incidents != self._keepalive_incidents
                        or self.coordinator.client.connection_generation != self._keepalive_generation
                        or self.coordinator.last_commanded(self._register.name) != self._keepalive_value):
                    _LOGGER.warning("G3 keepalive lease lost for %s; explicit control is required before resuming",
                                    self._register.name)
                    self._keepalive_value = None
                    notify_control_lease_lost(getattr(self, "hass", None),
                                              getattr(self.coordinator, "_communication_entry_id", ""),
                                              control=f"modbus_{self._register.name}")
                    break
                try:
                    # Same command on the same connection (checked above): do
                    # not count it as a new one, the entity outcome still holds.
                    # A client retry must not restore this value once another
                    # command or a reconnect has superseded it.
                    revision = self.coordinator.command_revision(self._register.name)
                    await self.coordinator.async_write_register(
                        self._register, self._keepalive_value, record_command=False,
                        precondition=lambda: (
                            self.coordinator.client.connection_generation == self._keepalive_generation
                            and self.coordinator.command_revision(self._register.name) == revision
                        ),
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
                    self._keepalive_value = None
                    notify_control_lease_lost(getattr(self, "hass", None),
                                              getattr(self.coordinator, "_communication_entry_id", ""),
                                              control=f"modbus_{self._register.name}")
                    break
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
