"""Targeted Wave-3 coverage for MQTT bridge and switch platform edge paths."""

from __future__ import annotations

import sys
import asyncio
import time
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pykoplenti import SettingsData

from homeassistant.helpers.device_registry import DeviceInfo

from pytest_homeassistant_custom_component.common import MockConfigEntry


def _mock_mqtt_module() -> MagicMock:
    mock = MagicMock(spec=ModuleType)
    mock.async_publish = AsyncMock()
    mock.async_subscribe = AsyncMock(return_value=MagicMock())
    return mock


def _mock_mqtt_hass(mqtt_available: bool = True) -> MagicMock:
    hass = MagicMock()
    hass.config.components = {"mqtt"} if mqtt_available else set()
    hass.async_create_task = MagicMock(return_value=MagicMock())
    return hass


def _mock_modbus_coordinator() -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = {
        "total_dc_power": 4500.0,
        "battery_soc": 72,
        "pm_total_active": 123.4,
        "battery_cd_power": -500.0,
        "home_from_pv": 3500.0,
    }
    coordinator.async_add_listener = MagicMock(return_value=MagicMock())
    coordinator.async_write_register = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


def _fake_switch_plenticore(string_count: str = "2") -> SimpleNamespace:
    from kostal_plenticore.const_ids import ModuleId, SettingId

    return SimpleNamespace(
        device_info=DeviceInfo(identifiers={("kostal_kore", "switch-test")}),
        available_modules=[ModuleId.DEVICES_LOCAL],
        async_get_settings_cached=AsyncMock(
            return_value={
                ModuleId.DEVICES_LOCAL: [
                    SettingsData(
                        min=None,
                        max=None,
                        default=None,
                        access="readwrite",
                        unit=None,
                        id="Battery:ManualCharge",
                        type="bool",
                    ),
                ]
            }
        ),
        client=SimpleNamespace(
            get_setting_values=AsyncMock(
                return_value={ModuleId.DEVICES_LOCAL: {SettingId.STRING_COUNT: string_count}}
            )
        ),
        is_advanced_write_armed=False,
        advanced_write_arm_seconds_left=0,
        arm_advanced_writes=MagicMock(),
        disarm_advanced_writes=MagicMock(),
    )


def _device_info(identifier: str) -> DeviceInfo:
    return DeviceInfo(identifiers={("kostal_kore", identifier)})


@pytest.mark.asyncio
async def test_mqtt_bridge_stop_and_publish_edge_paths() -> None:
    from kostal_plenticore.mqtt_bridge import KostalMqttBridge, TOPIC_PREFIX

    hass = _mock_mqtt_hass()
    coordinator = _mock_modbus_coordinator()
    bridge = KostalMqttBridge(hass, coordinator, "INV123")
    bridge._started = True
    unsub = MagicMock()
    bridge._unsub_command = [unsub]
    mock_mqtt = _mock_mqtt_module()

    with patch.dict(sys.modules, {"homeassistant.components.mqtt": mock_mqtt}):
        await bridge.async_stop()

        unsub.assert_called_once()
        mock_mqtt.async_publish.assert_awaited_once_with(
            hass,
            f"{TOPIC_PREFIX}/INV123/modbus/available",
            "offline",
            1,
            retain=True,
        )
        assert bridge._started is False

    hass = _mock_mqtt_hass(mqtt_available=False)
    coordinator = _mock_modbus_coordinator()
    bridge = KostalMqttBridge(hass, coordinator, "INV124")
    bridge._started = True
    unsub_coordinator = MagicMock()
    bridge._unsub_coordinator = unsub_coordinator
    await bridge.async_stop()
    unsub_coordinator.assert_called_once()


@pytest.mark.asyncio
async def test_mqtt_bridge_publish_and_formatting_edge_paths() -> None:
    from kostal_plenticore.mqtt_bridge import KostalMqttBridge, TOPIC_PREFIX

    hass = _mock_mqtt_hass()
    coordinator = _mock_modbus_coordinator()
    bridge = KostalMqttBridge(hass, coordinator, "INV123")
    mock_mqtt = _mock_mqtt_module()

    class _Unserializable:
        pass

    with patch.dict(sys.modules, {"homeassistant.components.mqtt": mock_mqtt}):
        await bridge._publish_data({"custom": _Unserializable(), "battery_soc": 72})
        payloads = [call.args[2] for call in mock_mqtt.async_publish.await_args_list]
        await bridge._publish_proxy_topics({"inverter_state": "not-an-int"})
        assert any(
            call.args[1] == f"{TOPIC_PREFIX}/INV123/proxy/inverter_state"
            and call.args[2] == "not-an-int"
            for call in mock_mqtt.async_publish.await_args_list
        )
        mock_mqtt.async_publish.reset_mock()
        await bridge._publish_proxy_topics(
            {
                "total_dc_power": 5000,
                "home_from_pv": 1000,
                "home_from_battery": None,
                "home_from_grid": 500,
            }
        )
        partial_home = [
            c
            for c in mock_mqtt.async_publish.await_args_list
            if c.args[1] == f"{TOPIC_PREFIX}/INV123/proxy/home_power"
        ]
        assert not partial_home, "home_power must not publish when a register is missing"

        mock_mqtt.async_publish.reset_mock()
        await bridge._publish_proxy_topics(
            {
                "total_dc_power": 5000,
                "home_from_pv": 1000,
                "home_from_battery": 200,
                "home_from_grid": 500,
            }
        )
        full_home = [
            c
            for c in mock_mqtt.async_publish.await_args_list
            if c.args[1] == f"{TOPIC_PREFIX}/INV123/proxy/home_power"
        ]
        assert full_home, "home_power should publish when all three registers exist"
        publish_args = mock_mqtt.async_publish.await_args_list
        pv_dc = [
            c
            for c in publish_args
            if c.args[1] == f"{TOPIC_PREFIX}/INV123/proxy/pv_power_dc"
        ]
        pv_legacy = [
            c
            for c in publish_args
            if c.args[1] == f"{TOPIC_PREFIX}/INV123/proxy/pv_power"
        ]
        assert pv_dc and pv_legacy
        assert pv_dc[0].args[2] == pv_legacy[0].args[2] == "5000.0"
        pv_ac = [
            c
            for c in publish_args
            if c.args[1] == f"{TOPIC_PREFIX}/INV123/proxy/pv_power_ac_est"
        ]
        assert pv_ac and pv_ac[0].args[2] == "4800.0"
        await bridge._publish_register_metadata()

    assert any("custom" in str(payload) for payload in payloads)
    assert any(
        call.args[1] == f"{TOPIC_PREFIX}/INV123/modbus/config"
        for call in mock_mqtt.async_publish.await_args_list
    )
    hass = _mock_mqtt_hass(mqtt_available=False)
    bridge = KostalMqttBridge(hass, coordinator, "INV123")
    await bridge._publish_proxy_topics({"battery_soc": 50})
    await bridge._publish_register_metadata()

    assert bridge._fmt(object()).startswith("<")


@pytest.mark.asyncio
async def test_mqtt_bridge_command_validation_and_failure_paths() -> None:
    from kostal_plenticore.modbus_registers import (
        REG_ACTIVE_POWER_SETPOINT,
        REG_BAT_MIN_SOC,
        REG_MODBUS_ENABLE,
    )
    from kostal_plenticore.mqtt_bridge import KostalMqttBridge, TOPIC_PREFIX

    hass = _mock_mqtt_hass()
    coordinator = _mock_modbus_coordinator()
    bridge = KostalMqttBridge(hass, coordinator, "INV123", installer_access=True)

    msg = MagicMock()
    msg.topic = "x"
    msg.payload = "1"
    await bridge._handle_proxy_command(msg)
    coordinator.async_write_register.assert_not_called()

    msg = MagicMock()
    msg.topic = f"{TOPIC_PREFIX}/INV123/modbus/command/{REG_MODBUS_ENABLE.name}"
    msg.payload = "1"
    await bridge._handle_command(msg)
    coordinator.async_write_register.assert_not_called()

    soc_controller = SimpleNamespace(active=True, target_soc=55)
    bridge = KostalMqttBridge(
        hass, coordinator, "INV123", installer_access=True, soc_controller=soc_controller
    )
    await bridge._execute_write(REG_BAT_MIN_SOC, "40", source="test")
    coordinator.async_write_register.assert_not_called()

    bridge = KostalMqttBridge(hass, coordinator, "INV123", installer_access=True)
    await bridge._execute_write(REG_ACTIVE_POWER_SETPOINT, "not-a-number", source="test")
    coordinator.async_write_register.assert_not_called()

    bridge._last_write[REG_ACTIVE_POWER_SETPOINT.name] = time.monotonic()
    await bridge._execute_write(REG_ACTIVE_POWER_SETPOINT, "80", source="test")
    coordinator.async_write_register.assert_not_called()

    coordinator.async_write_register.side_effect = RuntimeError("write failed")
    bridge._last_write.clear()
    await bridge._execute_write(REG_ACTIVE_POWER_SETPOINT, "80", source="test")
    coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_advanced_write_arm_switch_listener_lifecycle(hass) -> None:
    from kostal_plenticore.switch import AdvancedWriteArmSwitch, _fetch_switch_settings

    plenticore = SimpleNamespace(
        is_advanced_write_armed=False,
        advanced_write_arm_seconds_left=30,
    )

    def _arm() -> None:
        plenticore.is_advanced_write_armed = True
        plenticore.advanced_write_arm_seconds_left = 30

    def _disarm() -> None:
        plenticore.is_advanced_write_armed = False
        plenticore.advanced_write_arm_seconds_left = 0

    plenticore.arm_advanced_writes = MagicMock(side_effect=_arm)
    plenticore.disarm_advanced_writes = MagicMock(side_effect=_disarm)

    timeout_plenticore = SimpleNamespace(async_get_settings_cached=AsyncMock(side_effect=asyncio.TimeoutError()))
    assert await _fetch_switch_settings(timeout_plenticore) == {}

    entity = AdvancedWriteArmSwitch(
        plenticore=plenticore,
        entry_id="entry-1",
        device_info=DeviceInfo(identifiers={("kostal_kore", "switch-arm")}),
    )
    entity.hass = hass

    old_listener = MagicMock()
    new_listener = MagicMock()
    entity._remove_expire_listener = old_listener

    with (
        patch("kostal_plenticore.switch.async_call_later", return_value=new_listener),
        patch.object(entity, "async_write_ha_state"),
    ):
        await entity.async_turn_on()

        old_listener.assert_called_once()
        assert entity._remove_expire_listener is new_listener

        await entity.async_turn_off()
        new_listener.assert_called_once()
        assert entity._remove_expire_listener is None

        remove_listener = MagicMock()
        entity._remove_expire_listener = remove_listener
        await entity.async_will_remove_from_hass()
        remove_listener.assert_called_once()

        entity._remove_expire_listener = None
        await entity.async_turn_on()
        await entity.async_turn_off()
        await entity.async_turn_off()


@pytest.mark.asyncio
async def test_switch_setup_handles_string_count_clamp_and_modbus_entity_errors(hass) -> None:
    from kostal_plenticore.const import CONF_MODBUS_ENABLED, DOMAIN
    from kostal_plenticore.switch import (
        AdvancedWriteArmSwitch,
        PlenticoreShadowMgmtSwitch,
        async_setup_entry,
    )

    entry = MockConfigEntry(
        domain="kostal_plenticore",
        title="switch-test",
        options={CONF_MODBUS_ENABLED: True},
    )
    entry.runtime_data = _fake_switch_plenticore(string_count="999")
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {"modbus_coordinator": object()}

    added: list[object] = []

    with (
        patch("kostal_plenticore.switch.ensure_installer_access", return_value=True),
        patch("kostal_plenticore.switch.string_feature_id", side_effect=RuntimeError("boom")),
        patch(
            "kostal_plenticore.charge_block_switch.BatteryChargeBlockSwitch",
            side_effect=RuntimeError("modbus boom"),
        ),
        patch("kostal_plenticore.switch.asyncio.sleep", AsyncMock()),
        patch("kostal_plenticore.switch._LOGGER.error"),
    ):
        await async_setup_entry(hass, entry, added.extend)

    assert any(isinstance(entity, AdvancedWriteArmSwitch) for entity in added)
    assert not any(isinstance(entity, PlenticoreShadowMgmtSwitch) for entity in added)


@pytest.mark.asyncio
async def test_switch_setup_handles_invalid_string_count_value(hass) -> None:
    from kostal_plenticore.switch import PlenticoreShadowMgmtSwitch, async_setup_entry

    entry = MockConfigEntry(domain="kostal_plenticore", title="switch-shadow-invalid-count")
    plenticore = _fake_switch_plenticore(string_count="not-a-number")
    entry.runtime_data = plenticore

    added: list[object] = []
    with patch("kostal_plenticore.switch.ensure_installer_access", return_value=True):
        await async_setup_entry(hass, entry, added.extend)

    assert not any(isinstance(entity, PlenticoreShadowMgmtSwitch) for entity in added)


@pytest.mark.asyncio
async def test_switch_setup_uses_individual_shadow_queries_after_batch_500(hass) -> None:
    from aiohttp import ClientError

    from kostal_plenticore.const_ids import ModuleId, SettingId, string_feature_id
    from kostal_plenticore.switch import PlenticoreShadowMgmtSwitch, async_setup_entry

    entry = MockConfigEntry(domain="kostal_plenticore", title="switch-shadow-fallback")
    plenticore = _fake_switch_plenticore(string_count="3")
    entry.runtime_data = plenticore

    feature0 = string_feature_id(0)
    feature1 = string_feature_id(1)
    feature2 = string_feature_id(2)

    async def _get_setting_values(module_id: str, data_id):
        if data_id == SettingId.STRING_COUNT:
            return {ModuleId.DEVICES_LOCAL: {SettingId.STRING_COUNT: "3"}}
        if data_id == (feature0, feature1, feature2):
            raise ClientError("Unknown API response [500]")
        if data_id == (feature0,):
            return {ModuleId.DEVICES_LOCAL: {feature0: "1"}}
        if data_id == (feature1,):
            return {}
        if data_id == (feature2,):
            raise ClientError("generic single-string failure")
        return {}

    plenticore.client.get_setting_values = AsyncMock(side_effect=_get_setting_values)

    added: list[object] = []
    with patch("kostal_plenticore.switch.ensure_installer_access", return_value=True):
        await async_setup_entry(hass, entry, added.extend)

    assert any(isinstance(entity, PlenticoreShadowMgmtSwitch) for entity in added)


@pytest.mark.asyncio
async def test_switch_entities_skip_refresh_when_write_returns_false() -> None:
    from kostal_plenticore.switch import (
        PlenticoreShadowMgmtSwitch,
        create_switch_description,
    )

    description = create_switch_description(
        "devices:local",
        "Battery:ManualCharge",
        "Battery Manual Charge",
        "1",
        "0",
    )

    coordinator = MagicMock()
    coordinator.data = {"devices:local": {"Battery:ManualCharge": "0", "Generator:ShadowMgmt:Enable": "0"}}
    coordinator.async_write_data = AsyncMock(return_value=False)
    coordinator.async_request_refresh = AsyncMock()

    from kostal_plenticore.switch import PlenticoreDataSwitch

    regular = PlenticoreDataSwitch(
        coordinator,
        description,
        "entry-1",
        "scb",
        DeviceInfo(identifiers={("kostal_kore", "switch-regular")}),
    )
    await regular.async_turn_on()
    await regular.async_turn_off()
    coordinator.async_request_refresh.assert_not_awaited()

    shadow = PlenticoreShadowMgmtSwitch(
        coordinator,
        0,
        "entry-1",
        "scb",
        DeviceInfo(identifiers={("kostal_kore", "switch-shadow")}),
    )
    await shadow.async_turn_on()
    await shadow.async_turn_off()
    coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_switch_entity_fetch_lifecycle_registration() -> None:
    from kostal_plenticore.switch import (
        CoordinatorEntity,
        PlenticoreDataSwitch,
        create_switch_description,
    )

    description = create_switch_description(
        "devices:local",
        "Battery:ManualCharge",
        "Battery Manual Charge",
        "1",
        "0",
    )

    coordinator = MagicMock()
    coordinator.data = {"devices:local": {"Battery:ManualCharge": "0"}}
    coordinator.start_fetch_data = MagicMock(return_value=MagicMock())
    coordinator.stop_fetch_data = MagicMock()

    entity = PlenticoreDataSwitch(
        coordinator,
        description,
        "entry-switch-lifecycle",
        "scb",
        DeviceInfo(identifiers={("kostal_kore", "switch-lifecycle")}),
    )
    entity.async_on_remove = MagicMock()

    with (
        patch.object(CoordinatorEntity, "async_added_to_hass", AsyncMock()),
        patch.object(CoordinatorEntity, "async_will_remove_from_hass", AsyncMock()),
    ):
        await entity.async_added_to_hass()
        await entity.async_will_remove_from_hass()

    coordinator.start_fetch_data.assert_called_once_with(
        "devices:local", "Battery:ManualCharge"
    )
    entity.async_on_remove.assert_called_once()
    coordinator.stop_fetch_data.assert_called_once_with(
        "devices:local", "Battery:ManualCharge"
    )


@pytest.mark.asyncio
async def test_number_setup_empty_settings_and_modbus_extensions(hass) -> None:
    from kostal_plenticore.const import CONF_MODBUS_ENABLED, DOMAIN
    import kostal_plenticore.number as number_module

    entry = MockConfigEntry(
        domain="kostal_plenticore",
        title="number-wave3",
        options={CONF_MODBUS_ENABLED: True},
    )
    entry.runtime_data = SimpleNamespace(
        available_modules=["devices:local"],
        device_info=_device_info("number-wave3"),
    )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "modbus_coordinator": object(),
        "soc_controller": object(),
        "grid_feedin_limiter": object(),
    }

    registry = MagicMock()
    added: list[object] = []
    fake_coordinator = MagicMock()
    fake_coordinator.data = {}
    fake_coordinator.config_entry = entry
    fake_coordinator.start_fetch_data = MagicMock()
    fake_coordinator.stop_fetch_data = MagicMock()

    with (
        patch.object(number_module, "_get_settings_data_safe", AsyncMock(side_effect=[{}, {}])),
        patch.object(number_module.asyncio, "sleep", AsyncMock()),
        patch.object(number_module, "SettingDataUpdateCoordinator", return_value=fake_coordinator),
        patch.object(number_module.er, "async_get", return_value=registry),
        patch.object(number_module.er, "async_entries_for_config_entry", return_value=[]),
        patch("kostal_plenticore.modbus_number.create_modbus_number_entities", AsyncMock(return_value=[MagicMock()])),
        patch("kostal_plenticore.soc_controller_entities.create_soc_controller_entities", return_value=[MagicMock()]),
        patch("kostal_plenticore.grid_charge_limiter.FeedInLimitNumber", return_value=MagicMock()),
        patch("kostal_plenticore.number.is_rest_write_supported_target", return_value=True),
    ):
        await number_module.async_setup_entry(hass, entry, added.extend)

    assert added


@pytest.mark.asyncio
async def test_number_entity_remaining_keepalive_and_non_battery_paths(hass) -> None:
    from kostal_plenticore.number import (
        PlenticoreDataNumber,
        PlenticoreNumberEntityDescription,
    )
    from kostal_plenticore.const_ids import SettingId

    entry = MockConfigEntry(domain="kostal_plenticore", title="number-entity")
    coordinator = MagicMock()
    coordinator.config_entry = entry
    coordinator.last_update_success = True
    coordinator.async_write_data = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    coordinator.start_fetch_data = MagicMock()
    coordinator.stop_fetch_data = MagicMock()

    g3_description = PlenticoreNumberEntityDescription(
        key="battery_max_charge_power_g3_wave3",
        name="Battery Max Charge Power (G3) Wave3",
        native_unit_of_measurement="W",
        native_max_value=50000,
        native_min_value=0,
        native_step=100,
        entity_category=None,
        entity_registry_enabled_default=True,
        icon=None,
        module_id="devices:local",
        data_id="Battery:MaxChargePowerG3",
        fmt_from="format_round",
        fmt_to="format_round_back",
    )
    non_battery_description = PlenticoreNumberEntityDescription(
        key="pave_enable_wave3",
        name="Pave Enable Wave3",
        native_unit_of_measurement="W",
        native_max_value=100,
        native_min_value=0,
        native_step=1,
        entity_category=None,
        entity_registry_enabled_default=True,
        icon=None,
        module_id="devices:local",
        data_id="Pave:Enable",
        fmt_from="format_round",
        fmt_to="format_round_back",
    )

    only_max_setting = SettingsData(
        min=None,
        max="100",
        default=None,
        access="readwrite",
        unit="W",
        id="Battery:MaxChargePowerG3",
        type="number",
    )
    full_setting = SettingsData(
        min="0",
        max=None,
        default=None,
        access="readwrite",
        unit="W",
        id="Pave:Enable",
        type="number",
    )

    g3_entity = PlenticoreDataNumber(
        coordinator,
        entry.entry_id,
        "scb",
        _device_info("number-g3"),
        g3_description,
        only_max_setting,
    )
    non_battery_entity = PlenticoreDataNumber(
        coordinator,
        entry.entry_id,
        "scb",
        _device_info("number-non-battery"),
        non_battery_description,
        full_setting,
    )

    g3_entity.hass = hass
    non_battery_entity.hass = hass

    coordinator.data = {}
    assert g3_entity._get_keepalive_interval() >= 1

    coordinator.data = {
        "devices:local": {SettingId.BATTERY_LIMIT_FALLBACK_TIME: "invalid"}
    }
    assert g3_entity._get_keepalive_interval() >= 1

    running_task = MagicMock()
    running_task.done.return_value = False
    g3_entity._keepalive_task = running_task
    g3_entity._start_keepalive(123.0)
    assert g3_entity._keepalive_task is running_task

    g3_entity._keepalive_value = None
    await g3_entity._run_keepalive()

    async def _successful_keepalive(module_id: str, payload: dict[str, str]) -> None:
        g3_entity._keepalive_value = None

    coordinator.async_write_data = AsyncMock(side_effect=_successful_keepalive)
    with (
        patch("kostal_plenticore.number.ensure_installer_access", return_value=True),
        patch("kostal_plenticore.number.asyncio.sleep", AsyncMock()),
    ):
        g3_entity._keepalive_task = None
        g3_entity._keepalive_value = 5000.0
        await g3_entity._run_keepalive()

    coordinator.async_write_data = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    await non_battery_entity.async_set_native_value(1.0)

    coordinator.async_write_data.assert_awaited_once_with(
        "devices:local", {"Pave:Enable": "1"}
    )
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_number_setup_nonempty_missing_settings_and_empty_modbus_entities(hass) -> None:
    from kostal_plenticore.const import CONF_MODBUS_ENABLED, DOMAIN
    import kostal_plenticore.number as number_module

    entry = MockConfigEntry(
        domain="kostal_plenticore",
        title="number-wave3-nonempty",
        options={CONF_MODBUS_ENABLED: True},
    )
    entry.runtime_data = SimpleNamespace(
        available_modules=["devices:local"],
        device_info=_device_info("number-wave3-nonempty"),
    )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "modbus_coordinator": object(),
        "soc_controller": object(),
    }

    fake_coordinator = MagicMock()
    fake_coordinator.data = {}
    fake_coordinator.config_entry = entry
    fake_coordinator.start_fetch_data = MagicMock()
    fake_coordinator.stop_fetch_data = MagicMock()

    available_settings = {
        "devices:local": [
            SettingsData(
                min=None,
                max=None,
                default=None,
                access="readwrite",
                unit=None,
                id="Battery:MinSocRel",
                type="number",
            ),
        ]
    }

    with (
        patch.object(number_module, "_get_settings_data_safe", AsyncMock(return_value=available_settings)),
        patch.object(number_module, "SettingDataUpdateCoordinator", return_value=fake_coordinator),
        patch.object(number_module.er, "async_get", return_value=MagicMock()),
        patch.object(number_module.er, "async_entries_for_config_entry", return_value=[]),
        patch("kostal_plenticore.modbus_number.create_modbus_number_entities", AsyncMock(return_value=[])),
        patch("kostal_plenticore.soc_controller_entities.create_soc_controller_entities", return_value=[]),
        patch("kostal_plenticore.number.is_rest_write_supported_target", return_value=True),
    ):
        await number_module.async_setup_entry(hass, entry, lambda entities: None)

    assert fake_coordinator.start_fetch_data.called


@pytest.mark.asyncio
async def test_number_setup_truthy_unrelated_settings_data_silently_skips(hass) -> None:
    import kostal_plenticore.number as number_module

    entry = MockConfigEntry(domain="kostal_plenticore", title="number-wave3-unrelated")
    entry.runtime_data = SimpleNamespace(
        available_modules=["devices:local"],
        device_info=_device_info("number-wave3-unrelated"),
    )

    fake_coordinator = MagicMock()
    fake_coordinator.data = {}
    fake_coordinator.config_entry = entry
    fake_coordinator.start_fetch_data = MagicMock()
    fake_coordinator.stop_fetch_data = MagicMock()

    unrelated_settings = {
        "scb:network": [
            SettingsData(
                min=None,
                max=None,
                default=None,
                access="readwrite",
                unit=None,
                id="Network:Hostname",
                type="string",
            ),
        ]
    }

    added: list[object] = []
    with (
        patch.object(
            number_module,
            "_get_settings_data_safe",
            AsyncMock(return_value=unrelated_settings),
        ),
        patch.object(number_module, "SettingDataUpdateCoordinator", return_value=fake_coordinator),
        patch.object(number_module.er, "async_get", return_value=MagicMock()),
        patch.object(number_module.er, "async_entries_for_config_entry", return_value=[]),
        patch.object(number_module, "NUMBER_SETTINGS_DATA", [number_module.NUMBER_SETTINGS_DATA[2]]),
        patch("kostal_plenticore.number.is_rest_write_supported_target", return_value=True),
    ):
        await number_module.async_setup_entry(hass, entry, added.extend)

    assert added == []
    fake_coordinator.start_fetch_data.assert_not_called()


@pytest.mark.asyncio
async def test_number_setup_creates_non_forced_entity_without_forced_fetch(hass) -> None:
    import kostal_plenticore.number as number_module

    entry = MockConfigEntry(domain="kostal_plenticore", title="number-wave3-nonforced")
    entry.runtime_data = SimpleNamespace(
        available_modules=["devices:local"],
        device_info=_device_info("number-wave3-nonforced"),
    )

    fake_coordinator = MagicMock()
    fake_coordinator.data = {}
    fake_coordinator.config_entry = entry
    fake_coordinator.start_fetch_data = MagicMock()
    fake_coordinator.stop_fetch_data = MagicMock()

    non_forced_description = number_module.NUMBER_SETTINGS_DATA[2]
    available_settings = {
        non_forced_description.module_id: [
            SettingsData(
                min="0",
                max="5000",
                default=None,
                access="readwrite",
                unit="W",
                id=non_forced_description.data_id,
                type="number",
            ),
        ]
    }

    added: list[object] = []
    with (
        patch.object(
            number_module,
            "_get_settings_data_safe",
            AsyncMock(return_value=available_settings),
        ),
        patch.object(number_module, "SettingDataUpdateCoordinator", return_value=fake_coordinator),
        patch.object(number_module.er, "async_get", return_value=MagicMock()),
        patch.object(number_module.er, "async_entries_for_config_entry", return_value=[]),
        patch.object(number_module, "NUMBER_SETTINGS_DATA", [non_forced_description]),
        patch("kostal_plenticore.number.is_rest_write_supported_target", return_value=True),
    ):
        await number_module.async_setup_entry(hass, entry, added.extend)

    assert len(added) == 1
    fake_coordinator.start_fetch_data.assert_not_called()


def test_number_keepalive_interval_uses_valid_fallback_value() -> None:
    from kostal_plenticore.number import (
        G3_KEEPALIVE_MAX_SECONDS,
        G3_KEEPALIVE_MIN_SECONDS,
        PlenticoreDataNumber,
        PlenticoreNumberEntityDescription,
    )
    from kostal_plenticore.const_ids import SettingId

    coordinator = MagicMock()
    coordinator.config_entry = MockConfigEntry(domain="kostal_plenticore", title="number-keepalive")
    coordinator.last_update_success = True
    coordinator.data = {
        "devices:local": {SettingId.BATTERY_LIMIT_FALLBACK_TIME: "20"}
    }

    description = PlenticoreNumberEntityDescription(
        key="battery_max_charge_power_g3_interval",
        name="Battery Max Charge Power (G3) Interval",
        native_unit_of_measurement="W",
        native_max_value=50000,
        native_min_value=0,
        native_step=100,
        entity_category=None,
        entity_registry_enabled_default=True,
        icon=None,
        module_id="devices:local",
        data_id="Battery:MaxChargePowerG3",
        fmt_from="format_round",
        fmt_to="format_round_back",
    )

    entity = PlenticoreDataNumber(
        coordinator,
        "entry-keepalive",
        "scb",
        _device_info("number-keepalive"),
        description,
        None,
    )

    interval = entity._get_keepalive_interval()
    assert G3_KEEPALIVE_MIN_SECONDS <= interval <= G3_KEEPALIVE_MAX_SECONDS
