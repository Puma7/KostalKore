"""MQTT bridge for Kostal Plenticore Modbus data.

Publishes all Modbus register values to MQTT topics so that external
systems (evcc, iobroker, Node-RED, etc.) can consume inverter data
without needing their own Modbus connection.  Also subscribes to
command topics so external systems can write to inverter registers
through this bridge.

Topic structure:
    kostal_plenticore/{device_id}/modbus/state          → full JSON snapshot
    kostal_plenticore/{device_id}/modbus/register/{name} → individual value
    kostal_plenticore/{device_id}/modbus/command/{name}  → write (inbound)
    kostal_plenticore/{device_id}/modbus/available       → online/offline
"""

from __future__ import annotations

import json
import logging
from typing import Any, Final

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .modbus_coordinator import ModbusDataUpdateCoordinator
from .modbus_registers import (
    REGISTER_BY_NAME,
    WRITABLE_REGISTERS,
    Access,
)

_LOGGER: Final = logging.getLogger(__name__)

TOPIC_PREFIX: Final[str] = "kostal_plenticore"
QOS: Final[int] = 1


def _has_mqtt(hass: HomeAssistant) -> bool:
    """Check whether the MQTT integration is loaded."""
    return "mqtt" in hass.config.components


class KostalMqttBridge:
    """Bridge between the Modbus coordinator and an MQTT broker.

    Uses Home Assistant's built-in MQTT integration for publishing and
    subscribing – no extra MQTT dependency required.  If MQTT is not
    configured in HA the bridge silently does nothing.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: ModbusDataUpdateCoordinator,
        device_id: str,
    ) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._device_id = device_id
        self._topic_base = f"{TOPIC_PREFIX}/{device_id}/modbus"
        self._unsub_command: list[Any] = []
        self._started = False

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
            self._hass,
            f"{self._topic_base}/available",
            "online",
            QOS,
            retain=True,
        )

        writable_names = [r.name for r in WRITABLE_REGISTERS]
        for name in writable_names:
            topic = f"{self._topic_base}/command/{name}"
            unsub = await mqtt.async_subscribe(  # type: ignore[attr-defined]
                self._hass, topic, self._handle_command, QOS,
            )
            self._unsub_command.append(unsub)

        self._coordinator.async_add_listener(self._on_coordinator_update)
        self._started = True

        await self._publish_register_metadata()

        _LOGGER.info(
            "MQTT bridge started – publishing to %s/# "
            "(%d writable command topics)",
            self._topic_base,
            len(writable_names),
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
                self._hass,
                f"{self._topic_base}/available",
                "offline",
                QOS,
                retain=True,
            )

        self._started = False
        _LOGGER.debug("MQTT bridge stopped")

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
        """Publish the full state snapshot and individual register topics."""
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
            self._hass,
            f"{self._topic_base}/state",
            json.dumps(safe, default=str),
            QOS,
            retain=True,
        )

        for key, val in safe.items():
            await mqtt.async_publish(  # type: ignore[attr-defined]
                self._hass,
                f"{self._topic_base}/register/{key}",
                json.dumps(val, default=str) if not isinstance(val, str) else val,
                QOS,
                retain=True,
            )

    async def _publish_register_metadata(self) -> None:
        """Publish a config topic with register metadata for discovery."""
        if not _has_mqtt(self._hass):
            return

        from homeassistant.components import mqtt

        meta: list[dict[str, Any]] = []
        for reg in WRITABLE_REGISTERS:
            meta.append({
                "name": reg.name,
                "address": reg.address,
                "description": reg.description,
                "unit": reg.unit,
                "data_type": reg.data_type.value,
                "command_topic": f"{self._topic_base}/command/{reg.name}",
            })

        await mqtt.async_publish(  # type: ignore[attr-defined]
            self._hass,
            f"{self._topic_base}/config",
            json.dumps({
                "device_id": self._device_id,
                "writable_registers": meta,
                "state_topic": f"{self._topic_base}/state",
                "available_topic": f"{self._topic_base}/available",
            }),
            QOS,
            retain=True,
        )

    # ------------------------------------------------------------------
    # Command handling (inbound writes from external systems)
    # ------------------------------------------------------------------

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
            _LOGGER.warning(
                "Rejected write to read-only register %s via MQTT", reg_name
            )
            return

        try:
            value: Any
            try:
                value = json.loads(payload)
            except (json.JSONDecodeError, ValueError):
                value = payload

            await self._coordinator.async_write_register(reg, value)
            _LOGGER.info(
                "MQTT command executed: %s = %s (from external system)",
                reg_name,
                value,
            )

            await self._coordinator.async_request_refresh()

        except Exception as err:
            _LOGGER.error(
                "MQTT command failed for %s: %s", reg_name, err
            )
