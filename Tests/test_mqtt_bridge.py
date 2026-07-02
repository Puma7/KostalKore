"""Tests for KostalMqttBridge."""

from __future__ import annotations

import json
import sys
from types import ModuleType  # noqa: F401
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from kostal_plenticore.modbus_registers import (
    REGISTER_BY_NAME,  # noqa: F401
    WRITABLE_REGISTERS,  # noqa: F401
    Access,  # noqa: F401
)
from kostal_plenticore.mqtt_bridge import (
    SAFE_WRITABLE_REGISTERS,
    TOPIC_PREFIX,
    KostalMqttBridge,
    _has_mqtt,
)


def _mock_mqtt_module(connected: bool = True) -> MagicMock:
    """Create a mock homeassistant.components.mqtt module."""
    mock = MagicMock()
    mock.async_publish = AsyncMock()
    mock.async_subscribe = AsyncMock(return_value=MagicMock())
    mock.is_connected = MagicMock(return_value=connected)
    return mock


def _mock_hass(mqtt_available: bool = True) -> MagicMock:
    hass = MagicMock()
    hass.config.components = {"mqtt"} if mqtt_available else set()

    def _create_task(coro, *args, **kwargs):
        # Tests only assert task creation; close the coroutine to avoid
        # "was never awaited" warnings from the MagicMock stub. done() must
        # return a real False so the idempotency/reentrancy guards
        # (`not task.done()`) are actually exercised.
        task = MagicMock()
        task.done.return_value = False
        coro.close()
        return task

    hass.async_create_task = MagicMock(side_effect=_create_task)
    hass.async_create_background_task = MagicMock(side_effect=_create_task)
    return hass


def _mock_coordinator() -> MagicMock:
    coord = MagicMock()
    coord.data = {"total_dc_power": 4500.0, "battery_soc": 72}
    coord.async_add_listener = MagicMock()
    coord.async_write_register = AsyncMock()
    coord.async_request_refresh = AsyncMock()
    return coord


class TestHasMqtt:
    """Test the _has_mqtt helper."""

    def test_mqtt_available(self) -> None:
        hass = _mock_hass(mqtt_available=True)
        assert _has_mqtt(hass) is True

    def test_mqtt_not_available(self) -> None:
        hass = _mock_hass(mqtt_available=False)
        assert _has_mqtt(hass) is False


class TestBridgeTopics:
    """Test topic structure."""

    def test_topic_base(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")
        assert bridge.topic_base == f"{TOPIC_PREFIX}/INV123/modbus"


class TestBridgeStartWithoutMqtt:
    """Test that the bridge gracefully handles missing MQTT."""

    @pytest.mark.asyncio
    async def test_start_without_mqtt_schedules_retry(self) -> None:
        """mqtt integration not loaded yet: no crash, retry loop scheduled."""
        hass = _mock_hass(mqtt_available=False)
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")
        await bridge.async_start()
        assert bridge._started is False
        # The self-heal must also cover "mqtt loads after this integration".
        hass.async_create_background_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_without_start_is_safe(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")
        await bridge.async_stop()


class TestBridgeStartWithMqtt:
    """Test bridge start when MQTT is available."""

    @pytest.mark.asyncio
    async def test_start_publishes_online(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")
        mock_mqtt = _mock_mqtt_module()

        with patch.dict(sys.modules, {"homeassistant.components.mqtt": mock_mqtt}):
            await bridge.async_start()

            mock_mqtt.async_publish.assert_any_call(
                hass,
                f"{TOPIC_PREFIX}/INV123/modbus/available",
                "online",
                1,
                retain=True,
            )
            assert bridge._started is True
            coord.async_add_listener.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_subscribes_to_writable_commands(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")
        mock_mqtt = _mock_mqtt_module()

        with patch.dict(sys.modules, {"homeassistant.components.mqtt": mock_mqtt}):
            await bridge.async_start()

            subscribe_calls = mock_mqtt.async_subscribe.call_args_list
            subscribed_topics = {call.args[1] for call in subscribe_calls}

            for reg in SAFE_WRITABLE_REGISTERS:
                expected_topic = f"{TOPIC_PREFIX}/INV123/modbus/command/{reg.name}"
                assert expected_topic in subscribed_topics, (
                    f"Missing subscription for {reg.name}"
                )

    @pytest.mark.asyncio
    async def test_stop_publishes_offline(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")
        mock_mqtt = _mock_mqtt_module()

        with patch.dict(sys.modules, {"homeassistant.components.mqtt": mock_mqtt}):
            await bridge.async_start()
            mock_mqtt.async_publish.reset_mock()
            await bridge.async_stop()

            mock_mqtt.async_publish.assert_any_call(
                hass,
                f"{TOPIC_PREFIX}/INV123/modbus/available",
                "offline",
                1,
                retain=True,
            )
            assert bridge._started is False


class TestBridgeStartSelfHealing:
    """A broker error at start must not abort setup; the bridge self-heals."""

    @pytest.mark.asyncio
    async def test_start_defers_and_schedules_retry_on_broker_error(self) -> None:
        hass = _mock_hass(mqtt_available=True)
        scheduled = []

        def _bg(coro, name=None):
            scheduled.append(coro)
            return MagicMock()

        hass.async_create_background_task = MagicMock(side_effect=_bg)
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")

        mock_mqtt = _mock_mqtt_module()
        mock_mqtt.async_publish = AsyncMock(
            side_effect=HomeAssistantError("mqtt_broker_error")
        )

        with patch.dict(sys.modules, {"homeassistant.components.mqtt": mock_mqtt}):
            # Must NOT raise even though the broker rejects the first publish.
            await bridge.async_start()

        assert bridge._started is False
        assert bridge._start_retry_task is not None
        assert len(scheduled) == 1
        # No partial wiring left behind by the failed attempt.
        assert bridge._unsub_command == []
        assert bridge._unsub_coordinator is None
        # Close the captured retry coroutine to avoid "never awaited" warnings.
        scheduled[0].close()

    @pytest.mark.asyncio
    async def test_retry_loop_recovers_when_broker_returns(self) -> None:
        hass = _mock_hass(mqtt_available=True)
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")

        mock_mqtt = _mock_mqtt_module()
        attempts = {"n": 0}

        async def _publish(*_args, **_kwargs):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise HomeAssistantError("mqtt_broker_error")

        mock_mqtt.async_publish = AsyncMock(side_effect=_publish)

        with patch.dict(sys.modules, {"homeassistant.components.mqtt": mock_mqtt}), \
                patch("asyncio.sleep", new=AsyncMock()):
            await bridge._start_retry_loop()

        assert bridge._started is True
        assert bridge._unsub_coordinator is not None

    @pytest.mark.asyncio
    async def test_try_start_rolls_back_partial_wiring_on_unexpected_error(self) -> None:
        hass = _mock_hass(mqtt_available=True)
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")

        mock_mqtt = _mock_mqtt_module()
        # Subscriptions + listener succeed, then an unexpected (non-broker) error
        # occurs during metadata publish. The partial wiring must be rolled back
        # and the error re-raised so the setup guard can log-and-continue.
        mock_mqtt.async_publish = AsyncMock(side_effect=RuntimeError("boom"))

        with patch.dict(sys.modules, {"homeassistant.components.mqtt": mock_mqtt}):
            with pytest.raises(RuntimeError):
                await bridge._try_start()

        assert bridge._started is False
        assert bridge._unsub_command == []
        assert bridge._unsub_coordinator is None

    @pytest.mark.asyncio
    async def test_retry_loop_waits_for_mqtt_integration(self) -> None:
        """mqtt not loaded: the loop keeps waiting and starts once it appears."""
        hass = _mock_hass(mqtt_available=False)
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")
        mock_mqtt = _mock_mqtt_module()

        async def _sleep(_delay):
            # Simulate the mqtt integration finishing setup during the wait.
            hass.config.components = {"mqtt"}

        with patch.dict(sys.modules, {"homeassistant.components.mqtt": mock_mqtt}), \
                patch("asyncio.sleep", new=AsyncMock(side_effect=_sleep)):
            await bridge._start_retry_loop()

        assert bridge._started is True

    @pytest.mark.asyncio
    async def test_retry_loop_survives_unexpected_error(self) -> None:
        """A non-HomeAssistantError on a retry must not end self-healing."""
        hass = _mock_hass(mqtt_available=True)
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")

        mock_mqtt = _mock_mqtt_module()
        raised = {"done": False}

        async def _publish(*_args, **_kwargs):
            if not raised["done"]:
                raised["done"] = True
                raise RuntimeError("unexpected mid-start explosion")

        mock_mqtt.async_publish = AsyncMock(side_effect=_publish)

        with patch.dict(sys.modules, {"homeassistant.components.mqtt": mock_mqtt}), \
                patch("asyncio.sleep", new=AsyncMock()):
            await bridge._start_retry_loop()

        assert bridge._started is True

    @pytest.mark.asyncio
    async def test_commit_point_failure_clears_retained_state(self) -> None:
        """If the final online publish fails, retained /config is cleared and
        availability is corrected to offline — no ghost bridge on the broker."""
        hass = _mock_hass(mqtt_available=True)
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")

        mock_mqtt = _mock_mqtt_module()

        async def _publish(_hass, topic, payload, _qos, retain=False):
            if payload == "online":
                raise HomeAssistantError("mqtt_broker_error")

        mock_mqtt.async_publish = AsyncMock(side_effect=_publish)

        with patch.dict(sys.modules, {"homeassistant.components.mqtt": mock_mqtt}):
            assert await bridge._try_start() is False

        assert bridge._started is False
        published = [
            (call.args[1], call.args[2])
            for call in mock_mqtt.async_publish.call_args_list
        ]
        assert (f"{TOPIC_PREFIX}/INV123/modbus/config", "") in published
        assert (f"{TOPIC_PREFIX}/INV123/modbus/available", "offline") in published

    @pytest.mark.asyncio
    async def test_stop_after_failed_attempt_clears_retained_state(self) -> None:
        """Unload after a failed start attempt clears leaked retained topics."""
        hass = _mock_hass(mqtt_available=True)
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")
        bridge._start_attempted = True  # a start attempt ran and failed

        mock_mqtt = _mock_mqtt_module()
        with patch.dict(sys.modules, {"homeassistant.components.mqtt": mock_mqtt}):
            await bridge.async_stop()

        published = [
            (call.args[1], call.args[2])
            for call in mock_mqtt.async_publish.call_args_list
        ]
        assert (f"{TOPIC_PREFIX}/INV123/modbus/config", "") in published
        assert (f"{TOPIC_PREFIX}/INV123/modbus/available", "offline") in published
        assert bridge._start_attempted is False


class TestCommandGating:
    """Inbound commands must never execute before the bridge commit point."""

    @pytest.mark.asyncio
    async def test_handle_command_ignored_before_start(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")
        assert bridge._started is False

        msg = MagicMock()
        msg.retain = False
        msg.topic = f"{TOPIC_PREFIX}/INV123/modbus/command/active_power_setpoint"
        msg.payload = "80"

        await bridge._handle_command(msg)
        coord.async_write_register.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_proxy_command_ignored_before_start(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123", installer_access=True)
        assert bridge._started is False

        msg = MagicMock()
        msg.retain = False
        msg.topic = f"{TOPIC_PREFIX}/INV123/proxy/command/battery_charge"
        msg.payload = "2000"

        await bridge._handle_proxy_command(msg)
        coord.async_write_register.assert_not_called()

    @pytest.mark.asyncio
    async def test_retained_command_rejected(self) -> None:
        """Broker-retained commands replay on every restart — never execute."""
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")
        bridge._started = True

        msg = MagicMock()
        msg.retain = True
        msg.topic = f"{TOPIC_PREFIX}/INV123/modbus/command/active_power_setpoint"
        msg.payload = "80"

        await bridge._handle_command(msg)
        coord.async_write_register.assert_not_called()

    @pytest.mark.asyncio
    async def test_retained_proxy_command_rejected(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123", installer_access=True)
        bridge._started = True

        msg = MagicMock()
        msg.retain = True
        msg.topic = f"{TOPIC_PREFIX}/INV123/proxy/command/battery_charge"
        msg.payload = "2000"

        await bridge._handle_proxy_command(msg)
        coord.async_write_register.assert_not_called()


class TestCommandHandling:
    """Test inbound MQTT command processing."""

    @pytest.mark.asyncio
    async def test_handle_command_writes_register(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")
        bridge._started = True

        msg = MagicMock()
        msg.retain = False
        msg.topic = f"{TOPIC_PREFIX}/INV123/modbus/command/active_power_setpoint"
        msg.payload = "80"

        await bridge._handle_command(msg)

        coord.async_write_register.assert_called_once()
        call_args = coord.async_write_register.call_args
        assert call_args[0][0].name == "active_power_setpoint"
        assert call_args[0][1] == 80

    @pytest.mark.asyncio
    async def test_handle_command_rejects_readonly(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")
        bridge._started = True

        msg = MagicMock()
        msg.retain = False
        msg.topic = f"{TOPIC_PREFIX}/INV123/modbus/command/total_dc_power"
        msg.payload = "100"

        await bridge._handle_command(msg)
        coord.async_write_register.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_command_unknown_register(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")
        bridge._started = True

        msg = MagicMock()
        msg.retain = False
        msg.topic = f"{TOPIC_PREFIX}/INV123/modbus/command/nonexistent"
        msg.payload = "42"

        await bridge._handle_command(msg)
        coord.async_write_register.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_command_json_payload(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123", installer_access=True)
        bridge._started = True

        msg = MagicMock()
        msg.retain = False
        msg.topic = f"{TOPIC_PREFIX}/INV123/modbus/command/bat_min_soc"
        msg.payload = json.dumps(15.0)

        await bridge._handle_command(msg)

        coord.async_write_register.assert_called_once()
        call_args = coord.async_write_register.call_args
        assert call_args[0][1] == 15.0

    @pytest.mark.asyncio
    async def test_handle_command_rejects_battery_write_without_installer_access(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123", installer_access=False)
        bridge._started = True

        msg = MagicMock()
        msg.retain = False
        msg.topic = f"{TOPIC_PREFIX}/INV123/modbus/command/bat_min_soc"
        msg.payload = json.dumps(15.0)

        await bridge._handle_command(msg)
        coord.async_write_register.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_command_malformed_topic(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")
        bridge._started = True

        msg = MagicMock()
        msg.retain = False
        msg.topic = "x"
        msg.payload = "42"

        await bridge._handle_command(msg)
        coord.async_write_register.assert_not_called()


class TestDataPublishing:
    """Test outbound data publishing."""

    @pytest.mark.asyncio
    async def test_publish_data(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")
        bridge._started = True
        mock_mqtt = _mock_mqtt_module()

        with patch.dict(sys.modules, {"homeassistant.components.mqtt": mock_mqtt}):
            await bridge._publish_data({"total_dc_power": 4500.0, "battery_soc": 72})

            calls = mock_mqtt.async_publish.call_args_list
            topics = {c.args[1] for c in calls}

            assert f"{TOPIC_PREFIX}/INV123/modbus/state" in topics
            assert f"{TOPIC_PREFIX}/INV123/modbus/register/total_dc_power" in topics
            assert f"{TOPIC_PREFIX}/INV123/modbus/register/battery_soc" in topics

    @pytest.mark.asyncio
    async def test_publish_data_without_mqtt_is_noop(self) -> None:
        hass = _mock_hass(mqtt_available=False)
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")
        await bridge._publish_data({"total_dc_power": 0})

    @pytest.mark.asyncio
    async def test_on_coordinator_update_creates_task(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")
        bridge._started = True

        bridge._on_coordinator_update()
        hass.async_create_task.assert_called_once()

    def test_on_coordinator_update_noop_when_stopped(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")
        bridge._started = False

        bridge._on_coordinator_update()
        hass.async_create_task.assert_not_called()

    def test_on_coordinator_update_noop_when_no_data(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        coord.data = None
        bridge = KostalMqttBridge(hass, coord, "INV123")
        bridge._started = True

        bridge._on_coordinator_update()
        hass.async_create_task.assert_not_called()


class TestRateLimiting:
    """Test rate limiting on MQTT write commands."""

    def test_rate_limit_allows_first_write(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")
        assert bridge._check_rate_limit("test_reg") is True

    def test_rate_limit_blocks_rapid_repeat(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")
        bridge._check_rate_limit("test_reg")
        assert bridge._check_rate_limit("test_reg") is False

    def test_rate_limit_independent_per_register(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")
        bridge._check_rate_limit("reg_a")
        assert bridge._check_rate_limit("reg_b") is True


class TestProxyCommands:
    """Test simplified proxy command handling."""

    @pytest.mark.asyncio
    async def test_proxy_battery_charge_command(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123", installer_access=True)
        bridge._started = True

        msg = MagicMock()
        msg.retain = False
        msg.topic = f"{TOPIC_PREFIX}/INV123/proxy/command/battery_charge"
        msg.payload = "-5000"

        await bridge._handle_proxy_command(msg)
        coord.async_write_register.assert_called_once()

    @pytest.mark.asyncio
    async def test_proxy_unknown_command_rejected(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")
        bridge._started = True

        msg = MagicMock()
        msg.retain = False
        msg.topic = f"{TOPIC_PREFIX}/INV123/proxy/command/nonexistent"
        msg.payload = "42"

        await bridge._handle_proxy_command(msg)
        coord.async_write_register.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_write_rejects_nan(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        # Use installer_access=True so the NaN check is actually reached
        bridge = KostalMqttBridge(hass, coord, "INV123", installer_access=True)

        from kostal_plenticore.modbus_registers import REG_BAT_MIN_SOC
        await bridge._execute_write(REG_BAT_MIN_SOC, "NaN", source="test")
        coord.async_write_register.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_write_serialized(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123", installer_access=True)

        from kostal_plenticore.modbus_registers import REG_BAT_MIN_SOC
        await bridge._execute_write(REG_BAT_MIN_SOC, "50", source="test")
        coord.async_write_register.assert_called_once()


class TestListenerCleanup:
    """Test coordinator listener lifecycle."""

    @pytest.mark.asyncio
    async def test_stop_removes_coordinator_listener(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        unsub_cb = MagicMock()
        coord.async_add_listener.return_value = unsub_cb
        bridge = KostalMqttBridge(hass, coord, "INV123")
        mock_mqtt = _mock_mqtt_module()

        with patch.dict(sys.modules, {"homeassistant.components.mqtt": mock_mqtt}):
            await bridge.async_start()
            assert bridge._unsub_coordinator is unsub_cb
            await bridge.async_stop()
            unsub_cb.assert_called_once()
            assert bridge._unsub_coordinator is None

    @pytest.mark.asyncio
    async def test_double_start_is_idempotent(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")
        mock_mqtt = _mock_mqtt_module()

        with patch.dict(sys.modules, {"homeassistant.components.mqtt": mock_mqtt}):
            await bridge.async_start()
            await bridge.async_start()  # second call should be a no-op
            coord.async_add_listener.assert_called_once()


class TestPayloadValidation:
    """Test that non-numeric JSON payloads are rejected."""

    @pytest.mark.asyncio
    async def test_rejects_boolean_payload(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123", installer_access=True)
        from kostal_plenticore.modbus_registers import REG_BAT_MIN_SOC
        await bridge._execute_write(REG_BAT_MIN_SOC, "true", source="test")
        coord.async_write_register.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_null_payload(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123", installer_access=True)
        from kostal_plenticore.modbus_registers import REG_BAT_MIN_SOC
        await bridge._execute_write(REG_BAT_MIN_SOC, "null", source="test")
        coord.async_write_register.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_array_payload(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123", installer_access=True)
        from kostal_plenticore.modbus_registers import REG_BAT_MIN_SOC
        await bridge._execute_write(REG_BAT_MIN_SOC, "[1,2]", source="test")
        coord.async_write_register.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_object_payload(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123", installer_access=True)
        from kostal_plenticore.modbus_registers import REG_BAT_MIN_SOC
        await bridge._execute_write(REG_BAT_MIN_SOC, '{"a":1}', source="test")
        coord.async_write_register.assert_not_called()

    @pytest.mark.asyncio
    async def test_rate_limit_not_consumed_by_invalid_request(self) -> None:
        """Invalid requests must not burn the rate-limit slot."""
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123", installer_access=False)
        from kostal_plenticore.modbus_registers import REG_BAT_MIN_SOC
        # Rejected by installer_access check — rate limit should NOT be consumed
        await bridge._execute_write(REG_BAT_MIN_SOC, "50", source="test")
        coord.async_write_register.assert_not_called()
        # Subsequent legitimate write should not be rate-limited
        assert bridge._check_rate_limit(REG_BAT_MIN_SOC.name) is True


class TestProxyTopicBase:
    """Test proxy topic structure."""

    def test_proxy_base_topic(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")
        assert bridge._proxy_base == f"{TOPIC_PREFIX}/INV123/proxy"
