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
    {prefix}/{id}/proxy/pv_power                   → LEGACY alias for pv_power_dc
    {prefix}/{id}/proxy/pv_power_dc                → Modbus total_dc_power (W, DC)
    {prefix}/{id}/proxy/pv_power_ac_est            → DC × efficiency estimate (W, AC)
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
from homeassistant.exceptions import HomeAssistantError

from .helper import (
    dc_pv_power_to_ac_estimate_w,
    optional_float,
    sum_home_consumption_power_w,
)
from .modbus_coordinator import ModbusDataUpdateCoordinator
from .modbus_registers import (
    INVERTER_STATES,
    REG_BAT_CHARGE_DC_ABS_POWER,
    REG_BAT_MAX_SOC,
    REG_BAT_MIN_SOC,
    REGISTER_BY_NAME,
    WRITABLE_REGISTERS,
    Access,
    ModbusRegister,
)

_LOGGER: Final = logging.getLogger(__name__)

_MQTT_EXCLUDED_NAMES: Final[frozenset[str]] = frozenset({
    "modbus_enable", "unit_id", "byte_order",
})

SAFE_WRITABLE_REGISTERS: Final[tuple[ModbusRegister, ...]] = tuple(
    r for r in WRITABLE_REGISTERS if r.name not in _MQTT_EXCLUDED_NAMES
)

TOPIC_PREFIX: Final[str] = "kostal_kore"
QOS: Final[int] = 1

# Self-healing start: if the MQTT integration is not loaded yet or the broker
# is not reachable when this integration sets up (e.g. MQTT finishes setting up
# after this one on a cold boot, or a CPU-loaded startup delays the broker),
# the bridge retries in the background instead of aborting the whole setup.
# The interval backs off to _RETRY_MAX_DELAY and then keeps probing at that
# pace indefinitely — the bridge starts whenever MQTT becomes available; only
# unloading the entry stops the loop.
_RETRY_INITIAL_DELAY: Final[float] = 5.0
_RETRY_MAX_DELAY: Final[float] = 300.0

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
        installer_access: bool = False,
    ) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._soc_controller = soc_controller
        self._installer_access = installer_access
        self._device_id = device_id
        self._topic_base = f"{TOPIC_PREFIX}/{device_id}/modbus"
        self._proxy_base = f"{TOPIC_PREFIX}/{device_id}/proxy"
        self._unsub_command: list[Any] = []
        self._unsub_coordinator: Any = None
        self._started = False
        self._last_write: dict[str, float] = {}
        self._write_lock = asyncio.Lock()
        self._publish_task: asyncio.Task[None] | None = None
        self._start_retry_task: asyncio.Task[None] | None = None
        self._start_attempted: bool = False
        self._command_count: int = 0
        self._rate_limited_count: int = 0

    @property
    def topic_base(self) -> str:
        return self._topic_base

    @property
    def started(self) -> bool:
        """Whether the bridge is fully started (commit point reached)."""
        return self._started

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_start(self) -> None:
        """Start the bridge, self-healing until MQTT becomes available.

        Neither blocker may abort the integration setup: the MQTT integration
        not being loaded yet (e.g. it sets up after this one on a cold boot)
        or the broker being unreachable. The first attempt runs immediately
        when possible; otherwise a background task retries with backoff until
        the bridge starts or the entry is unloaded.
        """
        if self._started:
            return
        if self._start_retry_task is not None and not self._start_retry_task.done():
            # A retry loop is already driving the start; don't race it with a
            # second concurrent _try_start over the shared subscription state.
            return

        if not _has_mqtt(self._hass):
            _LOGGER.warning(
                "MQTT integration not loaded yet – the Modbus MQTT bridge will "
                "start automatically once MQTT is available "
                "(waiting in background)."
            )
            self._schedule_start_retry()
            return

        if await self._try_start():
            return

        _LOGGER.warning(
            "MQTT broker not reachable yet – the Modbus MQTT bridge will start "
            "automatically once the broker is available (retrying in background)."
        )
        self._schedule_start_retry()

    async def _try_start(self) -> bool:
        """Attempt to bring the bridge up. Return True on success.

        Ordering matters: subscriptions and the retained register config are
        wired up first, and the retained ``available = online`` is published
        **last** as the commit point. Inbound commands are additionally gated
        on ``self._started`` (see ``_handle_command``), so nothing delivered
        into a half-started subscription window can write registers. On any
        error every partial step is rolled back and retained topics from the
        failed attempt are best-effort cleared, so a failed attempt never
        leaves a stale retained state or orphaned subscriptions.
        """
        from homeassistant.components import mqtt  # noqa: E402

        # Cheap fail-fast: HA queues subscriptions locally while the broker is
        # down (async_subscribe does not raise), so probing the connection
        # first avoids wiring all subscriptions per attempt only to roll them
        # back at the first publish.
        try:
            if not mqtt.is_connected(self._hass):  # type: ignore[attr-defined]
                _LOGGER.debug("MQTT broker not connected – skipping start attempt")
                return False
        except (KeyError, AttributeError):
            # mqtt loaded but client state not ready; let the attempt decide.
            pass

        self._start_attempted = True
        try:
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

            self._unsub_coordinator = self._coordinator.async_add_listener(
                self._on_coordinator_update
            )

            await self._publish_register_metadata()

            # Announce availability LAST, after everything is wired up, so a
            # failure in any step above never leaves a stale retained "online"
            # behind once the partial start is rolled back.
            await mqtt.async_publish(  # type: ignore[attr-defined]
                self._hass, f"{self._topic_base}/available", "online", QOS, retain=True,
            )
            self._started = True
        except asyncio.CancelledError:
            # Unload/cancel during a retry attempt: undo any partial wiring so no
            # stale subscriptions stay attached to a coordinator being torn down.
            self._rollback_partial_start()
            await self._best_effort_clear_retained()
            raise
        except HomeAssistantError as err:
            _LOGGER.debug(
                "MQTT bridge start attempt failed (broker not ready yet): %s", err
            )
            self._rollback_partial_start()
            await self._best_effort_clear_retained()
            return False
        except Exception:
            # Any other unexpected error: roll back partial wiring before it
            # propagates so no stale subscriptions/listener stay attached to
            # the coordinator; the caller decides logging/retry policy.
            self._rollback_partial_start()
            await self._best_effort_clear_retained()
            raise

        _LOGGER.info(
            "MQTT proxy bridge started – publishing to %s/# and %s/#",
            self._topic_base, self._proxy_base,
        )
        return True

    def _rollback_partial_start(self) -> None:
        """Undo partial wiring left by a failed start attempt.

        Only runs before the commit point (``_started`` is never True here),
        so this must never be reused for teardown of a started bridge — that
        path is ``async_stop`` (which also publishes the retained offline).
        """
        if self._unsub_coordinator is not None:
            self._unsub_coordinator()
            self._unsub_coordinator = None
        for unsub in self._unsub_command:
            unsub()
        self._unsub_command.clear()

    async def _best_effort_clear_retained(self) -> None:
        """Clear retained topics a failed start attempt may have left behind.

        ``_publish_register_metadata`` publishes the retained ``/config``
        before the commit point, and a cancelled final ``online`` publish can
        have reached the broker; both would otherwise advertise a bridge that
        is not running. Publishing an empty retained payload deletes the
        retained ``/config``; ``offline`` corrects the availability topic.
        Errors are swallowed — the broker being unreachable is usually the
        very reason the attempt failed.
        """
        if not _has_mqtt(self._hass):
            return
        from homeassistant.components import mqtt

        for topic, payload in (
            (f"{self._topic_base}/config", ""),
            (f"{self._topic_base}/available", "offline"),
        ):
            try:
                # Time-bounded so teardown/cancellation can never hang on a
                # dead broker; wait_for runs the publish in its own task.
                await asyncio.wait_for(
                    mqtt.async_publish(  # type: ignore[attr-defined]
                        self._hass, topic, payload, QOS, retain=True,
                    ),
                    timeout=2.0,
                )
            except Exception:  # best-effort cleanup only
                return

    def _schedule_start_retry(self) -> None:
        """Schedule the background self-healing start loop (idempotent)."""
        if self._start_retry_task is not None and not self._start_retry_task.done():
            return
        self._start_retry_task = self._hass.async_create_background_task(
            self._start_retry_loop(), name="kostal_kore_mqtt_bridge_start_retry"
        )

    async def _start_retry_loop(self) -> None:
        """Keep retrying until the bridge starts or the entry is unloaded.

        Waits through both blockers — the MQTT integration not being loaded
        (yet, or during a reload of it) and the broker being unreachable. The
        interval backs off to ``_RETRY_MAX_DELAY`` and then probes at that
        pace indefinitely; there is deliberately no give-up so the logged
        promise "will start automatically once the broker is available" holds.
        Cancellation (entry unload) is the only exit besides success.
        """
        delay: float = _RETRY_INITIAL_DELAY
        attempt = 0
        try:
            while not self._started:
                await asyncio.sleep(delay)
                attempt += 1
                delay = min(delay * 2, _RETRY_MAX_DELAY)
                if not _has_mqtt(self._hass):
                    # MQTT integration not (re)loaded yet – keep waiting.
                    continue
                try:
                    if await self._try_start():
                        _LOGGER.info(
                            "MQTT bridge recovered: started on retry attempt %d.",
                            attempt,
                        )
                        return
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Same policy as the first attempt (guarded in
                    # async_setup_entry): an unexpected error must not end
                    # self-healing — log loudly and keep retrying.
                    _LOGGER.exception(
                        "Unexpected error in MQTT bridge start attempt %d; "
                        "retrying.",
                        attempt,
                    )
        finally:
            self._start_retry_task = None

    @staticmethod
    def _reraise_if_stop_cancelled() -> None:
        """Re-raise when the task running ``async_stop`` is itself cancelled.

        Awaiting a child task we just cancelled raises ``CancelledError`` in
        two indistinguishable cases: the child acknowledged our cancel, or the
        caller cancelled *us* while we awaited (e.g. ``asyncio.wait_for``'s
        unload timeout in ``_await_cleanup_step``). Swallowing the latter
        silently defeats the caller's timeout, so re-raise when our own task
        has a pending cancellation.
        """
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            raise asyncio.CancelledError

    async def async_stop(self) -> None:
        """Stop the bridge and publish offline status."""
        retry_task = self._start_retry_task
        if retry_task is not None and not retry_task.done():
            retry_task.cancel()
            try:
                await retry_task
            except asyncio.CancelledError:
                self._reraise_if_stop_cancelled()
            except Exception:  # defensive: teardown must never raise
                pass
        self._start_retry_task = None

        if not self._started:
            # A failed or cancelled start attempt may have left retained
            # topics on the broker; clear them so external consumers do not
            # see a ghost bridge after unload.
            if self._start_attempted:
                await self._best_effort_clear_retained()
                self._start_attempted = False
            return

        if self._unsub_coordinator is not None:
            self._unsub_coordinator()
            self._unsub_coordinator = None

        if self._publish_task is not None and not self._publish_task.done():
            self._publish_task.cancel()
            try:
                await self._publish_task
            except asyncio.CancelledError:
                self._reraise_if_stop_cancelled()
            except Exception:
                pass
            self._publish_task = None

        for unsub in self._unsub_command:
            unsub()
        self._unsub_command.clear()

        if _has_mqtt(self._hass):
            from homeassistant.components import mqtt

            await mqtt.async_publish(  # type: ignore[attr-defined]
                self._hass, f"{self._topic_base}/available", "offline", QOS, retain=True,
            )

        self._started = False
        self._start_attempted = False
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
        if self._publish_task is not None and not self._publish_task.done():
            self._publish_task.cancel()
        self._publish_task = self._hass.async_create_task(self._publish_data(data))

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

        publishes = [
            mqtt.async_publish(  # type: ignore[attr-defined]
                self._hass,
                f"{self._topic_base}/register/{key}",
                json.dumps(val, default=str) if not isinstance(val, str) else val,
                QOS,
                retain=True,
            )
            for key, val in safe.items()
        ]
        await asyncio.gather(*publishes, return_exceptions=True)

        await self._publish_proxy_topics(safe)

    async def _publish_proxy_topics(self, data: dict[str, Any]) -> None:
        """Publish simplified proxy topics for evcc/iobroker."""
        if not _has_mqtt(self._hass):
            return

        from homeassistant.components import mqtt

        # GEÄNDERT: home_power = home_from_pv + home_from_battery + home_from_grid
        # Vorher wurde nur home_from_pv (PV→Haus-Anteil) verwendet, was bei Netz-
        # oder Batteriebezug zu massiv zu niedrigen Werten führte und externe EMS
        # (evcc/iobroker) zu falschen Lade-/Entladeentscheidungen verleitete.
        home_total = sum_home_consumption_power_w(
            optional_float(data.get("home_from_pv")),
            optional_float(data.get("home_from_battery")),
            optional_float(data.get("home_from_grid")),
        )

        dc_pv = optional_float(data.get("total_dc_power"))
        pv_dc_fmt = self._fmt(dc_pv)
        pv_ac_est_fmt = (
            self._fmt(dc_pv_power_to_ac_estimate_w(dc_pv))
            if dc_pv is not None
            else None
        )

        proxy_map: dict[str, str | None] = {
            # Legacy key kept for evcc/ioBroker configs; same value as pv_power_dc.
            "pv_power": pv_dc_fmt,
            "pv_power_dc": pv_dc_fmt,
            "pv_power_ac_est": pv_ac_est_fmt,
            "grid_power": self._fmt(data.get("pm_total_active")),
            "battery_power": self._fmt(data.get("battery_cd_power")),
            "battery_soc": self._fmt(data.get("battery_soc")),
            "home_power": self._fmt(home_total),
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
                    "pv_power_dc": f"{self._proxy_base}/pv_power_dc",
                    "pv_power_ac_est": f"{self._proxy_base}/pv_power_ac_est",
                    "grid_power": f"{self._proxy_base}/grid_power",
                    "battery_power": f"{self._proxy_base}/battery_power",
                    "battery_soc": f"{self._proxy_base}/battery_soc",
                    "home_power": f"{self._proxy_base}/home_power",
                    "inverter_state": f"{self._proxy_base}/inverter_state",
                },
                "proxy_topic_notes": {
                    "pv_power": "Legacy alias for pv_power_dc (Modbus register 100, DC side)",
                    "pv_power_dc": "total_dc_power [W] before inverter conversion",
                    "pv_power_ac_est": "DC PV scaled by inverter efficiency (~0.96)",
                    "grid_power": "pm_total_active at KSEM [W], AC",
                    "home_power": "Sum of home_from_pv+battery+grid; omitted if any register missing",
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
        if not self._started:
            # Never act on messages delivered into a half-started (or rolled
            # back) subscription window — e.g. retained commands replayed by
            # the broker during a start attempt that later fails. Acting here
            # would write real inverter registers from a bridge that is
            # officially not running; external systems re-send cyclically.
            _LOGGER.debug("Ignoring MQTT command before bridge start: %s", msg.topic)
            return
        topic: str = msg.topic
        payload: str = msg.payload

        expected_prefix = f"{self._topic_base}/command/"
        if not topic.startswith(expected_prefix):
            _LOGGER.warning("Malformed command topic: %s", topic)
            return
        reg_name = topic[len(expected_prefix):]
        if not reg_name or "/" in reg_name:
            _LOGGER.warning("Malformed command topic: %s", topic)
            return

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
        if not self._started:
            # See _handle_command: no register writes before the commit point.
            _LOGGER.debug(
                "Ignoring MQTT proxy command before bridge start: %s", msg.topic
            )
            return
        topic: str = msg.topic
        payload: str = msg.payload

        expected_prefix = f"{self._proxy_base}/command/"
        if not topic.startswith(expected_prefix):
            _LOGGER.warning("Malformed proxy command topic: %s", topic)
            return
        proxy_name = topic[len(expected_prefix):]
        if not proxy_name or "/" in proxy_name:
            _LOGGER.warning("Malformed proxy command topic: %s", topic)
            return

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
        # --- Validate BEFORE consuming the rate-limit slot ---
        if reg.name in self._BATTERY_REG_NAMES and not self._installer_access:
            _LOGGER.warning(
                "MQTT command rejected: installer access required for %s (source: %s)",
                reg.name,
                source,
            )
            self._log_audit_rejection(reg.name, None, "rejected_installer", source)
            return

        # SoC controller arbitration for battery registers
        if reg.name in self._BATTERY_REG_NAMES:
            ctrl = self._soc_controller
            if ctrl is not None and getattr(ctrl, "active", False):
                _LOGGER.warning(
                    "MQTT command rejected: %s (SoC Controller active, target=%.0f%%). "
                    "Stop the SoC Controller first. (source: %s)",
                    reg.name, ctrl.target_soc or 0, source,
                )
                self._log_audit_rejection(reg.name, None, "rejected_soc_active", source)
                return

        try:
            value: Any
            try:
                value = json.loads(payload)
            except (json.JSONDecodeError, ValueError):
                value = payload

            # json.loads can produce bool, None, list, dict — reject all
            # non-numeric types before they reach the Modbus write path.
            if isinstance(value, bool) or value is None or isinstance(value, (list, dict)):
                _LOGGER.warning(
                    "MQTT command rejected: unsupported type %s for %s (source: %s)",
                    type(value).__name__, reg.name, source,
                )
                self._log_audit_rejection(reg.name, None, "rejected_validation",
                                          f"type={type(value).__name__}")
                return

            if isinstance(value, str):
                try:
                    value = float(value)
                except ValueError:
                    _LOGGER.warning(
                        "MQTT command rejected: non-numeric value %r for %s (source: %s)",
                        payload, reg.name, source,
                    )
                    self._log_audit_rejection(reg.name, None, "rejected_validation",
                                              f"non-numeric={payload!r}")
                    return

            if isinstance(value, (int, float)) and (math.isnan(value) or math.isinf(value)):
                _LOGGER.warning(
                    "MQTT command rejected: NaN/Infinity for %s (source: %s)",
                    reg.name, source,
                )
                self._log_audit_rejection(reg.name, None, "rejected_validation", "NaN/Inf")
                return

            # --- Rate-limit AFTER validation passes ---
            if not self._check_rate_limit(reg.name):
                self._rate_limited_count += 1
                self._log_audit_rejection(reg.name, value, "rejected_rate", source)
                return

            async with self._write_lock:
                await self._coordinator.async_write_register(
                    reg, value, audit_source="mqtt"
                )

            self._command_count += 1
            _LOGGER.info(
                "MQTT command executed: %s = %s (source: %s)",
                reg.name, value, source,
            )

            await self._coordinator.async_request_refresh()

        except Exception as err:
            _LOGGER.error(
                "MQTT command failed for %s: %s (source: %s)",
                reg.name, err, source,
                exc_info=True,
            )

    def _log_audit_rejection(
        self, key: str, value: Any, result: str, detail: str
    ) -> None:
        """Log a rejection event to the write audit if one is attached."""
        audit = getattr(self._coordinator, "_write_audit", None)
        if audit is None:
            return
        import time

        from .write_audit import WriteEvent
        audit.log(WriteEvent(
            ts=time.monotonic(),
            source="mqtt",
            key=key,
            value=value,
            result=result,
            detail=detail,
        ))
