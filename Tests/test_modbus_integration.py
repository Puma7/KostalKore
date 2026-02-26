"""Tests for Modbus integration lifecycle, options flow, and unload."""

from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

kp_init = importlib.import_module("kostal_plenticore.__init__")
from kostal_plenticore.const import DOMAIN


async def test_options_flow_shows_form(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_plenticore_client,
) -> None:
    """Test the options flow shows a form with Modbus/MQTT fields."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] == "form"
    assert result["step_id"] == "init"


async def test_options_flow_modbus_disabled_saves_directly(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_plenticore_client,
) -> None:
    """Disabling Modbus saves directly without test step."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    from kostal_plenticore.config_flow import KostalPlenticoreOptionsFlow

    flow = KostalPlenticoreOptionsFlow()
    flow.hass = hass
    flow.handler = mock_config_entry.entry_id

    result = await flow.async_step_init(user_input={
        "modbus_enabled": False,
        "modbus_port": 1502,
        "modbus_unit_id": 71,
        "modbus_endianness": "auto",
        "mqtt_bridge_enabled": False,
    })

    assert result["type"] == "create_entry"
    assert result["data"]["modbus_enabled"] is False


async def test_options_flow_modbus_enabled_goes_to_test_step(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_plenticore_client,
) -> None:
    """Enabling Modbus triggers the connection test step."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] == "form"

    with patch(
        "kostal_plenticore.modbus_client.KostalModbusClient",
    ) as MockClient:
        instance = AsyncMock()
        instance.connect = AsyncMock()
        instance.detect_endianness = AsyncMock(return_value="little")
        instance.read_register = AsyncMock(return_value="PLENTICORE")
        instance.disconnect = AsyncMock()
        MockClient.return_value = instance

        result2 = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                "modbus_enabled": True,
                "modbus_port": 1502,
                "modbus_unit_id": 71,
                "modbus_endianness": "auto",
                "mqtt_bridge_enabled": False,
            },
        )

    assert result2["type"] == "form"
    assert result2["step_id"] == "modbus_test"
    placeholders = result2.get("description_placeholders", {})
    assert "ERFOLGREICH" in placeholders.get("test_result", "")


async def test_options_flow_modbus_test_failure_shows_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_plenticore_client,
) -> None:
    """Connection test failure shows error details."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    with patch(
        "kostal_plenticore.modbus_client.KostalModbusClient",
    ) as MockClient:
        instance = AsyncMock()
        instance.connect = AsyncMock(side_effect=ConnectionError("Connection refused"))
        MockClient.return_value = instance

        result2 = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                "modbus_enabled": True,
                "modbus_port": 1502,
                "modbus_unit_id": 71,
                "modbus_endianness": "auto",
                "mqtt_bridge_enabled": False,
            },
        )

    assert result2["type"] == "form"
    assert result2["step_id"] == "modbus_test"
    assert "modbus_test_failed" in result2.get("errors", {}).get("base", "")
    placeholders = result2.get("description_placeholders", {})
    assert "FEHLGESCHLAGEN" in placeholders.get("test_result", "")


async def test_options_flow_modbus_test_confirm_saves(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_plenticore_client,
) -> None:
    """Confirming the test step saves the options."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    from kostal_plenticore.config_flow import KostalPlenticoreOptionsFlow

    flow = KostalPlenticoreOptionsFlow()
    flow.hass = hass
    flow.handler = mock_config_entry.entry_id
    flow._user_input = {
        "modbus_enabled": True,
        "modbus_port": 1502,
        "modbus_unit_id": 71,
        "modbus_endianness": "auto",
        "mqtt_bridge_enabled": False,
    }

    result = await flow.async_step_modbus_test(user_input={})

    assert result["type"] == "create_entry"
    assert result["data"]["modbus_enabled"] is True


async def test_setup_entry_modbus_disabled(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_plenticore_client,
) -> None:
    """Modbus-related branches are skipped when modbus_enabled is False."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    entry_data = hass.data.get(DOMAIN, {}).get(mock_config_entry.entry_id, {})
    assert entry_data.get("modbus_coordinator") is None
    assert entry_data.get("mqtt_bridge") is None


async def test_setup_entry_modbus_enabled_connection_failure(
    hass: HomeAssistant,
    mock_plenticore_client,
) -> None:
    """Modbus setup failure is non-fatal; REST API stays active."""
    entry = MockConfigEntry(
        entry_id="modbus_test_entry",
        title="scb",
        domain=DOMAIN,
        data={"host": "192.168.1.2", "password": "pw"},
        options={
            "modbus_enabled": True,
            "modbus_port": 1502,
            "modbus_unit_id": 71,
            "modbus_endianness": "auto",
        },
    )
    entry.add_to_hass(hass)

    with patch(
        "kostal_plenticore.modbus_coordinator.ModbusDataUpdateCoordinator.async_setup",
        side_effect=Exception("Modbus connect failed"),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    assert entry_data.get("modbus_coordinator") is None


async def test_setup_entry_modbus_enabled_success(
    hass: HomeAssistant,
    mock_plenticore_client,
) -> None:
    """Modbus setup succeeds and coordinator is stored."""
    entry = MockConfigEntry(
        entry_id="modbus_ok_entry",
        title="scb",
        domain=DOMAIN,
        data={"host": "192.168.1.2", "password": "pw"},
        options={
            "modbus_enabled": True,
            "modbus_port": 1502,
            "modbus_unit_id": 71,
            "modbus_endianness": "little",
        },
    )
    entry.add_to_hass(hass)

    with patch(
        "kostal_plenticore.modbus_coordinator.ModbusDataUpdateCoordinator.async_setup",
        new_callable=AsyncMock,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    assert entry_data.get("modbus_coordinator") is not None


async def test_setup_entry_modbus_auto_endianness(
    hass: HomeAssistant,
    mock_plenticore_client,
) -> None:
    """Auto endianness detection is called when set to 'auto'."""
    entry = MockConfigEntry(
        entry_id="modbus_auto_entry",
        title="scb",
        domain=DOMAIN,
        data={"host": "192.168.1.2", "password": "pw"},
        options={
            "modbus_enabled": True,
            "modbus_port": 1502,
            "modbus_unit_id": 71,
            "modbus_endianness": "auto",
        },
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "kostal_plenticore.modbus_coordinator.ModbusDataUpdateCoordinator.async_setup",
            new_callable=AsyncMock,
        ),
        patch(
            "kostal_plenticore.modbus_client.KostalModbusClient.detect_endianness",
            new_callable=AsyncMock,
            return_value="little",
        ) as mock_detect,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    mock_detect.assert_called_once()


async def test_setup_entry_mqtt_bridge_enabled(
    hass: HomeAssistant,
    mock_plenticore_client,
) -> None:
    """MQTT bridge is started when both modbus and mqtt_bridge are enabled."""
    entry = MockConfigEntry(
        entry_id="mqtt_bridge_entry",
        title="scb",
        domain=DOMAIN,
        data={"host": "192.168.1.2", "password": "pw"},
        options={
            "modbus_enabled": True,
            "modbus_port": 1502,
            "modbus_unit_id": 71,
            "modbus_endianness": "little",
            "mqtt_bridge_enabled": True,
        },
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "kostal_plenticore.modbus_coordinator.ModbusDataUpdateCoordinator.async_setup",
            new_callable=AsyncMock,
        ),
        patch(
            "kostal_plenticore.mqtt_bridge.KostalMqttBridge.async_start",
            new_callable=AsyncMock,
        ) as mock_start,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    mock_start.assert_called_once()


async def test_unload_entry_with_modbus_data(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_plenticore_client,
) -> None:
    """Unload cleans up Modbus/MQTT bridge entries from hass.data."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    mock_mqtt = MagicMock()
    mock_mqtt.async_stop = AsyncMock()
    mock_coord = MagicMock()
    mock_coord.async_shutdown = AsyncMock()

    hass.data.setdefault(DOMAIN, {})[mock_config_entry.entry_id] = {
        "modbus_coordinator": mock_coord,
        "mqtt_bridge": mock_mqtt,
    }

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    mock_mqtt.async_stop.assert_called_once()
    mock_coord.async_shutdown.assert_called_once()


async def test_options_updated_triggers_reload(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_plenticore_client,
) -> None:
    """Changing options reloads the config entry."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    await kp_init._async_options_updated(hass, mock_config_entry)
    await hass.async_block_till_done()
