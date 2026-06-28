"""Wave-5 coverage tests for sensor setup and calculated sensor edge paths."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.sensor import SensorStateClass
from homeassistant.helpers.device_registry import DeviceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry


def _sensor_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain="kostal_plenticore",
        title="sensor-wave5",
        data={"host": "10.0.0.11", "password": "pw"},
    )


def _sensor_device(identifier: str) -> DeviceInfo:
    return DeviceInfo(identifiers={("kostal_kore", identifier)})


@pytest.mark.asyncio
async def test_sensor_setup_uses_modbus_dc_count_and_adds_optional_groups(hass) -> None:
    import kostal_plenticore.sensor as sensor_mod
    from kostal_plenticore.const import DOMAIN
    from kostal_plenticore.sensor import PreferredGridPowerSensor

    entry = _sensor_entry()
    entry.runtime_data = SimpleNamespace(
        client=SimpleNamespace(
            get_process_data=AsyncMock(return_value={}),
            get_setting_values=AsyncMock(side_effect=AssertionError("REST string count should not run")),
        ),
        device_info=_sensor_device("sensor-wave5-setup"),
    )

    fake_process = MagicMock()
    fake_process.data = {}
    fake_process.start_fetch_data = MagicMock()
    fake_process.stop_fetch_data = MagicMock()
    fake_process.async_add_listener = MagicMock(return_value=MagicMock())

    modbus_coordinator = MagicMock()
    modbus_coordinator.data = {}
    modbus_coordinator.device_info_data = {"num_pv_strings": "3"}
    ksem_coordinator = MagicMock()
    ksem_coordinator.data = {}
    ksem_coordinator.async_add_listener = MagicMock(return_value=MagicMock())
    event_coordinator = MagicMock()
    event_coordinator.data = {}

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "event_coordinator": event_coordinator,
        "modbus_coordinator": modbus_coordinator,
        "ksem_coordinator": ksem_coordinator,
        "health_monitor": object(),
        "fire_safety": object(),
        "diagnostics_engine": object(),
        "degradation_tracker": object(),
        "longevity_advisor": object(),
    }

    added: list[object] = []
    with (
        patch.object(sensor_mod, "SENSOR_PROCESS_DATA", []),
        patch.object(sensor_mod, "MODBUS_DIAGNOSTIC_SENSORS", (("modbus_key", "Modbus Key", None),)),
        patch.object(sensor_mod, "KSEM_DIAGNOSTIC_SENSORS", (("ksem_key", "Ksem Key", None),)),
        patch.object(sensor_mod, "generate_dc_sensor_descriptions", return_value=[]),
        patch.object(sensor_mod, "generate_pv_energy_sensor_descriptions", return_value=[]),
        patch.object(sensor_mod, "ProcessDataUpdateCoordinator", return_value=fake_process) as process_ctor,
        patch("kostal_plenticore.health_sensor.create_health_sensors", return_value=[MagicMock()]),
        patch("kostal_plenticore.fire_safety_entities.create_fire_safety_sensors", return_value=[MagicMock()]),
        patch("kostal_plenticore.diagnostic_entities.create_diagnostic_sensors", return_value=[MagicMock()]),
        patch("kostal_plenticore.degradation_entities.create_degradation_sensors", return_value=[MagicMock()]),
        patch("kostal_plenticore.longevity_entities.create_longevity_sensors", return_value=[MagicMock()]),
    ):
        await sensor_mod.async_setup_entry(hass, entry, added.extend)

    process_ctor.assert_called_once()
    assert process_ctor.call_args.args[4].seconds == sensor_mod.REST_PROCESS_POLL_SECONDS_WITH_MODBUS
    assert any(isinstance(entity, PreferredGridPowerSensor) for entity in added)
    assert len(added) == 12


@pytest.mark.asyncio
async def test_sensor_setup_invalid_modbus_string_count_uses_safe_default(hass) -> None:
    import kostal_plenticore.sensor as sensor_mod
    from kostal_plenticore.const import DOMAIN

    entry = _sensor_entry()
    entry.runtime_data = SimpleNamespace(
        client=SimpleNamespace(
            get_process_data=AsyncMock(return_value={}),
            get_setting_values=AsyncMock(
                return_value={"devices:local": {"Properties:StringCnt": "0"}}
            ),
        ),
        device_info=_sensor_device("sensor-wave5-invalid-count"),
    )

    fake_process = MagicMock()
    fake_process.data = {}
    fake_process.start_fetch_data = MagicMock()
    fake_process.stop_fetch_data = MagicMock()
    fake_process.async_add_listener = MagicMock(return_value=MagicMock())

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "modbus_coordinator": MagicMock(
            data={},
            device_info_data={"num_pv_strings": "bad"},
        )
    }

    with (
        patch.object(sensor_mod, "SENSOR_PROCESS_DATA", []),
        patch.object(sensor_mod, "generate_dc_sensor_descriptions", return_value=[]) as gen_dc,
        patch.object(sensor_mod, "ProcessDataUpdateCoordinator", return_value=fake_process),
    ):
        await sensor_mod.async_setup_entry(hass, entry, lambda _entities: None)

    gen_dc.assert_called_once_with(2)


@pytest.mark.asyncio
async def test_sensor_setup_optional_groups_allow_empty_factories(hass) -> None:
    import kostal_plenticore.sensor as sensor_mod
    from kostal_plenticore.const import DOMAIN

    entry = _sensor_entry()
    entry.runtime_data = SimpleNamespace(
        client=SimpleNamespace(
            get_process_data=AsyncMock(return_value={}),
            get_setting_values=AsyncMock(
                return_value={"devices:local": {"Properties:StringCnt": "2"}}
            ),
        ),
        device_info=_sensor_device("sensor-wave5-empty-factories"),
    )

    fake_process = MagicMock()
    fake_process.data = {}
    fake_process.start_fetch_data = MagicMock()
    fake_process.stop_fetch_data = MagicMock()
    fake_process.async_add_listener = MagicMock(return_value=MagicMock())

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "modbus_coordinator": MagicMock(data={}, device_info_data={"num_pv_strings": None}),
        "health_monitor": object(),
        "fire_safety": object(),
        "diagnostics_engine": object(),
        "degradation_tracker": object(),
        "longevity_advisor": object(),
    }

    with (
        patch.object(sensor_mod, "SENSOR_PROCESS_DATA", []),
        patch.object(sensor_mod, "generate_dc_sensor_descriptions", return_value=[]),
        patch.object(sensor_mod, "ProcessDataUpdateCoordinator", return_value=fake_process),
        patch("kostal_plenticore.health_sensor.create_health_sensors", return_value=[]),
        patch("kostal_plenticore.fire_safety_entities.create_fire_safety_sensors", return_value=[]),
        patch("kostal_plenticore.diagnostic_entities.create_diagnostic_sensors", return_value=[]),
        patch("kostal_plenticore.degradation_entities.create_degradation_sensors", return_value=[]),
        patch("kostal_plenticore.longevity_entities.create_longevity_sensors", return_value=[]),
    ):
        await sensor_mod.async_setup_entry(hass, entry, lambda _entities: None)


def _calc_desc(sensor_mod, key: str, module_id: str = "_calc_"):
    return sensor_mod.PlenticoreSensorEntityDescription(
        key=key,
        name=key,
        module_id=module_id,
        formatter="format_round",
    )


@pytest.mark.asyncio
async def test_sensor_calculated_and_virtual_sensor_paths() -> None:
    import kostal_plenticore.sensor as sensor_mod

    calc_data = {
        "scb:statistic:EnergyFlow": {
            "Statistic:EnergyHomeBat:Total": "2",
            "Statistic:EnergyDischargeGrid:Total": "1",
            "Statistic:EnergyChargeGrid:Total": "3",
            "Statistic:EnergyChargePv:Total": "2",
            "Statistic:EnergyDischarge:Total": "4",
        }
    }
    coordinator = MagicMock()
    coordinator.data = calc_data
    coordinator.start_fetch_data = MagicMock()
    coordinator.stop_fetch_data = MagicMock()

    with patch.object(sensor_mod, "_sensor_translation_key", return_value=None):
        discharge_total = sensor_mod.PlenticoreCalculatedSensor(
            coordinator,
            _calc_desc(sensor_mod, "BatteryDischargeTotal:Total"),
            "entry",
            "sensor",
            _sensor_device("calc-discharge-total"),
        )
        charge_total = sensor_mod.PlenticoreCalculatedSensor(
            coordinator,
            _calc_desc(sensor_mod, "BatteryChargeTotal:Total"),
            "entry",
            "sensor",
            _sensor_device("calc-charge-total"),
        )
        battery_eff = sensor_mod.PlenticoreCalculatedSensor(
            coordinator,
            _calc_desc(sensor_mod, "BatteryEfficiency:Total"),
            "entry",
            "sensor",
            _sensor_device("calc-battery-eff"),
        )
        net_eff = sensor_mod.PlenticoreCalculatedSensor(
            coordinator,
            _calc_desc(sensor_mod, "BatteryNetEfficiency:Total"),
            "entry",
            "sensor",
            _sensor_device("calc-net-eff"),
        )
        inverter_eff = sensor_mod.PlenticoreCalculatedSensor(
            coordinator,
            _calc_desc(sensor_mod, "InverterDischargeEfficiency:Total"),
            "entry",
            "sensor",
            _sensor_device("calc-inverter-eff"),
        )

    assert discharge_total.native_value == 3
    assert charge_total.native_value == 5
    assert battery_eff.native_value == 80
    assert net_eff.native_value == 60
    assert inverter_eff.native_value == 75
    assert battery_eff.extra_state_attributes["measurement_quality"] == "mixed"
    assert net_eff.extra_state_attributes["measurement_quality"] == "mixed"

    broken_coordinator = MagicMock()
    broken_coordinator.data = {
        "scb:statistic:EnergyFlow": {
            "Statistic:EnergyChargeGrid:Total": "bad",
            "Statistic:EnergyChargePv:Total": "2",
        }
    }
    broken_calc = sensor_mod.PlenticoreCalculatedSensor(
        broken_coordinator,
        _calc_desc(sensor_mod, "BatteryChargeTotal:Total"),
        "entry",
        "sensor",
        _sensor_device("calc-broken"),
    )
    assert broken_calc.native_value is None

    with (
        patch.object(sensor_mod.CoordinatorEntity, "async_added_to_hass", AsyncMock()),
        patch.object(sensor_mod.CoordinatorEntity, "async_will_remove_from_hass", AsyncMock()),
    ):
        await net_eff.async_added_to_hass()
        await net_eff.async_will_remove_from_hass()

    assert coordinator.start_fetch_data.call_count == 4
    assert coordinator.stop_fetch_data.call_count == 4

    no_period = sensor_mod.PlenticoreCalculatedSensor(
        coordinator,
        _calc_desc(sensor_mod, "BatteryEfficiency"),
        "entry",
        "sensor",
        _sensor_device("calc-no-period"),
    )
    with (
        patch.object(sensor_mod.CoordinatorEntity, "async_added_to_hass", AsyncMock()),
        patch.object(sensor_mod.CoordinatorEntity, "async_will_remove_from_hass", AsyncMock()),
    ):
        await no_period.async_added_to_hass()
        await no_period.async_will_remove_from_hass()

    pv_coordinator = MagicMock()
    pv_coordinator.data = {
        "devices:local:pv1": {"P": "1.5"},
        "devices:local:pv2": {"P": "bad"},
        "devices:local:pv3": {"P": None},
    }
    pv_coordinator.start_fetch_data = MagicMock()
    pv_coordinator.stop_fetch_data = MagicMock()

    with patch.object(sensor_mod, "_sensor_translation_key", return_value=None):
        pv_sensor = sensor_mod.CalculatedPvSumSensor(
            pv_coordinator,
            _calc_desc(sensor_mod, "pv_P", module_id="_virt_"),
            "entry",
            "sensor",
            _sensor_device("pv-sum"),
            dc_string_count=3,
        )

    assert pv_sensor.native_value == 2
    assert pv_sensor.available is True

    pv_coordinator.data = {"devices:local:pv1": {"V": "100"}}
    assert pv_sensor.native_value is None
    assert pv_sensor.available is False

    pv_coordinator.data = None
    assert pv_sensor.native_value is None
    assert pv_sensor.available is False

    pv_coordinator.data = {"devices:local:pv1": {"P": "1.5"}}
    with (
        patch.object(sensor_mod.CoordinatorEntity, "async_added_to_hass", AsyncMock()),
        patch.object(sensor_mod.CoordinatorEntity, "async_will_remove_from_hass", AsyncMock()),
    ):
        await pv_sensor.async_added_to_hass()
        await pv_sensor.async_will_remove_from_hass()

    assert pv_coordinator.start_fetch_data.call_count == 3
    assert pv_coordinator.stop_fetch_data.call_count == 3


@pytest.mark.asyncio
async def test_sensor_event_diagnostic_preferred_grid_and_data_sensor_paths() -> None:
    import kostal_plenticore.sensor as sensor_mod

    event_coord = MagicMock()
    event_coord.data = None
    event_sensor = sensor_mod.PlenticoreEventSensor(
        event_coord,
        "entry",
        _sensor_device("event"),
        key="active_error_events_count",
        name="Active Error Events",
        icon="mdi:alert",
    )
    assert event_sensor.available is False
    assert event_sensor.native_value is None
    event_coord.data = {"active_error_events_count": 4}
    assert event_sensor.native_value == 4

    modbus_coord = MagicMock()
    modbus_coord.data = {}
    modbus_coord.device_info_data = {"device_info_key": "serial-x", "modbus_key": 9}
    modbus_sensor = sensor_mod.ModbusDiagnosticSensor(
        modbus_coord,
        "entry",
        _sensor_device("modbus-diag"),
        key="modbus_key",
        name="Modbus Key",
        unit="W",
    )
    modbus_coord.data = None
    modbus_coord.device_info_data = {}
    assert modbus_sensor.native_value is None

    modbus_coord.data = {}
    modbus_coord.device_info_data = {"device_info_key": "serial-x", "modbus_key": 9}
    assert modbus_sensor.native_value == 9
    modbus_coord.data = {"modbus_key": 11}
    assert modbus_sensor.native_value == 11
    modbus_coord.data = {}
    modbus_coord.device_info_data = {}
    assert modbus_sensor.native_value is None

    ksem_coord = MagicMock()
    ksem_coord.data = None
    ksem_sensor = sensor_mod.KsemDiagnosticSensor(
        ksem_coord,
        "entry",
        _sensor_device("ksem-diag"),
        key="net_active_power_w",
        name="Net Active Power",
        unit="W",
    )
    assert ksem_sensor.native_value is None
    ksem_coord.data = {"net_active_power_w": 12.3}
    assert ksem_sensor.native_value == 12.3

    process_coord = MagicMock()
    process_coord.data = {"devices:local": {"Grid_P": "-80"}}
    process_coord.async_add_listener = MagicMock(return_value=MagicMock())

    ksem_pref = MagicMock()
    ksem_pref.data = {"net_active_power_w": "100"}
    ksem_pref.async_add_listener = MagicMock(return_value=MagicMock())

    modbus_pref = MagicMock()
    modbus_pref.data = {"pm_total_active": "1500"}
    modbus_pref.async_add_listener = MagicMock(return_value=MagicMock())

    pref = sensor_mod.PreferredGridPowerSensor(
        ksem_pref,
        "entry",
        _sensor_device("preferred-grid"),
        modbus_pref,
        process_coord,
    )

    with (
        patch.object(sensor_mod.CoordinatorEntity, "async_added_to_hass", AsyncMock()),
        patch.object(sensor_mod.CoordinatorEntity, "async_will_remove_from_hass", AsyncMock()),
        patch.object(pref, "async_write_ha_state"),
    ):
        await pref.async_added_to_hass()
        pref._async_external_update()
        await pref.async_will_remove_from_hass()

    assert pref.available is True
    assert pref.native_value == 100
    assert pref.extra_state_attributes["source"] == "ksem"
    assert pref.extra_state_attributes["source_conflict"] is True
    assert pref.extra_state_attributes["source_confidence"] == "low"

    ksem_pref.data = {}
    modbus_pref.data = {"pm_total_active": "250.7"}
    assert pref.native_value == 251
    assert pref.extra_state_attributes["source"] == "modbus_powermeter"

    modbus_pref.data = {}
    process_coord.data = {"devices:local": {"Grid_P": "-80.2"}}
    assert pref.native_value == -80
    assert pref.extra_state_attributes["source"] == "rest_grid_p"

    process_coord.data = {}
    assert pref.available is False
    assert pref.native_value is None
    assert pref.extra_state_attributes["source"] == "none"

    ksem_pref.data = {"net_active_power_w": "90"}
    modbus_pref.data = {"pm_total_active": "120"}
    process_coord.data = {"devices:local": {"Grid_P": "-10"}}
    assert pref.extra_state_attributes["source_conflict"] is False

    bad_process = MagicMock()
    bad_process.data = {"devices:local": {"Grid_P": "bad"}}
    bad_process.async_add_listener = MagicMock(return_value=MagicMock())
    bad_pref = sensor_mod.PreferredGridPowerSensor(
        MagicMock(data={"net_active_power_w": "bad"}, async_add_listener=MagicMock(return_value=MagicMock())),
        "entry",
        _sensor_device("preferred-grid-bad"),
        MagicMock(data={"pm_total_active": "bad"}, async_add_listener=MagicMock(return_value=MagicMock())),
        bad_process,
    )
    assert bad_pref.available is False
    assert bad_pref.native_value is None

    no_modbus_pref = sensor_mod.PreferredGridPowerSensor(
        MagicMock(data={"net_active_power_w": "10"}, async_add_listener=MagicMock(return_value=MagicMock())),
        "entry",
        _sensor_device("preferred-grid-no-modbus"),
        None,
        process_coord,
    )
    with (
        patch.object(sensor_mod.CoordinatorEntity, "async_added_to_hass", AsyncMock()),
        patch.object(sensor_mod.CoordinatorEntity, "async_will_remove_from_hass", AsyncMock()),
    ):
        await no_modbus_pref.async_added_to_hass()
        await no_modbus_pref.async_will_remove_from_hass()

    no_process_listener_pref = sensor_mod.PreferredGridPowerSensor(
        MagicMock(data={"net_active_power_w": "10"}, async_add_listener=MagicMock(return_value=MagicMock())),
        "entry",
        _sensor_device("preferred-grid-no-process-listener"),
        None,
        process_coord,
    )
    no_process_listener_pref._remove_process_listener = None
    with patch.object(sensor_mod.CoordinatorEntity, "async_will_remove_from_hass", AsyncMock()):
        await no_process_listener_pref.async_will_remove_from_hass()

    missing_source_pref = sensor_mod.PreferredGridPowerSensor(
        MagicMock(data={}, async_add_listener=MagicMock(return_value=MagicMock())),
        "entry",
        _sensor_device("preferred-grid-missing-sources"),
        MagicMock(data={}, async_add_listener=MagicMock(return_value=MagicMock())),
        MagicMock(data={"devices:local": {}}, async_add_listener=MagicMock(return_value=MagicMock())),
    )
    assert missing_source_pref.available is False
    assert missing_source_pref.native_value is None

    none_value_pref = sensor_mod.PreferredGridPowerSensor(
        MagicMock(
            data={"some_other_key": 1},
            async_add_listener=MagicMock(return_value=MagicMock()),
        ),
        "entry",
        _sensor_device("preferred-grid-none-values"),
        MagicMock(
            data={"another_key": 2},
            async_add_listener=MagicMock(return_value=MagicMock()),
        ),
        MagicMock(
            data={"devices:local": {}},
            async_add_listener=MagicMock(return_value=MagicMock()),
        ),
    )
    assert none_value_pref.available is False

    data_coord = MagicMock()
    data_coord.data = {"devices:local": {"total_yield": -5}}
    data_coord.start_fetch_data = MagicMock()
    data_coord.stop_fetch_data = MagicMock()

    data_desc = sensor_mod.PlenticoreSensorEntityDescription(
        key="total_yield",
        name="Total Yield",
        module_id="devices:local",
        formatter="format_round",
        state_class=SensorStateClass.TOTAL_INCREASING,
    )

    with patch.object(sensor_mod, "_sensor_translation_key", return_value=None):
        data_sensor = sensor_mod.PlenticoreDataSensor(
            data_coord,
            data_desc,
            "entry",
            "sensor",
            _sensor_device("data-sensor"),
        )

    with (
        patch.object(sensor_mod.CoordinatorEntity, "async_added_to_hass", AsyncMock()),
        patch.object(sensor_mod.CoordinatorEntity, "async_will_remove_from_hass", AsyncMock()),
    ):
        await data_sensor.async_added_to_hass()
        await data_sensor.async_will_remove_from_hass()

    assert data_sensor.available is True
    assert data_sensor.native_value == 0.0
    data_coord.start_fetch_data.assert_called_once_with("devices:local", "total_yield")
    data_coord.stop_fetch_data.assert_called_once_with("devices:local", "total_yield")

    data_coord.data = {}
    assert data_sensor.native_value is None
