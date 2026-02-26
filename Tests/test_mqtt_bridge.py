"""Tests for KostalMqttBridge."""

from __future__ import annotations

import json
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kostal_plenticore.mqtt_bridge import (
    KostalMqttBridge,
    SAFE_WRITABLE_REGISTERS,
    TOPIC_PREFIX,
    _has_mqtt,
)
from kostal_plenticore.modbus_registers import (
    REGISTER_BY_NAME,
    WRITABLE_REGISTERS,
    Access,
)


def _mock_mqtt_module() -> MagicMock:
    """Create a mock homeassistant.components.mqtt module."""
    mock = MagicMock()
    mock.async_publish = AsyncMock()
    mock.async_subscribe = AsyncMock(return_value=MagicMock())
    return mock


def _mock_hass(mqtt_available: bool = True) -> MagicMock:
    hass = MagicMock()
    hass.config.components = {"mqtt"} if mqtt_available else set()
    hass.async_create_task = MagicMock(side_effect=lambda coro: coro)
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
    async def test_start_without_mqtt_logs_warning(self) -> None:
        hass = _mock_hass(mqtt_available=False)
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")
        await bridge.async_start()
        assert bridge._started is False

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


class TestCommandHandling:
    """Test inbound MQTT command processing."""

    @pytest.mark.asyncio
    async def test_handle_command_writes_register(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")
        bridge._started = True

        msg = MagicMock()
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
        msg.topic = f"{TOPIC_PREFIX}/INV123/modbus/command/nonexistent"
        msg.payload = "42"

        await bridge._handle_command(msg)
        coord.async_write_register.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_command_json_payload(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")
        bridge._started = True

        msg = MagicMock()
        msg.topic = f"{TOPIC_PREFIX}/INV123/modbus/command/bat_min_soc"
        msg.payload = json.dumps(15.0)

        await bridge._handle_command(msg)

        coord.async_write_register.assert_called_once()
        call_args = coord.async_write_register.call_args
        assert call_args[0][1] == 15.0

    @pytest.mark.asyncio
    async def test_handle_command_malformed_topic(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")
        bridge._started = True

        msg = MagicMock()
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
        bridge = KostalMqttBridge(hass, coord, "INV123")
        bridge._started = True

        msg = MagicMock()
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
        msg.topic = f"{TOPIC_PREFIX}/INV123/proxy/command/nonexistent"
        msg.payload = "42"

        await bridge._handle_proxy_command(msg)
        coord.async_write_register.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_write_rejects_nan(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")

        from kostal_plenticore.modbus_registers import REG_BAT_MIN_SOC
        await bridge._execute_write(REG_BAT_MIN_SOC, "NaN", source="test")
        coord.async_write_register.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_write_serialized(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")

        from kostal_plenticore.modbus_registers import REG_BAT_MIN_SOC
        await bridge._execute_write(REG_BAT_MIN_SOC, "50", source="test")
        coord.async_write_register.assert_called_once()


class TestProxyTopicBase:
    """Test proxy topic structure."""

    def test_proxy_base_topic(self) -> None:
        hass = _mock_hass()
        coord = _mock_coordinator()
        bridge = KostalMqttBridge(hass, coord, "INV123")
        assert bridge._proxy_base == f"{TOPIC_PREFIX}/INV123/proxy"
