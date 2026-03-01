"""MQTT proxy bridge for Kostal Plenticore Modbus data.

Acts as the SINGLE Modbus-TCP gateway to the inverter. External systems
(evcc, iobroker, Node-RED, etc.) consume inverter data and send control
commands exclusively through MQTT -- they never touch Modbus directly.

Architecture:
    Inverter <--Modbus TCP (exclusive)--> This Bridge <--MQTT--> evcc
                                                              iobroker
                                                              Node-RED
                                                              any MQTT client

Traffic flow control:
    - Rate limiting: max 1 write command per register per second
    - Command queue: serialized writes prevent concurrent Modbus access
    - Source tracking: every command is logged with source identification
    - Admin protection: modbus_enable, unit_id, byte_order are read-only via MQTT

Topic structure:
    {prefix}/{id}/modbus/state                    → full JSON snapshot (5s)
    {prefix}/{id}/modbus/register/{name}          → individual register value
    {prefix}/{id}/modbus/command/{name}            → write command (inbound)
    {prefix}/{id}/modbus/available                 → online/offline (LWT)
    {prefix}/{id}/modbus/config                    → register metadata (retained)

    Simplified proxy topics for evcc/iobroker:
    {prefix}/{id}/proxy/pv_power                   → total PV power (W)
    {prefix}/{id}/proxy/grid_power                 → grid power (W, +import/-export)
    {prefix}/{id}/proxy/battery_power              → battery power (W, +discharge/-charge)
    {prefix}/{id}/proxy/battery_soc                → battery SoC (%)
    {prefix}/{id}/proxy/home_power                 → total home consumption (W)
    {prefix}/{id}/proxy/inverter_state             → inverter state (text)
    {prefix}/{id}/proxy/command/battery_charge     → set battery charge power (W)
    {prefix}/{id}/proxy/command/battery_min_soc    → set min SoC (%)
    {prefix}/{id}/proxy/command/battery_max_soc    → set max SoC (%)
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from typing import Any, Final

from homeassistant.core import HomeAssistant, callback

from .modbus_coordinator import ModbusDataUpdateCoordinator
from .modbus_registers import (
    INVERTER_STATES,
    ModbusRegister,
    REGISTER_BY_NAME,
    WRITABLE_REGISTERS,
    Access,
    REG_BAT_CHARGE_DC_ABS_POWER,
    REG_BAT_MIN_SOC,
    REG_BAT_MAX_SOC,
)

_LOGGER: Final = logging.getLogger(__name__)

_MQTT_EXCLUDED_NAMES: Final[frozenset[str]] = frozenset({
    "modbus_enable", "unit_id", "byte_order",
})

SAFE_WRITABLE_REGISTERS: Final[tuple[ModbusRegister, ...]] = tuple(
    r for r in WRITABLE_REGISTERS if r.name not in _MQTT_EXCLUDED_NAMES
)

TOPIC_PREFIX: Final[str] = "kostal_plenticore"
QOS: Final[int] = 1

RATE_LIMIT_SECONDS: Final[float] = 1.0

PROXY_COMMAND_MAP: Final[dict[str, ModbusRegister]] = {
    "battery_charge": REG_BAT_CHARGE_DC_ABS_POWER,
    "battery_min_soc": REG_BAT_MIN_SOC,
    "battery_max_soc": REG_BAT_MAX_SOC,
}


def _has_mqtt(hass: HomeAssistant) -> bool:
    """Check whether the MQTT integration is loaded."""
    return "mqtt" in hass.config.components


class KostalMqttBridge:
    """MQTT proxy bridge -- the single gateway between inverter and external systems.

    Provides traffic flow control via rate limiting and command serialization.
    Publishes simplified proxy topics for easy evcc/iobroker integration.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: ModbusDataUpdateCoordinator,
        device_id: str,
        soc_controller: Any = None,
    ) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._soc_controller = soc_controller
        self._device_id = device_id
        self._topic_base = f"{TOPIC_PREFIX}/{device_id}/modbus"
        self._proxy_base = f"{TOPIC_PREFIX}/{device_id}/proxy"
        self._unsub_command: list[Any] = []
        self._started = False
        self._last_write: dict[str, float] = {}
        self._write_lock = asyncio.Lock()

    @property
    def topic_base(self) -> str:
        return self._topic_base

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_start(self) -> None:
        """Start publishing data and subscribing to commands."""
        if not _has_mqtt(self._hass):
            _LOGGER.warning(
                "MQTT integration not available – Modbus MQTT bridge disabled. "
                "Set up MQTT in HA to share inverter data with evcc/iobroker."
            )
            return

        from homeassistant.components import mqtt  # noqa: E402

        await mqtt.async_publish(  # type: ignore[attr-defined]
            self._hass, f"{self._topic_base}/available", "online", QOS, retain=True,
        )

        for reg in SAFE_WRITABLE_REGISTERS:
            topic = f"{self._topic_base}/command/{reg.name}"
            unsub = await mqtt.async_subscribe(  # type: ignore[attr-defined]
                self._hass, topic, self._handle_command, QOS,
            )
            self._unsub_command.append(unsub)

        for proxy_name in PROXY_COMMAND_MAP:
            topic = f"{self._proxy_base}/command/{proxy_name}"
            unsub = await mqtt.async_subscribe(  # type: ignore[attr-defined]
                self._hass, topic, self._handle_proxy_command, QOS,
            )
            self._unsub_command.append(unsub)

        self._coordinator.async_add_listener(self._on_coordinator_update)
        self._started = True

        await self._publish_register_metadata()

        _LOGGER.info(
            "MQTT proxy bridge started – publishing to %s/# and %s/#",
            self._topic_base, self._proxy_base,
        )

    async def async_stop(self) -> None:
        """Stop the bridge and publish offline status."""
        if not self._started:
            return

        for unsub in self._unsub_command:
            unsub()
        self._unsub_command.clear()

        if _has_mqtt(self._hass):
            from homeassistant.components import mqtt

            await mqtt.async_publish(  # type: ignore[attr-defined]
                self._hass, f"{self._topic_base}/available", "offline", QOS, retain=True,
            )

        self._started = False
        _LOGGER.debug("MQTT proxy bridge stopped")

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    @callback
    def _on_coordinator_update(self) -> None:
        """Called by the coordinator after each poll cycle."""
        if not self._started:
            return
        data = self._coordinator.data
        if data is None:
            return
        self._hass.async_create_task(self._publish_data(data))

    async def _publish_data(self, data: dict[str, Any]) -> None:
        """Publish register values and simplified proxy topics."""
        if not _has_mqtt(self._hass):
            return

        from homeassistant.components import mqtt

        safe: dict[str, Any] = {}
        for key, val in data.items():
            try:
                json.dumps(val)
                safe[key] = val
            except (TypeError, ValueError):
                safe[key] = str(val)

        await mqtt.async_publish(  # type: ignore[attr-defined]
            self._hass, f"{self._topic_base}/state",
            json.dumps(safe, default=str), QOS, retain=True,
        )

        for key, val in safe.items():
            payload = json.dumps(val, default=str) if not isinstance(val, str) else val
            await mqtt.async_publish(  # type: ignore[attr-defined]
                self._hass, f"{self._topic_base}/register/{key}", payload, QOS, retain=True,
            )

        await self._publish_proxy_topics(safe)

    async def _publish_proxy_topics(self, data: dict[str, Any]) -> None:
        """Publish simplified proxy topics for evcc/iobroker."""
        if not _has_mqtt(self._hass):
            return

        from homeassistant.components import mqtt

        proxy_map: dict[str, str | None] = {
            "pv_power": self._fmt(data.get("total_dc_power")),
            "grid_power": self._fmt(data.get("pm_total_active")),
            "battery_power": self._fmt(data.get("battery_cd_power")),
            "battery_soc": self._fmt(data.get("battery_soc")),
            "home_power": self._fmt(data.get("home_from_pv", data.get("total_ac_power"))),
        }

        state_raw = data.get("inverter_state")
        if state_raw is not None:
            try:
                proxy_map["inverter_state"] = INVERTER_STATES.get(int(state_raw), str(state_raw))
            except (TypeError, ValueError):
                proxy_map["inverter_state"] = str(state_raw)

        for name, val in proxy_map.items():
            if val is not None:
                await mqtt.async_publish(  # type: ignore[attr-defined]
                    self._hass, f"{self._proxy_base}/{name}", val, QOS, retain=True,
                )

    @staticmethod
    def _fmt(val: Any) -> str | None:
        if val is None:
            return None
        try:
            return str(round(float(val), 1))
        except (TypeError, ValueError):
            return str(val)

    async def _publish_register_metadata(self) -> None:
        """Publish metadata for register discovery and proxy topic docs."""
        if not _has_mqtt(self._hass):
            return

        from homeassistant.components import mqtt

        reg_meta: list[dict[str, Any]] = []
        for reg in SAFE_WRITABLE_REGISTERS:
            reg_meta.append({
                "name": reg.name,
                "address": reg.address,
                "description": reg.description,
                "unit": reg.unit,
                "data_type": reg.data_type.value,
                "command_topic": f"{self._topic_base}/command/{reg.name}",
            })

        proxy_meta: dict[str, str] = {}
        for name, reg in PROXY_COMMAND_MAP.items():
            proxy_meta[name] = f"{self._proxy_base}/command/{name}"

        await mqtt.async_publish(  # type: ignore[attr-defined]
            self._hass, f"{self._topic_base}/config",
            json.dumps({
                "device_id": self._device_id,
                "writable_registers": reg_meta,
                "proxy_commands": proxy_meta,
                "proxy_topics": {
                    "pv_power": f"{self._proxy_base}/pv_power",
                    "grid_power": f"{self._proxy_base}/grid_power",
                    "battery_power": f"{self._proxy_base}/battery_power",
                    "battery_soc": f"{self._proxy_base}/battery_soc",
                    "home_power": f"{self._proxy_base}/home_power",
                    "inverter_state": f"{self._proxy_base}/inverter_state",
                },
                "state_topic": f"{self._topic_base}/state",
                "available_topic": f"{self._topic_base}/available",
            }),
            QOS, retain=True,
        )

    # ------------------------------------------------------------------
    # Command handling with rate limiting + source tracking
    # ------------------------------------------------------------------

    def _check_rate_limit(self, reg_name: str) -> bool:
        """Return True if the write is allowed, False if rate-limited."""
        now = time.monotonic()
        last = self._last_write.get(reg_name, 0.0)
        if now - last < RATE_LIMIT_SECONDS:
            _LOGGER.debug(
                "Rate-limited MQTT write to %s (%.1fs since last write)",
                reg_name, now - last,
            )
            return False
        self._last_write[reg_name] = now
        return True

    async def _handle_command(self, msg: Any) -> None:
        """Process an inbound MQTT command to write a register value."""
        topic: str = msg.topic
        payload: str = msg.payload

        parts = topic.split("/")
        if len(parts) < 2:
            _LOGGER.warning("Malformed command topic: %s", topic)
            return
        reg_name = parts[-1]

        reg = REGISTER_BY_NAME.get(reg_name)
        if reg is None:
            _LOGGER.warning("Unknown register in command: %s", reg_name)
            return

        if reg.access != Access.RW:
            _LOGGER.warning("Rejected write to read-only register %s via MQTT", reg_name)
            return

        if reg.name in _MQTT_EXCLUDED_NAMES:
            _LOGGER.warning("Rejected write to protected register %s via MQTT", reg_name)
            return

        await self._execute_write(reg, payload, source=f"mqtt/command/{reg_name}")

    async def _handle_proxy_command(self, msg: Any) -> None:
        """Process a simplified proxy command (e.g. from evcc)."""
        topic: str = msg.topic
        payload: str = msg.payload

        parts = topic.split("/")
        if len(parts) < 2:
            return
        proxy_name = parts[-1]

        reg = PROXY_COMMAND_MAP.get(proxy_name)
        if reg is None:
            _LOGGER.warning("Unknown proxy command: %s", proxy_name)
            return

        await self._execute_write(reg, payload, source=f"proxy/{proxy_name}")

    # Battery control register names for SoC controller arbitration
    _BATTERY_REG_NAMES: Final = frozenset({
        "bat_charge_dc_abs_power", "bat_max_charge_limit", "bat_max_discharge_limit",
        "bat_min_soc", "bat_max_soc", "bat_charge_ac_abs",
        "g3_max_charge", "g3_max_discharge",
    })

    async def _execute_write(self, reg: ModbusRegister, payload: str, source: str) -> None:
        """Validate, rate-limit, arbitrate, and execute a register write."""
        if not self._check_rate_limit(reg.name):
            return

        # SoC controller arbitration for battery registers
        if reg.name in self._BATTERY_REG_NAMES:
            ctrl = self._soc_controller
            if ctrl is not None and getattr(ctrl, "active", False):
                _LOGGER.warning(
                    "MQTT command REJECTED: %s (SoC Controller active, target=%.0f%%). "
                    "Stop the SoC Controller first. (source: %s)",
                    reg.name, ctrl.target_soc or 0, source,
                )
                return

        try:
            value: Any
            try:
                value = json.loads(payload)
            except (json.JSONDecodeError, ValueError):
                value = payload

            if isinstance(value, str):
                try:
                    value = float(value)
                except ValueError:
                    _LOGGER.warning(
                        "MQTT command rejected: non-numeric value %r for %s (source: %s)",
                        payload, reg.name, source,
                    )
                    return

            if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
                _LOGGER.warning(
                    "MQTT command rejected: NaN/Infinity for %s (source: %s)",
                    reg.name, source,
                )
                return

            async with self._write_lock:
                await self._coordinator.async_write_register(reg, value)

            _LOGGER.info(
                "MQTT command executed: %s = %s (source: %s)",
                reg.name, value, source,
            )

            await self._coordinator.async_request_refresh()

        except Exception as err:
            _LOGGER.error(
                "MQTT command failed for %s: %s (source: %s)",
                reg.name, err, source,
            )
