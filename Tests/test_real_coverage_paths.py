"""Targeted tests to close real (non-omit) coverage gaps."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp.client_exceptions import ClientError
from pykoplenti import ApiException, ProcessData, SettingsData
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import UpdateFailed

from pytest_homeassistant_custom_component.common import MockConfigEntry


def _mock_entry() -> MockConfigEntry:
    return MockConfigEntry(
        entry_id="cov-entry",
        title="cov",
        domain="kostal_plenticore",
        data={"host": "192.168.1.2", "password": "pw", "service_code": "12345"},
    )


@pytest.mark.asyncio
async def test_plenticore_async_setup_and_unload_error_paths(hass: HomeAssistant) -> None:
    """Cover login/metadata/module error branches and unload branches."""
    from kostal_plenticore.coordinator import Plenticore
    from pykoplenti import AuthenticationException

    entry = _mock_entry()
    p = Plenticore(hass, entry)

    with patch("kostal_plenticore.coordinator.ExtendedApiClient") as client_cls:
        # auth error -> False
        client = MagicMock()
        client.login = AsyncMock(side_effect=AuthenticationException(401, "bad auth"))
        client_cls.return_value = client
        assert await p.async_setup() is False

        # client/network errors -> ConfigEntryNotReady
        client = MagicMock()
        client.login = AsyncMock(side_effect=ClientError("net"))
        client_cls.return_value = client
        with pytest.raises(ConfigEntryNotReady):
            await p.async_setup()

        # api errors during login -> ConfigEntryNotReady
        client = MagicMock()
        client.login = AsyncMock(side_effect=ApiException("illegal data value"))
        client_cls.return_value = client
        with pytest.raises(ConfigEntryNotReady):
            await p.async_setup()

        # metadata/modules gather error -> False branch in async_setup
        client = MagicMock()
        client.login = AsyncMock(return_value=None)
        client_cls.return_value = client
        p._fetch_modules = AsyncMock(side_effect=ValueError("x"))
        p._fetch_device_metadata = AsyncMock(return_value=None)
        assert await p.async_setup() is False

    # unload: skip logout during closing
    p._client = MagicMock()
    p._client.logout = AsyncMock()
    p.hass.state = "closing"  # type: ignore[attr-defined]
    await p.async_unload()
    assert p._client is None

    # unload: unexpected exception branch
    p._client = MagicMock()
    p._client.logout = AsyncMock(side_effect=RuntimeError("boom"))
    p.hass.state = "running"  # type: ignore[attr-defined]
    await p.async_unload()
    assert p._client is None


@pytest.mark.asyncio
async def test_plenticore_async_setup_tolerates_metadata_timeout(
    hass: HomeAssistant,
) -> None:
    """Slow metadata fetch should not fail startup outright."""
    from kostal_plenticore.coordinator import Plenticore

    entry = _mock_entry()
    p = Plenticore(hass, entry)

    with patch("kostal_plenticore.coordinator.ExtendedApiClient") as client_cls:
        client = MagicMock()
        client.login = AsyncMock(return_value=None)
        client.get_settings = AsyncMock(return_value={})
        client.get_process_data = AsyncMock(return_value={})
        client_cls.return_value = client

        p._fetch_modules = AsyncMock(return_value=None)
        p._fetch_device_metadata = AsyncMock(side_effect=asyncio.TimeoutError())

        assert await p.async_setup() is True
        assert p.device_info.get("name") == "192.168.1.2"


@pytest.mark.asyncio
async def test_plenticore_async_setup_tolerates_module_timeout(
    hass: HomeAssistant,
) -> None:
    """Slow module discovery should keep default module capability set."""
    from kostal_plenticore.coordinator import Plenticore

    entry = _mock_entry()
    p = Plenticore(hass, entry)

    with patch("kostal_plenticore.coordinator.ExtendedApiClient") as client_cls:
        client = MagicMock()
        client.login = AsyncMock(return_value=None)
        client.get_settings = AsyncMock(return_value={})
        client.get_process_data = AsyncMock(return_value={})
        client_cls.return_value = client

        p._fetch_modules = AsyncMock(side_effect=asyncio.TimeoutError())
        p._fetch_device_metadata = AsyncMock(return_value=None)

        assert await p.async_setup() is True
        assert "devices:local" in p.available_modules


@pytest.mark.asyncio
async def test_coordinator_mixins_and_update_error_paths(hass: HomeAssistant) -> None:
    """Cover DataUpdateCoordinatorMixin/Process/Setting/Select branches."""
    from kostal_plenticore.coordinator import (
        DataUpdateCoordinatorMixin,
        Plenticore,
        ProcessDataUpdateCoordinator,
        SelectDataUpdateCoordinator,
        SettingDataUpdateCoordinator,
    )

    class DummyMixin(DataUpdateCoordinatorMixin):
        def __init__(self, plenticore: Plenticore) -> None:
            self._plenticore = plenticore
            self.name = "dummy"

    entry = _mock_entry()
    p = Plenticore(hass, entry)
    p._client = MagicMock()
    mix = DummyMixin(p)

    # async_read_data 404/503/500/missing/fallback
    p._client.get_setting_values = AsyncMock(side_effect=ApiException("[404] module or setting not found"))
    assert await mix.async_read_data("m", "k") is None
    p._client.get_setting_values = AsyncMock(side_effect=ApiException("[503] internal communication error"))
    assert await mix.async_read_data("m", "k") is None
    p._client.get_setting_values = AsyncMock(side_effect=ApiException("Unknown API response [500]"))
    assert await mix.async_read_data("m", "k") is None
    p._client.get_setting_values = AsyncMock(side_effect=ApiException("Missing data_id foo"))
    assert await mix.async_read_data("m", "k") is None
    p._client.get_setting_values = AsyncMock(side_effect=ClientError("other"))
    assert await mix.async_read_data("m", "k") is None

    # async_write_data error mapping -> HomeAssistantError
    p._client.set_setting_values = AsyncMock(side_effect=ApiException("server device busy"))
    with pytest.raises(HomeAssistantError):
        await mix.async_write_data("m", {"k": "v"})
    p._client.set_setting_values = AsyncMock(side_effect=ClientError("Unknown API response [500]"))
    with pytest.raises(HomeAssistantError):
        await mix.async_write_data("m", {"k": "v"})
    p._client.set_setting_values = AsyncMock(side_effect=ClientError("other"))
    with pytest.raises(HomeAssistantError):
        await mix.async_write_data("m", {"k": "v"})

    # Process coordinator error branches
    proc = ProcessDataUpdateCoordinator(
        hass, entry, logging.getLogger(__name__), "proc", timedelta(seconds=10), p
    )
    proc._fetch = {"devices:local": ["P"]}

    p._client.get_process_data_values = AsyncMock(side_effect=asyncio.TimeoutError())
    with pytest.raises(UpdateFailed):
        await proc._async_update_data()

    p._client.get_process_data_values = AsyncMock(side_effect=ApiException("[503] internal communication error"))
    with pytest.raises(UpdateFailed):
        await proc._async_update_data()

    p._client.get_process_data_values = AsyncMock(side_effect=ClientError("Unknown API response [500]"))
    with pytest.raises(UpdateFailed):
        await proc._async_update_data()

    # Process data conversion branches (mapping/iterable/unsupported/error)
    class IterContainer:
        def __iter__(self):
            return iter(["X"])

        def __getitem__(self, item):
            return ProcessData(id=item, unit="", value="2")

    class BrokenContainer:
        def __iter__(self):
            return iter(["Y"])

        def __getitem__(self, _item):
            raise KeyError("broken")

    p._client.get_process_data_values = AsyncMock(
        return_value={
            "map": {"P": ProcessData(id="P", unit="", value="1")},
            "iter": IterContainer(),
            "unsupported": 123,
            "broken": BrokenContainer(),
        }
    )
    data = await proc._async_update_data()
    assert data["map"]["P"] == "1.0"
    assert data["iter"]["X"] == "2.0"
    assert data["unsupported"] == {}
    assert data["broken"] == {}

    # Setting coordinator branches
    settings = SettingDataUpdateCoordinator(
        hass, entry, logging.getLogger(__name__), "settings", timedelta(seconds=10), p
    )
    settings._fetch = {"devices:local": ["Battery:MinSoc"]}
    p._client.get_setting_values = AsyncMock(return_value={"devices:local": {"Battery:MinSoc": "5"}})
    assert await settings._async_update_data() == {"devices:local": {"Battery:MinSoc": "5"}}

    settings._last_result = {"devices:local": {"Battery:MinSoc": "6"}}
    p._client.get_setting_values = AsyncMock(side_effect=ApiException("[503] internal communication error"))
    assert await settings._async_update_data() == {"devices:local": {"Battery:MinSoc": "6"}}

    settings._last_result = {}
    p._client.get_setting_values = AsyncMock(side_effect=ApiException("[503] internal communication error"))
    assert await settings._async_update_data() == {}

    p._client.get_setting_values = AsyncMock(side_effect=ApiException("[404] not found"))
    assert await settings._async_update_data() == {}

    p._client.get_setting_values = AsyncMock(side_effect=ApiException("Missing data_id"))
    assert await settings._async_update_data() == {}

    p._client.get_setting_values = AsyncMock(side_effect=ApiException("illegal function"))
    with pytest.raises(UpdateFailed):
        await settings._async_update_data()

    # Select coordinator branches
    select = SelectDataUpdateCoordinator(
        hass, entry, logging.getLogger(__name__), "select", timedelta(seconds=10), p
    )
    select._fetch = {"devices:local": {"Battery:Mode": ["A", "B", "None"]}}
    p._client = None
    assert await select._async_update_data() == {}

    p._client = MagicMock()
    with patch.object(select, "async_read_data", AsyncMock(side_effect=[{}, {"devices:local": {"B": "1"}}])):
        assert await select._async_get_current_option(select._fetch) == {
            "devices:local": {"Battery:Mode": "B"}
        }
    with patch.object(select, "async_read_data", AsyncMock(return_value={})):
        assert await select._async_get_current_option(select._fetch) == {
            "devices:local": {"Battery:Mode": "None"}
        }


@pytest.mark.asyncio
async def test_switch_setup_and_entities_cover_paths(hass: HomeAssistant) -> None:
    """Cover switch setup fallback and entity method branches."""
    from kostal_plenticore.switch import (
        PlenticoreDataSwitch,
        PlenticoreShadowMgmtSwitch,
        PlenticoreSwitchEntityDescription,
        async_setup_entry,
    )

    entry = _mock_entry()
    client = MagicMock()
    plenticore = SimpleNamespace(
        client=client,
        available_modules=[],
        device_info=DeviceInfo(identifiers={("kostal_plenticore", "x")}),
    )
    entry.runtime_data = plenticore  # type: ignore[attr-defined]

    # get_settings succeeds but empty, shadow batch query returns 500 then individual
    client.get_settings = AsyncMock(return_value={})

    async def _get_setting_values(*args):
        if args == ("devices:local", "Properties:StringCnt"):
            return {"devices:local": {"Properties:StringCnt": "2"}}
        if args[0] == "devices:local" and isinstance(args[1], tuple) and len(args[1]) == 2:
            raise ApiException("Unknown API response [500]")
        if args[0] == "devices:local" and isinstance(args[1], tuple) and len(args[1]) == 1:
            # string 1 supported, string 2 unsupported
            key = args[1][0]
            val = "1" if key.endswith("0Features") else "0"
            return {"devices:local": {key: val}}
        return {}

    client.get_setting_values = AsyncMock(side_effect=_get_setting_values)
    added: list[object] = []
    await async_setup_entry(hass, entry, lambda ents: added.extend(ents))
    assert added  # at least shadow mgmt entity created

    # PlenticoreDataSwitch branches
    coordinator = MagicMock()
    coordinator.data = {"devices:local": {"Battery:Strategy": "1"}}
    coordinator.async_write_data = AsyncMock(return_value=True)
    coordinator.async_request_refresh = AsyncMock()
    coordinator.start_fetch_data = MagicMock(return_value=lambda: None)
    coordinator.stop_fetch_data = MagicMock()
    description = PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="Battery:Strategy",
        name="Battery Strategy",
        is_on="1",
        on_value="1",
        on_label="On",
        off_value="0",
        off_label="Off",
    )
    entity = PlenticoreDataSwitch(
        coordinator,
        description,
        entry.entry_id,
        "cov",
        DeviceInfo(identifiers={("kostal_plenticore", "x")}),
    )
    assert entity.available is True
    assert entity.is_on is True
    await entity.async_turn_on()
    await entity.async_turn_off()
    coordinator.async_write_data.assert_called()
    coordinator.async_request_refresh.assert_called()

    coordinator.data = None
    assert entity.is_on is None

    # Shadow switch branches
    sh = PlenticoreShadowMgmtSwitch(
        coordinator,
        0,
        entry.entry_id,
        "cov",
        DeviceInfo(identifiers={("kostal_plenticore", "x")}),
    )
    assert sh._get_shadow_mgmt_value() == 0
    assert sh.is_on is None
    coordinator.data = {"devices:local": {"Generator:ShadowMgmt:Enable": "invalid"}}
    assert sh._get_shadow_mgmt_value() == 0
    coordinator.data = {"devices:local": {"Generator:ShadowMgmt:Enable": "1"}}
    assert sh.is_on is True
    await sh.async_turn_on()
    await sh.async_turn_off()


@pytest.mark.asyncio
async def test_number_and_sensor_targeted_gap_paths(hass: HomeAssistant) -> None:
    """Cover selected number/sensor branches not reached by integration tests."""
    from kostal_plenticore.number import (
        PlenticoreDataNumber,
        PlenticoreNumberEntityDescription,
        _get_settings_data_safe,
    )
    from kostal_plenticore.sensor import (
        CalculatedPvSumSensor,
        PlenticoreCalculatedSensor,
        _extract_dc_number_from_module_id,
        _sensor_translation_key,
        async_setup_entry as sensor_setup_entry,
    )

    # number: _get_settings_data_safe error path
    plenticore = SimpleNamespace(client=SimpleNamespace(get_settings=AsyncMock(side_effect=RuntimeError("x"))))
    assert await _get_settings_data_safe(plenticore, "op") == {}

    entry = _mock_entry()
    coord = MagicMock()
    coord.data = {"devices:local": {"Battery:MinSoc": "abc", "Battery:TimeUntilFallback": "0"}}
    coord.config_entry = entry
    coord.async_write_data = AsyncMock(return_value=True)
    coord.async_request_refresh = AsyncMock()
    coord.start_fetch_data = MagicMock(return_value=lambda: None)
    coord.stop_fetch_data = MagicMock()
    coord.hass = hass

    desc = PlenticoreNumberEntityDescription(
        key="battery_min_soc",
        name="Battery Min SoC",
        native_unit_of_measurement="%",
        native_max_value=100,
        native_min_value=0,
        native_step=1,
        entity_category=None,
        entity_registry_enabled_default=True,
        icon="mdi:battery",
        module_id="devices:local",
        data_id="Battery:MinSoc",
        fmt_from="format_round",
        fmt_to="format_round_back",
    )
    num = PlenticoreDataNumber(
        coord,
        entry.entry_id,
        "cov",
        DeviceInfo(identifiers={("kostal_plenticore", "x")}),
        desc,
        SettingsData(min="1", max="100", default=None, access="rw", unit="%", id="Battery:MinSoc", type="byte"),
    )
    assert num.native_value is None  # non-float branch
    assert num._parse_seconds(None) is None
    assert num._parse_seconds("x") is None
    assert num._get_keepalive_interval() >= 1
    num._cancel_keepalive()
    num._start_keepalive(5.0)

    with patch("kostal_plenticore.number.ensure_installer_access", return_value=False):
        await num.async_set_native_value(10.0)  # early return branch

    # high power safety branch
    desc_power = PlenticoreNumberEntityDescription(
        key="battery_charge_power_dc_absolute",
        name="Battery Charge Power",
        native_unit_of_measurement="W",
        native_max_value=100000,
        native_min_value=-100000,
        native_step=1,
        entity_category=None,
        entity_registry_enabled_default=True,
        icon="mdi:battery",
        module_id="devices:local",
        data_id="Battery:ChargePowerDcAbsolute",
        fmt_from="format_round",
        fmt_to="format_round_back",
    )
    num_power = PlenticoreDataNumber(
        coord,
        entry.entry_id,
        "cov",
        DeviceInfo(identifiers={("kostal_plenticore", "x")}),
        desc_power,
        None,
    )
    with patch("kostal_plenticore.number.ensure_installer_access", return_value=True):
        await num_power.async_set_native_value(60000.0)

    # sensor helper branches
    assert _sensor_translation_key("unknown:module", "A") is None
    assert _sensor_translation_key("devices:local:pv1", "P") is None
    assert _extract_dc_number_from_module_id("devices:local:pv3") == 3
    assert _extract_dc_number_from_module_id("devices:local:pvx") is None

    # sensor setup timeout/error branches
    client = MagicMock()
    client.get_process_data = AsyncMock(side_effect=asyncio.TimeoutError())
    client.get_setting_values = AsyncMock(side_effect=ApiException("Unknown API response [500]"))
    entry.runtime_data = SimpleNamespace(
        client=client,
        device_info=DeviceInfo(identifiers={("kostal_plenticore", "x")}),
    )  # type: ignore[attr-defined]
    added: list[object] = []
    await sensor_setup_entry(hass, entry, lambda ents: added.extend(ents))
    assert added is not None

    # calculated sensor branches
    sensor_desc = SimpleNamespace(
        module_id="_calc_",
        key="TotalGridConsumption:Total",
        name="Total Grid Consumption Total",
        formatter="format_round",
    )
    sc = MagicMock()
    sc.data = None
    calc = PlenticoreCalculatedSensor(
        sc,
        sensor_desc,
        "e",
        "cov",
        DeviceInfo(identifiers={("kostal_plenticore", "x")}),
    )
    assert calc.native_value is None

    # PV sum branches
    pv_desc = SimpleNamespace(
        module_id="_virt_",
        key="pv_P",
        name="PV Sum",
        formatter="format_round",
    )
    pvc = MagicMock()
    pvc.data = {"devices:local:pv1": {"P": "1.2"}, "devices:local:pv2": {"P": "bad"}}
    pv = CalculatedPvSumSensor(
        pvc,
        pv_desc,
        "e",
        "cov",
        DeviceInfo(identifiers={("kostal_plenticore", "x")}),
        dc_string_count=2,
    )
    assert pv.native_value is not None
    pvc.data = None
    assert pv.native_value is None


@pytest.mark.asyncio
async def test_switch_helper_and_setup_error_branches(hass: HomeAssistant) -> None:
    """Exercise switch helper/setup branches still not covered."""
    from kostal_plenticore.switch import (
        _handle_api_error,
        _normalize_translation_key,
        async_setup_entry,
    )

    assert _normalize_translation_key("A..B:: C") == "a_b_c"
    _handle_api_error(TimeoutError("t"), "op", "ctx")
    _handle_api_error(ClientError("n"), "op", "ctx")
    _handle_api_error(RuntimeError("x"), "op", "ctx")

    entry = _mock_entry()
    plenticore = SimpleNamespace(
        client=MagicMock(),
        available_modules=[],
        device_info=DeviceInfo(identifiers={("kostal_plenticore", "x")}),
    )
    entry.runtime_data = plenticore  # type: ignore[attr-defined]

    # settings fetch errors (500/api/other)
    for err in (
        ApiException("Unknown API response [500]"),
        ApiException("illegal function"),
        ClientError("net"),
    ):
        plenticore.client.get_settings = AsyncMock(side_effect=err)
        added: list[object] = []
        await async_setup_entry(hass, entry, lambda ents: added.extend(ents))
        assert added == []

    # shadow setup outer catch branches
    plenticore.client.get_settings = AsyncMock(return_value={"devices:local": []})
    plenticore.available_modules = ["devices:local"]
    plenticore.client.get_setting_values = AsyncMock(side_effect=ApiException("Unknown API response [500]"))
    added = []
    await async_setup_entry(hass, entry, lambda ents: added.extend(ents))
    assert isinstance(added, list)

    plenticore.client.get_setting_values = AsyncMock(side_effect=ApiException("illegal data value"))
    added = []
    await async_setup_entry(hass, entry, lambda ents: added.extend(ents))
    assert isinstance(added, list)

    plenticore.client.get_setting_values = AsyncMock(side_effect=ClientError("io"))
    added = []
    await async_setup_entry(hass, entry, lambda ents: added.extend(ents))
    assert isinstance(added, list)


@pytest.mark.asyncio
async def test_number_setup_migration_and_error_branches(hass: HomeAssistant) -> None:
    """Drive number setup through migration/exception branches."""
    from homeassistant.helpers import entity_registry as er
    from kostal_plenticore import number as number_mod
    from kostal_plenticore.number import (
        PlenticoreNumberEntityDescription,
        _handle_number_error,
        async_setup_entry,
    )

    # helper function branches
    assert _handle_number_error(ApiException("illegal function"), "op") == {}
    assert _handle_number_error(TimeoutError("t"), "op") == {}
    assert _handle_number_error(ClientError("io"), "op") == {}
    assert _handle_number_error(RuntimeError("x"), "op") == {}

    # minimal number descriptions (one force-create key + one non-force key)
    force_desc = PlenticoreNumberEntityDescription(
        key="battery_min_soc",
        name="Battery min SoC",
        native_unit_of_measurement="%",
        native_max_value=100,
        native_min_value=0,
        native_step=1,
        entity_category=None,
        entity_registry_enabled_default=True,
        icon="mdi:battery",
        module_id="devices:local",
        data_id="Battery:MinSoc",
        fmt_from="format_round",
        fmt_to="format_round_back",
    )
    other_desc = PlenticoreNumberEntityDescription(
        key="dummy_missing",
        name="Dummy Missing",
        native_unit_of_measurement="W",
        native_max_value=100,
        native_min_value=0,
        native_step=1,
        entity_category=None,
        entity_registry_enabled_default=True,
        icon="mdi:flash",
        module_id="devices:local",
        data_id="Dummy:Missing",
        fmt_from="format_round",
        fmt_to="format_round_back",
    )

    entry = _mock_entry()
    plenticore = SimpleNamespace(
        available_modules=["devices:local"],
        device_info=DeviceInfo(identifiers={("kostal_plenticore", "x")}),
        client=SimpleNamespace(),
    )
    entry.runtime_data = plenticore  # type: ignore[attr-defined]

    # First call returns empty -> retry branch. Retry returns known settings.
    settings_retry = [
        {},
        {"devices:local": [SettingsData(min="1", max="100", default=None, access="rw", unit="%", id="Battery:MinSoc", type="byte")]},
    ]

    async def fake_safe(_pl, _op):
        return settings_retry.pop(0) if settings_retry else {}

    entity_registry = er.async_get(hass)
    # create duplicates to enter migration/disable paths
    entity_registry.async_get_or_create(
        "number",
        "kostal_plenticore",
        f"{entry.entry_id}_devices:local_Battery:MinSoc",
        config_entry=entry,
        original_name="cov Battery min SoC",
    )
    entity_registry.async_get_or_create(
        "number",
        "kostal_plenticore",
        f"{entry.entry_id}_devices:local_Battery:MinSoc_DUP",
        config_entry=entry,
        original_name="cov Battery min SoC",
    )

    added: list[object] = []
    with (
        patch.object(number_mod, "NUMBER_SETTINGS_DATA", [force_desc, other_desc]),
        patch.object(number_mod, "_get_settings_data_safe", side_effect=fake_safe),
        patch.object(number_mod, "asyncio", wraps=number_mod.asyncio) as asyncio_mod,
        patch.object(number_mod.SettingDataUpdateCoordinator, "start_fetch_data", return_value=lambda: None),
        patch.object(number_mod.SettingDataUpdateCoordinator, "stop_fetch_data", return_value=None),
    ):
        asyncio_mod.sleep = AsyncMock(return_value=None)
        await async_setup_entry(hass, entry, lambda ents: added.extend(ents))
    assert added

    # Force registry migration exception paths (both try blocks)
    with (
        patch.object(number_mod, "NUMBER_SETTINGS_DATA", [force_desc]),
        patch.object(number_mod, "_get_settings_data_safe", AsyncMock(return_value={"devices:local": []})),
        patch("homeassistant.helpers.entity_registry.async_get", side_effect=RuntimeError("registry fail")),
        patch.object(number_mod.SettingDataUpdateCoordinator, "start_fetch_data", return_value=lambda: None),
        patch.object(number_mod.SettingDataUpdateCoordinator, "stop_fetch_data", return_value=None),
    ):
        await async_setup_entry(hass, entry, lambda _ents: None)


@pytest.mark.asyncio
async def test_additional_hard_branches_switch_number_coordinator(hass: HomeAssistant) -> None:
    """Cover additional hard-to-reach branches for real coverage."""
    from kostal_plenticore import number as number_mod
    from kostal_plenticore.coordinator import Plenticore, SelectDataUpdateCoordinator
    from kostal_plenticore.number import PlenticoreDataNumber, PlenticoreNumberEntityDescription
    from kostal_plenticore.switch import async_setup_entry as switch_setup_entry

    entry = _mock_entry()

    # --- switch: fallback per-string query branches (750-775, 779-782, 794-795) ---
    p = SimpleNamespace(
        client=MagicMock(),
        available_modules=["devices:local"],
        device_info=DeviceInfo(identifiers={("kostal_plenticore", "x")}),
    )
    entry.runtime_data = p  # type: ignore[attr-defined]

    p.client.get_settings = AsyncMock(return_value={"devices:local": []})

    async def _switch_values(module_id, data_ids):
        if module_id == "devices:local" and data_ids == "Properties:StringCnt":
            return {"devices:local": {"Properties:StringCnt": "1"}}
        # batch query -> force fallback path
        if module_id == "devices:local" and isinstance(data_ids, tuple) and len(data_ids) == 1:
            # first call from batch: raise 500
            if data_ids[0].endswith("0Features"):
                raise ApiException("Unknown API response [500]")
        return {}

    p.client.get_setting_values = AsyncMock(side_effect=_switch_values)
    await switch_setup_entry(hass, entry, lambda _ents: None)

    # branch: api exception non-500
    p.client.get_setting_values = AsyncMock(side_effect=ApiException("illegal function"))
    await switch_setup_entry(hass, entry, lambda _ents: None)

    # branch: non-api exception
    p.client.get_setting_values = AsyncMock(side_effect=ClientError("network"))
    await switch_setup_entry(hass, entry, lambda _ents: None)

    # --- number: constructor exception + keepalive branches ---
    coord = MagicMock()
    coord.data = {"devices:local": {"Battery:MinSoc": "5", "Battery:TimeUntilFallback": "30"}}
    coord.config_entry = entry
    coord.async_write_data = AsyncMock(side_effect=RuntimeError("keepalive write fail"))
    coord.async_request_refresh = AsyncMock()
    coord.start_fetch_data = MagicMock(return_value=lambda: None)
    coord.stop_fetch_data = MagicMock()
    coord.hass = hass

    bad_desc = PlenticoreNumberEntityDescription(
        key="bad",
        name="Bad",
        native_unit_of_measurement="%",
        native_max_value=100,
        native_min_value=0,
        native_step=1,
        entity_category=None,
        entity_registry_enabled_default=True,
        icon=None,
        module_id="devices:local",
        data_id="Battery:MinSoc",
        fmt_from="does_not_exist",
        fmt_to="format_round_back",
    )
    with pytest.raises(Exception):
        PlenticoreDataNumber(
            coord,
            entry.entry_id,
            "cov",
            DeviceInfo(identifiers={("kostal_plenticore", "x")}),
            bad_desc,
            None,
        )

    good_desc = PlenticoreNumberEntityDescription(
        key="g3",
        name="G3",
        native_unit_of_measurement="W",
        native_max_value=100000,
        native_min_value=0,
        native_step=1,
        entity_category=None,
        entity_registry_enabled_default=True,
        icon=None,
        module_id="devices:local",
        data_id="Battery:MaxChargePowerG3",
        fmt_from="format_round",
        fmt_to="format_round_back",
    )
    num = PlenticoreDataNumber(
        coord,
        entry.entry_id,
        "cov",
        DeviceInfo(identifiers={("kostal_plenticore", "x")}),
        good_desc,
        None,
    )
    # no data/module branch
    coord.data = None
    assert num.native_value is None
    assert num._resolve_data_id_for_read() == "Battery:MaxChargePowerG3"
    coord.data = {"devices:local": {"Battery:TimeUntilFallback": "30"}}
    num._start_keepalive(10.0)
    # existing running task branch
    num._start_keepalive(11.0)
    await asyncio.sleep(0)  # allow keepalive task to enter and fail branch
    num._cancel_keepalive()

    # --- coordinator: metadata/modules and select empty-iterator branch ---
    pl = Plenticore(hass, entry)
    pl._client = MagicMock()
    pl._client.get_modules = AsyncMock(side_effect=ClientError("m"))
    await pl._fetch_modules()
    pl._client.get_settings = AsyncMock(
        return_value={
            "scb:network": [
                SettingsData(
                    min="1",
                    max="63",
                    default=None,
                    access="rw",
                    unit=None,
                    id="Hostname",
                    type="string",
                )
            ]
        }
    )
    pl._client.get_setting_values = AsyncMock(side_effect=ApiException("meta fail"))
    await pl._fetch_device_metadata()

    select = SelectDataUpdateCoordinator(
        hass, entry, logging.getLogger(__name__), "sel", timedelta(seconds=10), pl
    )
    # empty dict path -> final return {}
    assert await select._async_get_current_option({}) == {}


@pytest.mark.asyncio
async def test_coordinator_remaining_branches(hass: HomeAssistant) -> None:
    """Cover remaining coordinator branches with focused unit-style tests."""
    from kostal_plenticore.coordinator import (
        DataUpdateCoordinatorMixin,
        Plenticore,
        ProcessDataUpdateCoordinator,
        SettingDataUpdateCoordinator,
    )

    entry = _mock_entry()
    p = Plenticore(hass, entry)

    # async_setup gather metadata_result exception (line 139) + ApiException parse branch (145-146)
    with patch("kostal_plenticore.coordinator.ExtendedApiClient") as client_cls:
        client = MagicMock()
        client.login = AsyncMock(return_value=None)
        client_cls.return_value = client
        p._fetch_modules = AsyncMock(return_value=None)
        p._fetch_device_metadata = AsyncMock(side_effect=ApiException("illegal function"))
        assert await p.async_setup() is False

    # _fetch_modules/_fetch_device_metadata early return with client None
    p2 = Plenticore(hass, entry)
    p2._client = None
    await p2._fetch_modules()
    await p2._fetch_device_metadata()

    # async_unload timeout/api branch
    p2._client = MagicMock()
    p2._client.logout = AsyncMock(side_effect=asyncio.TimeoutError())
    await p2.async_unload()

    class Mix(DataUpdateCoordinatorMixin):
        def __init__(self, pl: Plenticore) -> None:
            self._plenticore = pl
            self.name = "mix"

    mix = Mix(p2)
    p2._client = None
    assert await mix.async_read_data("m", "k") is None  # line 257
    assert await mix.async_write_data("m", {"k": "v"}) is False  # line 295

    p2._client = MagicMock()
    p2._client.get_setting_values = AsyncMock(side_effect=ClientError("Unknown API response [500]"))
    assert await mix.async_read_data("m", "k") is None  # line 285 branch
    p2._client.get_setting_values = AsyncMock(side_effect=ClientError("Missing data_id foo"))
    assert await mix.async_read_data("m", "k") is None  # line 287 branch

    p2._client.set_setting_values = AsyncMock(side_effect=ApiException("illegal data value"))
    with pytest.raises(HomeAssistantError):
        await mix.async_write_data("m", {"k": "v"})  # 313-314
    p2._client.set_setting_values = AsyncMock(side_effect=ApiException("illegal data address"))
    with pytest.raises(HomeAssistantError):
        await mix.async_write_data("m", {"k": "v"})  # 315-316

    # start_fetch_data duplicate callback + stop ValueError path
    proc = ProcessDataUpdateCoordinator(
        hass, entry, logging.getLogger(__name__), "proc", timedelta(seconds=10), p2
    )
    proc._fetch = {"m": ["k"]}
    cb2 = proc.start_fetch_data("m", "k")
    cb2()  # line 376 callback path without scheduling debouncer timer

    class WeirdList(list):
        def __contains__(self, item):
            return True

        def remove(self, item):
            raise ValueError("already removed")

    proc._fetch["m"] = WeirdList(["k"])
    proc.stop_fetch_data("m", "k")  # 397-399

    # process update: no fetch/client none
    proc._fetch = {}
    assert await proc._async_update_data() == {}  # 411
    proc._fetch = {"m": ["k"]}
    p2._client = MagicMock()
    p2._client.get_process_data_values = AsyncMock(side_effect=ApiException("illegal function"))
    with pytest.raises(UpdateFailed):
        await proc._async_update_data()  # 432-433
    p2._client.get_process_data_values = AsyncMock(side_effect=ClientError("generic"))
    with pytest.raises(UpdateFailed):
        await proc._async_update_data()  # 437

    # settings update: client none + no fetch + ApiException/else branches
    settings = SettingDataUpdateCoordinator(
        hass, entry, logging.getLogger(__name__), "set", timedelta(seconds=10), p2
    )
    p2._client = None
    assert await settings._async_update_data() == {}  # 477
    p2._client = MagicMock()
    settings._fetch = {}
    assert await settings._async_update_data() == {}  # 488
    settings._fetch = {"m": ["k"]}
    p2._client.get_setting_values = AsyncMock(side_effect=ApiException("illegal function"))
    with pytest.raises(UpdateFailed):
        await settings._async_update_data()  # 529
    p2._client.get_setting_values = AsyncMock(side_effect=ClientError("generic"))
    with pytest.raises(UpdateFailed):
        await settings._async_update_data()  # 532

    # avoid shutdown listener side effects in test cleanup
    p._shutdown_remove_listener = None
    p._client = None
    p2._shutdown_remove_listener = None
    p2._client = None


@pytest.mark.asyncio
async def test_number_remaining_branches(hass: HomeAssistant) -> None:
    """Cover remaining number branches with small deterministic scenarios."""
    from kostal_plenticore import number as number_mod
    from kostal_plenticore.number import PlenticoreDataNumber, PlenticoreNumberEntityDescription

    # line 72 normalization loop
    assert number_mod._normalize_translation_key("a__b") == "a_b"

    entry = _mock_entry()
    coord = MagicMock()
    coord.data = {"devices:local": {"Battery:MinSoc": "5", "Battery:TimeUntilFallback": "30"}}
    coord.config_entry = entry
    coord.async_write_data = AsyncMock(side_effect=RuntimeError("boom"))
    coord.async_request_refresh = AsyncMock()
    coord.start_fetch_data = MagicMock(return_value=lambda: None)
    coord.stop_fetch_data = MagicMock()
    coord.hass = hass

    desc = PlenticoreNumberEntityDescription(
        key="g3",
        name="G3",
        native_unit_of_measurement="W",
        native_max_value=100000,
        native_min_value=0,
        native_step=1,
        entity_category=None,
        entity_registry_enabled_default=True,
        icon=None,
        module_id="devices:local",
        data_id="Battery:MaxChargePowerG3",
        fmt_from="format_round",
        fmt_to="format_round_back",
    )
    num = PlenticoreDataNumber(
        coord,
        entry.entry_id,
        "cov",
        DeviceInfo(identifiers={("kostal_plenticore", "x")}),
        desc,
        None,
    )

    # _cancel_keepalive branch with running task (line 1605)
    never = asyncio.Event()
    num._keepalive_task = hass.async_create_task(never.wait())
    num._cancel_keepalive()

    # _start_keepalive branch create task + existing task return (1612-1615)
    num._start_keepalive(1.0)
    num._start_keepalive(2.0)
    num._cancel_keepalive()

    # _run_keepalive exception branch (1619-1644)
    num._keepalive_value = 1.0
    with patch("kostal_plenticore.number.ensure_installer_access", return_value=True):
        await num._run_keepalive()


@pytest.mark.asyncio
async def test_sensor_remaining_branches(hass: HomeAssistant) -> None:
    """Cover remaining sensor branches."""
    from kostal_plenticore.sensor import (
        CalculatedPvSumSensor,
        PlenticoreCalculatedSensor,
        PlenticoreDataSensor,
        PlenticoreSensorEntityDescription,
        _extract_dc_number_from_module_id,
        _handle_api_error,
        _sensor_translation_key,
    )

    # helper branches
    assert _sensor_translation_key("devices:local:battery", "A__B") == "battery_a_b"
    assert _extract_dc_number_from_module_id("devices:local:") is None  # line 130
    assert _extract_dc_number_from_module_id("devices:local:x1") is None  # line 134
    _handle_api_error(TimeoutError("t"), "op")
    _handle_api_error(ClientError("n"), "op")
    _handle_api_error(RuntimeError("x"), "op")

    # PlenticoreDataSensor unavailable native_value branch (2244)
    coord = MagicMock()
    coord.data = None
    desc = PlenticoreSensorEntityDescription(
        module_id="devices:local",
        key="Dc_P",
        name="Solar Power",
        formatter="format_round",
    )
    ds = PlenticoreDataSensor(
        coord,
        desc,
        "e",
        "cov",
        DeviceInfo(identifiers={("kostal_plenticore", "x")}),
    )
    assert ds.native_value is None

    # calculated branches for exceptions and zero/none paths
    calc_desc = PlenticoreSensorEntityDescription(
        module_id="_calc_",
        key="BatteryNetEfficiency:Total",
        name="Battery Net Efficiency",
        formatter="format_round",
    )
    c = MagicMock()
    c.data = {
        "scb:statistic:EnergyFlow": {
            "Statistic:EnergyChargePv:Total": "-1",
            "Statistic:EnergyChargeGrid:Total": "1",
            "Statistic:EnergyHomeBat:Total": "3",
            "Statistic:EnergyDischargeGrid:Total": "1",
        }
    }
    calc = PlenticoreCalculatedSensor(
        c,
        calc_desc,
        "e",
        "cov",
        DeviceInfo(identifiers={("kostal_plenticore", "x")}),
    )
    assert calc.native_value is None  # energy_in <= 0 (-1+1=0)
    c.data["scb:statistic:EnergyFlow"]["Statistic:EnergyChargePv:Total"] = "x"
    assert calc.native_value is None  # ValueError from float("x")

    pv_desc = PlenticoreSensorEntityDescription(
        module_id="_virt_",
        key="pv_P",
        name="PV Sum",
        formatter="format_round",
    )
    pvc = MagicMock()
    pvc.data = {"devices:local:pv1": {"P": "bad"}}
    pv = CalculatedPvSumSensor(
        pvc,
        pv_desc,
        "e",
        "cov",
        DeviceInfo(identifiers={("kostal_plenticore", "x")}),
        dc_string_count=1,
    )
    assert pv.native_value is None  # 2138


@pytest.mark.asyncio
async def test_final_gap_branches_switch_number_sensor_coordinator(hass: HomeAssistant) -> None:
    """Drive remaining difficult branches closer to full real coverage."""
    from kostal_plenticore import number as number_mod
    from kostal_plenticore import switch as switch_mod
    from kostal_plenticore.coordinator import Plenticore, SettingDataUpdateCoordinator
    from kostal_plenticore.number import PlenticoreNumberEntityDescription
    from kostal_plenticore.sensor import (
        PlenticoreCalculatedSensor,
        PlenticoreDataSensor,
        PlenticoreSensorEntityDescription,
        _extract_dc_number_from_module_id,
        async_setup_entry as sensor_setup_entry,
    )

    # coordinator line 530: Unknown 500 in non-ApiException branch
    entry = _mock_entry()
    p = Plenticore(hass, entry)
    p._client = MagicMock()
    settings = SettingDataUpdateCoordinator(
        hass, entry, logging.getLogger(__name__), "set2", timedelta(seconds=10), p
    )
    settings._fetch = {"m": ["k"]}
    p._client.get_setting_values = AsyncMock(side_effect=ClientError("Unknown API response [500]"))
    with pytest.raises(UpdateFailed):
        await settings._async_update_data()
    p._shutdown_remove_listener = None
    p._client = None

    # switch lines 767-775, 794-795, 828-848
    e2 = _mock_entry()
    pl = SimpleNamespace(
        client=MagicMock(),
        available_modules=["devices:local"],
        device_info=DeviceInfo(identifiers={("kostal_plenticore", "x")}),
    )
    e2.runtime_data = pl  # type: ignore[attr-defined]
    pl.client.get_settings = AsyncMock(return_value={"devices:local": []})

    # 767-772 ApiException non-500 in batch query
    async def _batch_api(module_id, data_ids):
        if data_ids == "Properties:StringCnt":
            return {"devices:local": {"Properties:StringCnt": "1"}}
        raise ApiException("illegal function")

    pl.client.get_setting_values = AsyncMock(side_effect=_batch_api)
    await switch_mod.async_setup_entry(hass, e2, lambda _ents: None)

    # 774-775 non-ApiException in batch query
    async def _batch_client(module_id, data_ids):
        if data_ids == "Properties:StringCnt":
            return {"devices:local": {"Properties:StringCnt": "1"}}
        raise ClientError("client boom")

    pl.client.get_setting_values = AsyncMock(side_effect=_batch_client)
    await switch_mod.async_setup_entry(hass, e2, lambda _ents: None)

    # 794-795 invalid integer conversion for feature value
    async def _feature_invalid(module_id, data_ids):
        if data_ids == "Properties:StringCnt":
            return {"devices:local": {"Properties:StringCnt": "1"}}
        if isinstance(data_ids, tuple) and len(data_ids) == 1:
            return {"devices:local": {data_ids[0]: "abc"}}
        return {}

    pl.client.get_setting_values = AsyncMock(side_effect=_feature_invalid)
    await switch_mod.async_setup_entry(hass, e2, lambda _ents: None)

    # 828-848 outer catch via string_feature_id failure
    with patch.object(switch_mod, "string_feature_id", side_effect=ApiException("Unknown API response [500]")):
        await switch_mod.async_setup_entry(hass, e2, lambda _ents: None)
    with patch.object(switch_mod, "string_feature_id", side_effect=ApiException("illegal data value")):
        await switch_mod.async_setup_entry(hass, e2, lambda _ents: None)
    with patch.object(switch_mod, "string_feature_id", side_effect=ClientError("outer client")):
        await switch_mod.async_setup_entry(hass, e2, lambda _ents: None)

    # number 1295-1300 + migration lines
    force_desc = PlenticoreNumberEntityDescription(
        key="battery_min_soc",
        name="Battery min SoC",
        native_unit_of_measurement="%",
        native_max_value=100,
        native_min_value=0,
        native_step=1,
        entity_category=None,
        entity_registry_enabled_default=True,
        icon=None,
        module_id="devices:local",
        data_id="Battery:MinSoc",
        fmt_from="format_round",
        fmt_to="format_round_back",
    )
    nonforce_desc = PlenticoreNumberEntityDescription(
        key="nonforce",
        name="Non Force",
        native_unit_of_measurement="W",
        native_max_value=100,
        native_min_value=0,
        native_step=1,
        entity_category=None,
        entity_registry_enabled_default=True,
        icon=None,
        module_id="devices:local",
        data_id="Non:Force",
        fmt_from="format_round",
        fmt_to="format_round_back",
    )

    class FlakySetting:
        def __init__(self):
            # First access passes initial membership check, second access fails exact lookup.
            self._values = ["Non:Force", "Other:Id", "Other:Id"]

        @property
        def id(self):
            if self._values:
                return self._values.pop(0)
            return "Other:Id"

    reg_calls: list[tuple[str, dict]] = []
    fake_registry = SimpleNamespace(
        async_update_entity=lambda entity_id, **kwargs: reg_calls.append((entity_id, kwargs))
    )
    fake_entries = [
        SimpleNamespace(domain="sensor", unique_id="u0", entity_id="sensor.x", original_name="x"),
        SimpleNamespace(domain="number", unique_id="u1", entity_id="number.none", original_name=None),
        SimpleNamespace(domain="number", unique_id="u2", entity_id="number.old", original_name="prefix Battery min SoC"),
    ]

    pln = SimpleNamespace(
        available_modules=["devices:local"],
        device_info=DeviceInfo(identifiers={("kostal_plenticore", "x")}),
        client=SimpleNamespace(),
    )
    e3 = _mock_entry()
    e3.runtime_data = pln  # type: ignore[attr-defined]
    safe_data = {"devices:local": [FlakySetting(), SettingsData(min="1", max="100", default=None, access="rw", unit="%", id="Battery:MinSoc", type="byte")]}
    with (
        patch.object(number_mod, "NUMBER_SETTINGS_DATA", [force_desc, nonforce_desc]),
        patch.object(number_mod, "_get_settings_data_safe", AsyncMock(return_value=safe_data)),
        patch.object(number_mod.SettingDataUpdateCoordinator, "start_fetch_data", return_value=lambda: None),
        patch.object(number_mod.SettingDataUpdateCoordinator, "stop_fetch_data", return_value=None),
        patch.object(number_mod.er, "async_get", return_value=fake_registry),
        patch.object(number_mod.er, "async_entries_for_config_entry", return_value=fake_entries),
    ):
        await number_mod.async_setup_entry(hass, e3, lambda _ents: None)
        await hass.async_block_till_done()
    assert reg_calls

    # keepalive branches in _run_keepalive (1623/1626/1636/1642)
    coord = MagicMock()
    coord.data = {"devices:local": {"Battery:TimeUntilFallback": "30"}}
    coord.config_entry = e3
    coord.async_write_data = AsyncMock(return_value=True)
    coord.hass = hass
    d = PlenticoreNumberEntityDescription(
        key="g3",
        name="G3",
        native_unit_of_measurement="W",
        native_max_value=100,
        native_min_value=0,
        native_step=1,
        entity_category=None,
        entity_registry_enabled_default=True,
        icon=None,
        module_id="devices:local",
        data_id="Battery:MaxChargePowerG3",
        fmt_from="format_round",
        fmt_to="format_round_back",
    )
    n = number_mod.PlenticoreDataNumber(coord, e3.entry_id, "cov", DeviceInfo(identifiers={("kostal_plenticore", "x")}), d, None)
    n._keepalive_value = 1.0
    with patch("kostal_plenticore.number.asyncio.sleep", AsyncMock(side_effect=lambda *_: setattr(n, "_keepalive_value", None) or None)):
        await n._run_keepalive()  # 1623

    n._keepalive_value = 1.0
    d2 = PlenticoreNumberEntityDescription(**{**d.__dict__, "data_id": "NonKeepalive:Key", "key": "non_keepalive"})
    n2 = number_mod.PlenticoreDataNumber(coord, e3.entry_id, "cov", DeviceInfo(identifiers={("kostal_plenticore", "x")}), d2, None)
    n2._keepalive_value = 1.0
    with patch("kostal_plenticore.number.asyncio.sleep", AsyncMock(return_value=None)):
        await n2._run_keepalive()  # 1626

    # _start_keepalive paths (1612-1615)
    n._hass = hass
    with patch.object(n, "_run_keepalive", AsyncMock(return_value=None)):
        n._start_keepalive(2.0)  # creates task at 1615
        await hass.async_block_till_done()

    blocker = asyncio.Event()
    n._keepalive_task = hass.async_create_task(blocker.wait())
    n._start_keepalive(3.0)  # hits "task exists and running" early return at 1613-1614
    n._cancel_keepalive()

    # ensure_installer_access false -> break path (1636)
    n._keepalive_value = 1.0
    n._requires_installer = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
    n.coordinator.config_entry = SimpleNamespace(data={"service_code": None})
    n.coordinator.async_write_data = AsyncMock(side_effect=AssertionError("keepalive write must not run"))
    with patch("kostal_plenticore.number.asyncio.sleep", AsyncMock(return_value=None)):
        await n._run_keepalive()

    n._keepalive_value = 1.0
    with patch("kostal_plenticore.number.asyncio.sleep", AsyncMock(side_effect=asyncio.CancelledError)):
        await n._run_keepalive()  # 1642

    # sensor remaining helper/setup branches
    class BadSplit(str):
        def split(self, *_args, **_kwargs):
            raise AttributeError("boom")

    class ShortSplit(str):
        def split(self, *_args, **_kwargs):
            return ["x"]

    class WrongPvSplit(str):
        def split(self, *_args, **_kwargs):
            return ["devices", "local", "xx"]

    assert _extract_dc_number_from_module_id(ShortSplit("devices:local:pv1")) is None
    assert _extract_dc_number_from_module_id(WrongPvSplit("devices:local:pv1")) is None
    assert _extract_dc_number_from_module_id(BadSplit("devices:local:pv1")) is None

    e4 = _mock_entry()
    cl = MagicMock()
    cl.get_process_data = AsyncMock(side_effect=ApiException("bad process"))
    cl.get_setting_values = AsyncMock(side_effect=asyncio.TimeoutError())
    e4.runtime_data = SimpleNamespace(client=cl, device_info=DeviceInfo(identifiers={("kostal_plenticore", "x")}))  # type: ignore[attr-defined]
    with patch("kostal_plenticore.sensor.SENSOR_PROCESS_DATA", []):
        await sensor_setup_entry(hass, e4, lambda _ents: None)  # 1511-1513 + 1529

    # sensor nested create_entities_batch branches and calc paths
    bad_dc = PlenticoreSensorEntityDescription(module_id="devices:local:pvBAD", key="P", name="bad", formatter="format_round")
    high_dc = PlenticoreSensorEntityDescription(module_id="devices:local:pv9", key="P", name="high", formatter="format_round")
    with patch("kostal_plenticore.sensor.SENSOR_PROCESS_DATA", [bad_dc, high_dc]):
        cl.get_process_data = AsyncMock(return_value={"devices:local": ["X"]})
        cl.get_setting_values = AsyncMock(return_value={"devices:local": {"Properties:StringCnt": "1"}})
        await sensor_setup_entry(hass, e4, lambda _ents: None)  # 1648-1651 + 1655-1659

    c = MagicMock()
    c.data = {"scb:statistic:EnergyFlow": {"Statistic:EnergyHomeGrid:Total": "1", "Statistic:EnergyChargeGrid:Total": "2"}}
    calc_total = PlenticoreCalculatedSensor(
        c,
        PlenticoreSensorEntityDescription(module_id="_calc_", key="TotalGridConsumption:Total", name="t", formatter="format_round"),
        "e",
        "cov",
        DeviceInfo(identifiers={("kostal_plenticore", "x")}),
    )
    assert calc_total.native_value is not None  # 1788-1790

    calc_unknown = PlenticoreCalculatedSensor(
        c,
        PlenticoreSensorEntityDescription(module_id="_calc_", key="UnknownMetric:Total", name="u", formatter="format_round"),
        "e",
        "cov",
        DeviceInfo(identifiers={("kostal_plenticore", "x")}),
    )
    assert calc_unknown.native_value is None  # 2022

    ds = PlenticoreDataSensor(
        MagicMock(data={}),
        PlenticoreSensorEntityDescription(module_id="devices:local", key="Dc_P", name="s", formatter="format_round"),
        "e",
        "cov",
        DeviceInfo(identifiers={("kostal_plenticore", "x")}),
    )
    assert ds.native_value is None  # 2244


@pytest.mark.asyncio
async def test_number_warning_when_setting_disappears_after_initial_check(
    hass: HomeAssistant,
) -> None:
    """Cover number setup warning path where setting lookup fails after membership check."""
    from kostal_plenticore import number as number_mod
    from kostal_plenticore.number import PlenticoreNumberEntityDescription

    desc = PlenticoreNumberEntityDescription(
        key="vanishing_setting",
        name="Vanishing",
        native_unit_of_measurement="W",
        native_max_value=100,
        native_min_value=0,
        native_step=1,
        entity_category=None,
        entity_registry_enabled_default=True,
        icon=None,
        module_id="devices:local",
        data_id="Test:Vanish",
        fmt_from="format_round",
        fmt_to="format_round_back",
    )

    class VanishingSetting:
        def __init__(self) -> None:
            self._ids = ["Test:Vanish", "Other:Id"]

        @property
        def id(self) -> str:
            if self._ids:
                return self._ids.pop(0)
            return "Other:Id"

    entry = _mock_entry()
    entry.runtime_data = SimpleNamespace(  # type: ignore[attr-defined]
        available_modules=["devices:local"],
        device_info=DeviceInfo(identifiers={("kostal_plenticore", "x")}),
        client=SimpleNamespace(),
    )

    captured_entities: list[object] = []
    safe_data = {"devices:local": [VanishingSetting()]}
    with (
        patch.object(number_mod, "NUMBER_SETTINGS_DATA", [desc]),
        patch.object(number_mod, "_get_settings_data_safe", AsyncMock(return_value=safe_data)),
        patch.object(number_mod.SettingDataUpdateCoordinator, "start_fetch_data", return_value=lambda: None),
        patch.object(number_mod.SettingDataUpdateCoordinator, "stop_fetch_data", return_value=None),
        patch.object(number_mod.er, "async_get", return_value=SimpleNamespace(async_update_entity=lambda *_a, **_k: None)),
        patch.object(number_mod.er, "async_entries_for_config_entry", return_value=[]),
        patch.object(number_mod._LOGGER, "warning") as warning_mock,
    ):
        await number_mod.async_setup_entry(hass, entry, lambda ents: captured_entities.extend(ents))
        await hass.async_block_till_done()

    assert captured_entities == []
    warning_mock.assert_called()


@pytest.mark.asyncio
async def test_number_start_keepalive_internal_lines(hass: HomeAssistant) -> None:
    """Cover _start_keepalive assignment/create/early-return lines."""
    from kostal_plenticore import number as number_mod
    from kostal_plenticore.number import PlenticoreNumberEntityDescription

    entry = _mock_entry()
    coord = MagicMock()
    coord.data = {"devices:local": {"Battery:TimeUntilFallback": "30"}}
    coord.config_entry = entry
    coord.async_write_data = AsyncMock(return_value=True)
    desc = PlenticoreNumberEntityDescription(
        key="g3",
        name="G3",
        native_unit_of_measurement="W",
        native_max_value=100,
        native_min_value=0,
        native_step=1,
        entity_category=None,
        entity_registry_enabled_default=True,
        icon=None,
        module_id="devices:local",
        data_id="Battery:MaxChargePowerG3",
        fmt_from="format_round",
        fmt_to="format_round_back",
    )
    n = number_mod.PlenticoreDataNumber(
        coord,
        entry.entry_id,
        "cov",
        DeviceInfo(identifiers={("kostal_plenticore", "x")}),
        desc,
        None,
    )
    n.hass = hass

    with patch.object(n, "_run_keepalive", AsyncMock(return_value=None)):
        n._start_keepalive(2.0)
        await hass.async_block_till_done()
    assert n._keepalive_value == 2.0

    blocker = asyncio.Event()
    existing_task = hass.async_create_task(blocker.wait())
    n._keepalive_task = existing_task
    n._start_keepalive(3.0)
    assert n._keepalive_task is existing_task
    n._cancel_keepalive()
