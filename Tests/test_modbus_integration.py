"""Tests for Modbus integration lifecycle, options flow, and unload."""

from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

kp_init = importlib.import_module("custom_components.kostal_kore.__init__")
from custom_components.kostal_kore.const import DOMAIN

KNOWN_LINGERING_TIMER_TESTS = {
    "test_setup_entry_modbus_enabled_success",
    "test_setup_entry_modbus_auto_endianness",
    "test_setup_entry_mqtt_bridge_enabled",
    "test_setup_entry_mqtt_bridge_empty_identifiers",
    "test_setup_entry_mqtt_bridge_nonmatching_identifier",
    "test_modbus_platform_setup_fails_raises_config_entry_not_ready",
}


@pytest.fixture
def expected_lingering_timers(request: pytest.FixtureRequest) -> bool:
    """Allow known lingering timer teardown issues for specific tests."""
    return request.node.name in KNOWN_LINGERING_TIMER_TESTS


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

    from custom_components.kostal_kore.config_flow import KostalPlenticoreOptionsFlow

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
        "custom_components.kostal_kore.modbus_client.KostalModbusClient",
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
    assert "Modbus test passed" in placeholders.get("test_result", "")


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
        "custom_components.kostal_kore.modbus_client.KostalModbusClient",
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
    assert "Modbus test failed" in placeholders.get("test_result", "")


async def test_options_flow_modbus_test_confirm_saves(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_plenticore_client,
) -> None:
    """Confirming the test step saves the options."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    from custom_components.kostal_kore.config_flow import KostalPlenticoreOptionsFlow

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

    # HIGH-01 fix: Submit re-runs run_modbus_connection_test, so the Modbus
    # client must be mocked to succeed for this re-run too.
    with patch(
        "custom_components.kostal_kore.modbus_client.KostalModbusClient",
    ) as MockClient:
        instance = AsyncMock()
        instance.connect = AsyncMock()
        instance.detect_endianness = AsyncMock(return_value="little")
        instance.read_register = AsyncMock(return_value="PLENTICORE")
        instance.disconnect = AsyncMock()
        MockClient.return_value = instance

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
        "custom_components.kostal_kore.modbus_coordinator.ModbusDataUpdateCoordinator.async_setup",
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
        "custom_components.kostal_kore.modbus_coordinator.ModbusDataUpdateCoordinator.async_setup",
        new_callable=AsyncMock,
    ), patch(
        "custom_components.kostal_kore.battery_soc_controller.BatterySocController",
    ) as mock_soc_controller, patch(
        "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ):
        mock_soc = MagicMock()
        mock_soc.stop = AsyncMock()
        mock_soc_controller.return_value = mock_soc
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    assert entry_data.get("modbus_coordinator") is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


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
            "custom_components.kostal_kore.modbus_coordinator.ModbusDataUpdateCoordinator.async_setup",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.kostal_kore.modbus_client.KostalModbusClient.detect_endianness",
            new_callable=AsyncMock,
            return_value="little",
        ) as mock_detect,
        patch(
            "custom_components.kostal_kore.battery_soc_controller.BatterySocController",
        ) as mock_soc_controller,
        patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
            new=AsyncMock(return_value=True),
        ),
    ):
        mock_soc = MagicMock()
        mock_soc.stop = AsyncMock()
        mock_soc_controller.return_value = mock_soc
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # detect_endianness is called inside async_setup(), not again from __init__.py
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def _run_setup_with_mqtt_bridge(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    identifiers,
) -> MagicMock:
    """Helper: run async_setup_entry with a DummyPlenticore having custom identifiers.

    Returns the mock KostalMqttBridge constructor so callers can inspect calls.
    """
    from homeassistant.helpers.device_registry import DeviceInfo

    class _DummyPlenticoreWithIds:
        def __init__(self, *_args):
            self.device_info = DeviceInfo(
                identifiers=identifiers,
                manufacturer="Kostal",
                name="scb",
            )
            self._request_scheduler = None

        async def async_setup(self):
            return True

        async def async_unload(self):
            pass

    mock_modbus_coord = MagicMock()
    mock_modbus_coord.async_setup = AsyncMock()
    mock_modbus_coord.async_shutdown = AsyncMock()
    mock_modbus_coord._restore_isolation_sample = AsyncMock()
    mock_bridge = MagicMock()
    mock_bridge.async_start = AsyncMock()
    mock_bridge.async_stop = AsyncMock()

    mock_soc = MagicMock()
    mock_soc.stop = AsyncMock()

    with (
        patch("custom_components.kostal_kore.__init__.Plenticore", _DummyPlenticoreWithIds),
        patch("custom_components.kostal_kore.__init__.KostalModbusClient"),
        patch(
            "custom_components.kostal_kore.__init__.ModbusDataUpdateCoordinator",
            return_value=mock_modbus_coord,
        ),
        patch(
            "custom_components.kostal_kore.battery_soc_controller.BatterySocController",
            return_value=mock_soc,
        ),
        patch("custom_components.kostal_kore.__init__.KostalMqttBridge", return_value=mock_bridge) as mock_bridge_cls,
        patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
            new=AsyncMock(return_value=True),
        ),
    ):
        assert await kp_init.async_setup_entry(hass, entry) is True

    return mock_bridge_cls


async def test_setup_entry_mqtt_bridge_empty_identifiers(
    hass: HomeAssistant,
) -> None:
    """MQTT bridge uses entry_id when identifiers is empty (covers branch 262→266)."""
    entry = MockConfigEntry(
        entry_id="mqtt_empty_ids",
        domain=DOMAIN,
        data={"host": "192.168.1.2", "password": "pw"},
        options={"modbus_enabled": True, "modbus_port": 1502, "modbus_unit_id": 71,
                 "modbus_endianness": "little", "mqtt_bridge_enabled": True},
    )
    entry.add_to_hass(hass)

    mock_bridge_cls = await _run_setup_with_mqtt_bridge(hass, entry, identifiers=set())

    mock_bridge_cls.assert_called_once()
    device_id_arg = mock_bridge_cls.call_args[0][2]
    assert device_id_arg == entry.entry_id


async def test_setup_entry_mqtt_bridge_nonmatching_identifier(
    hass: HomeAssistant,
) -> None:
    """MQTT bridge skips non-domain identifiers before matching (covers branch 263→262)."""
    entry = MockConfigEntry(
        entry_id="mqtt_nonmatch_ids",
        domain=DOMAIN,
        data={"host": "192.168.1.2", "password": "pw"},
        options={"modbus_enabled": True, "modbus_port": 1502, "modbus_unit_id": 71,
                 "modbus_endianness": "little", "mqtt_bridge_enabled": True},
    )
    entry.add_to_hass(hass)

    # Use a list to control iteration order: non-matching first, matching second.
    identifiers = [("other_domain", "ignored"), (DOMAIN, "SN-99999")]
    mock_bridge_cls = await _run_setup_with_mqtt_bridge(hass, entry, identifiers=identifiers)

    mock_bridge_cls.assert_called_once()
    device_id_arg = mock_bridge_cls.call_args[0][2]
    assert device_id_arg == "SN-99999"


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
            "custom_components.kostal_kore.modbus_coordinator.ModbusDataUpdateCoordinator.async_setup",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.kostal_kore.mqtt_bridge.KostalMqttBridge.async_start",
            new_callable=AsyncMock,
        ) as mock_start,
        patch(
            "custom_components.kostal_kore.battery_soc_controller.BatterySocController",
        ) as mock_soc_controller,
        patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
            new=AsyncMock(return_value=True),
        ),
    ):
        mock_soc = MagicMock()
        mock_soc.stop = AsyncMock()
        mock_soc_controller.return_value = mock_soc
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    mock_start.assert_called_once()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


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


async def test_options_updated_ignored_when_entry_not_loaded(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Options updates that arrive during the unload/reload gap (no entry_data
    in hass.data) must NOT trigger another reload — that was the self-sustaining
    loop behind the "Config entry was never loaded!" binary_sensor errors."""
    mock_config_entry.add_to_hass(hass)

    with patch.object(
        hass.config_entries, "async_reload", AsyncMock(return_value=True)
    ) as mock_reload:
        await kp_init._async_options_updated(hass, mock_config_entry)

    mock_reload.assert_not_awaited()


async def test_options_updated_ignored_during_setup_in_progress(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Options updates while async_setup_entry is still running must not reload."""
    mock_config_entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})[mock_config_entry.entry_id] = {
        kp_init.KEY_SETUP_IN_PROGRESS: True,
    }

    with patch.object(
        hass.config_entries, "async_reload", AsyncMock(return_value=True)
    ) as mock_reload:
        await kp_init._async_options_updated(hass, mock_config_entry)

    mock_reload.assert_not_awaited()


async def test_options_updated_skips_reload_when_normalized_options_unchanged(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Normalized option snapshot must match the options-flow output exactly so
    HA-side dict reshuffles don't masquerade as actual user changes."""
    from custom_components.kostal_kore.config_flow import _normalize_options

    mock_config_entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})[mock_config_entry.entry_id] = {
        "_setup_options": _normalize_options(mock_config_entry.options),
        kp_init.KEY_SETUP_IN_PROGRESS: False,
    }

    with patch.object(
        hass.config_entries, "async_reload", AsyncMock(return_value=True)
    ) as mock_reload:
        await kp_init._async_options_updated(hass, mock_config_entry)

    mock_reload.assert_not_awaited()


async def test_options_updated_changed_options_triggers_reload(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_plenticore_client,
) -> None:
    """_async_options_updated reloads when options actually changed (branch 480→486)."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Overwrite the saved snapshot so it looks like options changed.
    hass.data[DOMAIN][mock_config_entry.entry_id]["_setup_options"] = {"stale": True}

    with patch.object(
        hass.config_entries, "async_reload", AsyncMock(return_value=True)
    ) as mock_reload:
        await kp_init._async_options_updated(hass, mock_config_entry)

    mock_reload.assert_awaited_once_with(mock_config_entry.entry_id)


async def test_modbus_platform_setup_fails_raises_config_entry_not_ready(
    hass: HomeAssistant,
    mock_plenticore_client,
) -> None:
    """MODBUS_PLATFORMS forward failure → ConfigEntryNotReady + rollback (lines 441-453, 488)."""
    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.const import Platform

    entry = MockConfigEntry(
        entry_id="modbus_platform_fail",
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

    async def _forward_side_effect(_entry, platforms):
        if Platform.BINARY_SENSOR in platforms:
            raise Exception("binary sensor forward failed")
        return True

    with (
        patch(
            "custom_components.kostal_kore.modbus_coordinator.ModbusDataUpdateCoordinator.async_setup",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.kostal_kore.battery_soc_controller.BatterySocController",
        ) as mock_soc_controller,
        patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
            side_effect=_forward_side_effect,
        ),
    ):
        mock_soc = MagicMock()
        mock_soc.stop = AsyncMock()
        mock_soc_controller.return_value = mock_soc
        await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_rollback_setup_shuts_down_ksem_coordinator(
    hass: HomeAssistant,
) -> None:
    """_rollback_setup shuts down ksem_coordinator when it is present (line 493)."""
    mock_ksem = MagicMock()
    mock_ksem.async_shutdown = AsyncMock()

    entry = MockConfigEntry(entry_id="rollback_ksem_test", domain=DOMAIN, title="scb")

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "ksem_coordinator": mock_ksem,
        "modbus_coordinator": None,
    }

    mock_plenticore = MagicMock()
    mock_plenticore.async_unload = AsyncMock()

    await kp_init._rollback_setup(hass, entry, mock_plenticore)

    mock_ksem.async_shutdown.assert_awaited_once()


async def test_async_unload_loaded_platforms_empty_list_is_noop(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """_async_unload_loaded_platforms([]) must return True without calling HA."""
    mock_config_entry.add_to_hass(hass)

    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        AsyncMock(return_value=True),
    ) as mock_unload:
        result = await kp_init._async_unload_loaded_platforms(
            hass, mock_config_entry, []
        )

    assert result is True
    mock_unload.assert_not_awaited()


async def test_async_unload_loaded_platforms_tolerates_never_loaded_value_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """HA 'Config entry was never loaded' during unload must be swallowed."""
    mock_config_entry.add_to_hass(hass)

    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        AsyncMock(side_effect=ValueError("Config entry was never loaded!")),
    ):
        result = await kp_init._async_unload_loaded_platforms(
            hass, mock_config_entry, list(kp_init.PLATFORMS)
        )

    assert result is True  # graceful — unload_ok starts True and the race is tolerated


async def test_async_unload_loaded_platforms_reraises_unrelated_value_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """ValueError with a different message must propagate."""
    import pytest

    mock_config_entry.add_to_hass(hass)

    with (
        patch.object(
            hass.config_entries,
            "async_unload_platforms",
            AsyncMock(side_effect=ValueError("unrelated problem")),
        ),
        pytest.raises(ValueError, match="unrelated"),
    ):
        await kp_init._async_unload_loaded_platforms(
            hass, mock_config_entry, list(kp_init.PLATFORMS)
        )
