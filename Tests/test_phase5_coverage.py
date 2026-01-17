"""Phase 5 coverage tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp.client_exceptions import ClientError
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pykoplenti import ApiException, AuthenticationException

import importlib

from kostal_plenticore import config_flow, diagnostics, helper, repairs, select
from kostal_plenticore.const import CONF_HOST, CONF_PASSWORD, DOMAIN

kp_init = importlib.import_module("kostal_plenticore.__init__")


@pytest.mark.parametrize(
    "err",
    [
        ApiException("boom"),
        TimeoutError("timeout"),
        ClientError("client"),
        RuntimeError("oops"),
    ],
)
def test_handle_init_error_branches(err) -> None:
    with patch(
        "kostal_plenticore.__init__._parse_modbus_exception",
        return_value=SimpleNamespace(message="modbus"),
    ):
        assert kp_init._handle_init_error(err, "setup") is False


def test_log_setup_metrics_branches() -> None:
    with patch("kostal_plenticore.__init__._LOGGER") as logger:
        kp_init._log_setup_metrics(0.0, True)
        kp_init._log_setup_metrics(0.0, False)
        assert logger.info.called
        assert logger.warning.called


@pytest.mark.asyncio
async def test_async_setup_entry_platform_setup_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    class DummyPlenticore:
        def __init__(self, *_args):
            pass

        async def async_setup(self) -> bool:
            return True

    mock_config_entry.add_to_hass(hass)

    with patch("kostal_plenticore.__init__.Plenticore", DummyPlenticore), patch(
        "kostal_plenticore.__init__.clear_issue"
    ), patch(
        "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
        AsyncMock(side_effect=RuntimeError("boom")),
    ):
        assert await kp_init.async_setup_entry(hass, mock_config_entry) is False


@pytest.mark.asyncio
async def test_async_unload_entry_branches(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    class DummyPlenticore:
        async def async_unload(self) -> None:
            raise ApiException("logout")

    mock_config_entry.runtime_data = DummyPlenticore()

    with patch(
        "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
        AsyncMock(return_value=True),
    ), patch("kostal_plenticore.__init__.time.time", return_value=0.0):
        assert await kp_init.async_unload_entry(hass, mock_config_entry) is True


@pytest.mark.asyncio
async def test_async_unload_entry_timeout_branch(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    class DummyPlenticore:
        async def async_unload(self) -> None:
            raise asyncio.TimeoutError

    mock_config_entry.runtime_data = DummyPlenticore()

    with patch(
        "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
        AsyncMock(return_value=True),
    ):
        assert await kp_init.async_unload_entry(hass, mock_config_entry) is True


@pytest.mark.asyncio
async def test_async_unload_entry_unload_platforms_false(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    with patch(
        "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
        AsyncMock(return_value=False),
    ):
        assert await kp_init.async_unload_entry(hass, mock_config_entry) is False


def test_handle_config_flow_error_branches() -> None:
    errors = config_flow._handle_config_flow_error(
        AuthenticationException("bad", "error"), "auth"
    )
    assert errors[CONF_PASSWORD] == "invalid_auth"

    errors = config_flow._handle_config_flow_error(ClientError("x"), "net")
    assert errors[CONF_HOST] == "cannot_connect"

    errors = config_flow._handle_config_flow_error(ApiException("api"), "api")
    assert errors[CONF_HOST] == "cannot_connect"

    errors = config_flow._handle_config_flow_error(asyncio.TimeoutError(), "timeout")
    assert errors[CONF_HOST] == "timeout"

    errors = config_flow._handle_config_flow_error(RuntimeError("boom"), "other")
    assert errors


@pytest.mark.asyncio
async def test_test_connection_safe_rate_limit(hass: HomeAssistant) -> None:
    with patch("kostal_plenticore.config_flow._check_rate_limit", return_value=True):
        with pytest.raises(TimeoutError):
            await config_flow.test_connection_safe(
                hass, {CONF_HOST: "1.2.3.4", CONF_PASSWORD: "pw"}
            )


@pytest.mark.asyncio
async def test_test_connection_safe_success(hass: HomeAssistant) -> None:
    class DummyClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def login(self, _password, service_code=None):
            return None

        async def get_setting_values(self, module_id, data_id):
            return {module_id: {data_id: "scb"}}

    with patch("kostal_plenticore.config_flow.ApiClient", DummyClient), patch(
        "kostal_plenticore.config_flow.get_hostname_id", AsyncMock(return_value="Hostname")
    ):
        hostname = await config_flow.test_connection_safe(
            hass, {CONF_HOST: "1.2.3.4", CONF_PASSWORD: "pw"}
        )
    assert hostname == "scb"


@pytest.mark.asyncio
async def test_reauth_step_roundtrip(hass: HomeAssistant) -> None:
    flow = config_flow.KostalPlenticoreConfigFlow()
    flow.hass = hass
    with patch("kostal_plenticore.config_flow.test_connection_safe", AsyncMock(return_value="scb")):
        result = await flow.async_step_reauth()
    assert result["type"] == "form"


@pytest.mark.asyncio
async def test_reauth_confirm_updates_entry(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    flow = config_flow.KostalPlenticoreConfigFlow()
    flow.hass = hass
    flow.context = {"entry_id": mock_config_entry.entry_id}

    with patch("kostal_plenticore.config_flow.test_connection_safe", AsyncMock(return_value="scb")):
        result = await flow.async_step_reauth_confirm(
            {CONF_HOST: "1.2.3.4", CONF_PASSWORD: "pw"}
        )

    assert result["type"] == "abort"


def test_diagnostics_error_handling() -> None:
    assert diagnostics._handle_diagnostics_error(ApiException("api"), "version") == "Unknown"
    assert diagnostics._handle_diagnostics_error(ValueError("bad"), "string_count") == 0
    assert diagnostics._handle_diagnostics_error(asyncio.TimeoutError(), "me") == "Unknown"
    assert diagnostics._handle_diagnostics_error(RuntimeError("x"), "other") == {}


@pytest.mark.asyncio
async def test_get_diagnostics_data_safe_returns_default() -> None:
    async def _raise():
        raise RuntimeError("boom")

    result = await diagnostics._get_diagnostics_data_safe(
        SimpleNamespace(), "other", _raise, default_value={"ok": True}
    )
    assert result == {"ok": True}


def test_helper_formatters_and_conversions() -> None:
    assert helper._safe_int_conversion("6.0") == 6
    assert helper._safe_int_conversion("bad") == "bad"
    assert helper._safe_float_conversion("bad") == "bad"
    assert helper._handle_format_error("x", "round") == "x"
    assert helper.PlenticoreDataFormatter.format_round("4.2") == 4
    assert helper.PlenticoreDataFormatter.format_round("bad") == "bad"
    assert helper.PlenticoreDataFormatter.format_round_back(4.0) == "4"
    assert helper.PlenticoreDataFormatter.format_round_back(4.4) == "4"
    assert helper.PlenticoreDataFormatter.format_float("1.5") == 1.5
    assert helper.PlenticoreDataFormatter.format_float("bad") == "bad"
    assert helper.PlenticoreDataFormatter.format_float_back(2.5) == "2.5"
    assert helper.PlenticoreDataFormatter.format_energy("1000") == 1.0
    assert helper.PlenticoreDataFormatter.format_energy("bad") == "bad"

    assert helper.PlenticoreDataFormatter.format_inverter_state("1") == "Init"
    assert helper.PlenticoreDataFormatter.format_inverter_state("999") == "Unknown State 999"
    assert helper.PlenticoreDataFormatter.format_inverter_state("bad") == "bad"

    assert helper.PlenticoreDataFormatter.format_em_manager_state("0") == "Idle"
    assert helper.PlenticoreDataFormatter.format_em_manager_state("999") == "Unknown EM State 999"

    assert helper.PlenticoreDataFormatter.format_battery_management_mode("99").startswith("Unknown")
    assert helper.PlenticoreDataFormatter.format_battery_management_mode("bad") == "bad"

    assert helper.PlenticoreDataFormatter.format_sensor_type("99").startswith("Unknown")
    assert helper.PlenticoreDataFormatter.format_sensor_type("bad") == "bad"

    assert helper.PlenticoreDataFormatter.format_battery_type("1").startswith("Unknown")
    assert helper.PlenticoreDataFormatter.format_battery_type("bad") == "bad"

    assert helper.PlenticoreDataFormatter.format_pssb_fuse_state("2").startswith("Unknown")
    assert helper.PlenticoreDataFormatter.format_pssb_fuse_state("bad") == "bad"


@pytest.mark.asyncio
async def test_get_hostname_id_errors() -> None:
    class DummyClient:
        async def get_settings(self):
            return {"scb:network": []}

    with pytest.raises(ApiException):
        await helper.get_hostname_id(DummyClient())

    class TimeoutClient:
        async def get_settings(self):
            raise asyncio.TimeoutError

    with pytest.raises(ApiException):
        await helper.get_hostname_id(TimeoutClient())

    class UnknownClient:
        async def get_settings(self):
            return {"scb:network": [SimpleNamespace(id="Other")]}

    with pytest.raises(ApiException):
        await helper.get_hostname_id(UnknownClient())


def test_repairs_create_and_clear(hass: HomeAssistant) -> None:
    with patch("kostal_plenticore.repairs.ir.async_create_issue") as create_issue, patch(
        "kostal_plenticore.repairs.ir.async_delete_issue"
    ) as delete_issue:
        repairs.create_auth_failed_issue(hass)
        repairs.create_api_unreachable_issue(hass)
        repairs.create_inverter_busy_issue(hass)
        repairs.clear_issue(hass, "auth_failed")
        assert create_issue.call_count == 3
        delete_issue.assert_called_once()


def test_select_helpers_and_errors() -> None:
    assert select._normalize_translation_key("Battery:Foo  Bar") == "battery_foo_bar"
    select._handle_select_error(ApiException("boom"), "api")
    select._handle_select_error(TimeoutError("timeout"), "timeout")
    select._handle_select_error(ClientError("client"), "client")
    select._handle_select_error(RuntimeError("x"), "other")


@pytest.mark.asyncio
async def test_select_setup_skips_unavailable(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    description = select.PlenticoreSelectEntityDescription(
        module_id="missing",
        key="not_forced",
        name="Not Forced",
        options=["None"],
    )

    plenticore = SimpleNamespace(available_modules=["other"], device_info=DeviceInfo(identifiers={(DOMAIN, "x")}), client=MagicMock(set_setting_values=AsyncMock(return_value=None)))
    mock_config_entry.runtime_data = plenticore

    async def _empty_settings(_plenticore, _op):
        return {}

    entities: list = []

    with patch.object(select, "SELECT_SETTINGS_DATA", [description]), patch.object(
        select, "_get_settings_data_safe", _empty_settings
    ):
        await select.async_setup_entry(hass, mock_config_entry, entities.extend)

    assert entities == []


@pytest.mark.asyncio
async def test_select_get_settings_data_safe_error() -> None:
    plenticore = SimpleNamespace(client=SimpleNamespace(get_settings=AsyncMock(side_effect=ApiException("boom"))))
    result = await select._get_settings_data_safe(plenticore, "settings data")
    assert result == {}


@pytest.mark.asyncio
async def test_select_registry_migration_and_methods(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    plenticore = SimpleNamespace(
        available_modules=[],
        device_info=DeviceInfo(identifiers={(DOMAIN, "x")}),
        client=MagicMock(set_setting_values=AsyncMock(return_value=None)),
    )
    mock_config_entry.runtime_data = plenticore

    async def _empty_settings(_plenticore, _op):
        return {}

    entity_registry = er.async_get(hass)
    old_unique_id = f"{mock_config_entry.entry_id}_devices:local"
    new_unique_id = f"{mock_config_entry.entry_id}_devices:local_battery_charge"
    entity_registry.async_get_or_create(
        "select",
        DOMAIN,
        old_unique_id,
        config_entry=mock_config_entry,
        original_name="Old",
    )
    entity_registry.async_get_or_create(
        "select",
        DOMAIN,
        new_unique_id,
        config_entry=mock_config_entry,
        original_name="New",
    )

    entities: list = []

    with patch.object(select, "_get_settings_data_safe", _empty_settings):
        await select.async_setup_entry(hass, mock_config_entry, entities.extend)

    select_entity = entities[0]
    select_entity.coordinator.async_request_refresh = AsyncMock(side_effect=RuntimeError("boom"))
    select_entity.coordinator.async_write_data = AsyncMock(return_value=True)
    await select_entity.async_added_to_hass()
    select_entity.async_write_ha_state = MagicMock()
    select_entity.hass = hass
    await select_entity.async_select_option("Battery:SmartBatteryControl:Enable")
    await select_entity.async_will_remove_from_hass()
    unsub = getattr(select_entity.coordinator, "_unsub_refresh", None)
    if callable(unsub):
        unsub()
    assert select_entity.current_option is None


@pytest.mark.asyncio
async def test_select_registry_migration_error_branch(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    plenticore = SimpleNamespace(
        available_modules=[],
        device_info=DeviceInfo(identifiers={(DOMAIN, "x")}),
        client=MagicMock(set_setting_values=AsyncMock(return_value=None)),
    )
    mock_config_entry.runtime_data = plenticore

    async def _empty_settings(_plenticore, _op):
        return {}

    entities: list = []

    with patch.object(select, "_get_settings_data_safe", _empty_settings), patch(
        "kostal_plenticore.select.er.async_get", side_effect=RuntimeError("boom")
    ):
        await select.async_setup_entry(hass, mock_config_entry, entities.extend)


@pytest.mark.asyncio
async def test_select_registry_migration_old_entry_only(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    plenticore = SimpleNamespace(
        available_modules=[],
        device_info=DeviceInfo(identifiers={(DOMAIN, "x")}),
        client=MagicMock(set_setting_values=AsyncMock(return_value=None)),
    )
    mock_config_entry.runtime_data = plenticore

    async def _empty_settings(_plenticore, _op):
        return {}

    entity_registry = er.async_get(hass)
    old_unique_id = f"{mock_config_entry.entry_id}_devices:local"
    entity_registry.async_get_or_create(
        "select",
        DOMAIN,
        old_unique_id,
        config_entry=mock_config_entry,
        original_name="Old",
    )

    entities: list = []

    with patch.object(select, "_get_settings_data_safe", _empty_settings):
        await select.async_setup_entry(hass, mock_config_entry, entities.extend)




@pytest.mark.asyncio
async def test_async_unload_entry_cleanup_warning(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    class DummyPlenticore:
        async def async_unload(self) -> None:
            return None

    mock_config_entry.runtime_data = DummyPlenticore()

    with patch(
        "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
        AsyncMock(return_value=True),
    ), patch("kostal_plenticore.__init__.time.time", side_effect=[0.0, 10.0, 10.0]):
        assert await kp_init.async_unload_entry(hass, mock_config_entry) is True


def test_diagnostics_error_additional_branches() -> None:
    assert diagnostics._handle_diagnostics_error(ApiException("api"), "string_count") == 0
    assert diagnostics._handle_diagnostics_error(ApiException("api"), "other") == {}
    assert diagnostics._handle_diagnostics_error(ValueError("bad"), "other") == {}
    assert diagnostics._handle_diagnostics_error(asyncio.TimeoutError(), "string_count") == 0
    assert diagnostics._handle_diagnostics_error(asyncio.TimeoutError(), "other") == {}
    assert diagnostics._handle_diagnostics_error(RuntimeError("boom"), "me") == "Unknown"


def test_helper_additional_branches() -> None:
    class BadFloat:
        def __float__(self):
            raise TypeError("bad")

    assert helper.PlenticoreDataFormatter.format_em_manager_state("bad") == "bad"
    assert helper.PlenticoreDataFormatter.format_round_back(BadFloat()) == ""
    assert "BadFloat" in helper.PlenticoreDataFormatter.format_float_back(BadFloat())


@pytest.mark.asyncio
async def test_get_hostname_id_client_error() -> None:
    class ErrorClient:
        async def get_settings(self):
            raise ClientError("boom")

    with pytest.raises(ApiException):
        await helper.get_hostname_id(ErrorClient())


@pytest.mark.asyncio
async def test_select_option_none(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    plenticore = SimpleNamespace(
        available_modules=[],
        device_info=DeviceInfo(identifiers={(DOMAIN, "x")}),
        client=MagicMock(set_setting_values=AsyncMock(return_value=None)),
    )
    mock_config_entry.runtime_data = plenticore

    async def _settings(_plenticore, _op):
        return {"devices:local": [SimpleNamespace(id="Battery:SmartBatteryControl:Enable")]}

    entities: list = []

    with patch.object(select, "_get_settings_data_safe", _settings):
        await select.async_setup_entry(hass, mock_config_entry, entities.extend)

    select_entity = entities[0]
    select_entity.hass = hass
    select_entity.async_write_ha_state = MagicMock()
    select_entity.coordinator.async_write_data = AsyncMock(return_value=True)
    await select_entity.async_select_option("None")
    assert select_entity.current_option is None

def test_handle_config_flow_timeout_branch() -> None:
    errors = config_flow._handle_config_flow_error(asyncio.TimeoutError(), "timeout-branch")
    assert errors[CONF_HOST] == "timeout"


@pytest.mark.asyncio
async def test_reauth_confirm_exception_branch(hass: HomeAssistant) -> None:
    flow = config_flow.KostalPlenticoreConfigFlow()
    flow.hass = hass
    with patch("kostal_plenticore.config_flow.test_connection_safe", AsyncMock(side_effect=RuntimeError("boom"))):
        result = await flow.async_step_reauth_confirm({CONF_HOST: "1.2.3.4", CONF_PASSWORD: "pw"})
    assert result["type"] == "form"


@pytest.mark.asyncio
async def test_reauth_confirm_updates_entry_branch(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    flow = config_flow.KostalPlenticoreConfigFlow()
    flow.hass = hass
    flow.context = {"entry_id": mock_config_entry.entry_id}
    with patch("kostal_plenticore.config_flow.test_connection_safe", AsyncMock(return_value="scb")), patch.object(
        hass.config_entries, "async_reload", AsyncMock()
    ):
        result = await flow.async_step_reauth_confirm({CONF_HOST: "1.2.3.4", CONF_PASSWORD: "pw"})
    assert result["type"] == "abort"


def test_diagnostics_unexpected_string_count_branch() -> None:
    assert diagnostics._handle_diagnostics_error(RuntimeError("boom"), "string_count") == 0

def test_handle_config_flow_api_exception_branch() -> None:
    errors = config_flow._handle_config_flow_error(ApiException("api"), "api")
    assert errors[CONF_HOST] == "cannot_connect"


def test_handle_config_flow_timeout_branch_again() -> None:
    errors = config_flow._handle_config_flow_error(asyncio.TimeoutError(), "timeout")
    assert errors[CONF_HOST] == "timeout"


@pytest.mark.asyncio
async def test_reauth_confirm_updates_entry_explicit(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    flow = config_flow.KostalPlenticoreConfigFlow()
    flow.hass = hass
    flow.context = {"entry_id": mock_config_entry.entry_id}
    hass.config_entries.async_update_entry = MagicMock()
    hass.config_entries.async_get_entry = MagicMock(return_value=mock_config_entry)
    with patch("kostal_plenticore.config_flow.test_connection_safe", AsyncMock(return_value="scb")), patch.object(
        hass.config_entries, "async_reload", AsyncMock()
    ):
        result = await flow.async_step_reauth_confirm({CONF_HOST: "1.2.3.4", CONF_PASSWORD: "pw"})
    assert result["type"] == "abort"

@pytest.mark.asyncio
async def test_reauth_confirm_no_entry_id(
    hass: HomeAssistant,
) -> None:
    flow = config_flow.KostalPlenticoreConfigFlow()
    flow.hass = hass
    flow.context = {}
    with patch("kostal_plenticore.config_flow.test_connection_safe", AsyncMock(return_value="scb")):
        result = await flow.async_step_reauth_confirm({CONF_HOST: "1.2.3.4", CONF_PASSWORD: "pw"})
    assert result["type"] == "abort"


@pytest.mark.asyncio
async def test_reauth_confirm_entry_missing(
    hass: HomeAssistant,
) -> None:
    flow = config_flow.KostalPlenticoreConfigFlow()
    flow.hass = hass
    flow.context = {"entry_id": "missing"}
    hass.config_entries.async_get_entry = MagicMock(return_value=None)
    with patch("kostal_plenticore.config_flow.test_connection_safe", AsyncMock(return_value="scb")):
        result = await flow.async_step_reauth_confirm({CONF_HOST: "1.2.3.4", CONF_PASSWORD: "pw"})
    assert result["type"] == "abort"
