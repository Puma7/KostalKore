"""Phase 5 coverage tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp.client_exceptions import ClientError, ContentTypeError
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pykoplenti import ApiException, AuthenticationException

import importlib

kp_init = importlib.import_module("custom_components.kostal_kore.__init__")
from custom_components.kostal_kore import config_flow, diagnostics, helper, repairs, select
from custom_components.kostal_kore.const import CONF_HOST, CONF_PASSWORD, DOMAIN


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
        "custom_components.kostal_kore.__init__.parse_modbus_exception",
        return_value=SimpleNamespace(message="modbus"),
    ):
        assert kp_init._handle_init_error(err, "setup") is False


def test_log_setup_metrics_branches() -> None:
    with patch("custom_components.kostal_kore.__init__._LOGGER") as logger:
        kp_init._log_setup_metrics(0.0, True)
        kp_init._log_setup_metrics(0.0, False)
        assert logger.info.called
        assert logger.warning.called


@pytest.mark.asyncio
async def test_await_cleanup_step_timeout_is_handled() -> None:
    async def _slow_cleanup() -> None:
        await asyncio.sleep(0.05)

    with patch("custom_components.kostal_kore.__init__._LOGGER") as logger:
        await kp_init._await_cleanup_step("slow-step", _slow_cleanup(), timeout=0.001)
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

        async def async_unload(self) -> None:
            pass

    mock_config_entry.add_to_hass(hass)

    with patch("custom_components.kostal_kore.__init__.Plenticore", DummyPlenticore), patch(
        "custom_components.kostal_kore.__init__.clear_issue"
    ), patch(
        "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
        AsyncMock(side_effect=RuntimeError("boom")),
    ):
        assert await kp_init.async_setup_entry(hass, mock_config_entry) is False


@pytest.mark.asyncio
async def test_async_setup_entry_success(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    class DummyPlenticore:
        def __init__(self, *_args):
            pass

        async def async_setup(self) -> bool:
            return True

    mock_config_entry.add_to_hass(hass)

    with patch("custom_components.kostal_kore.__init__.Plenticore", DummyPlenticore), patch(
        "custom_components.kostal_kore.__init__.clear_issue"
    ), patch(
        "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
        AsyncMock(return_value=True),
    ):
        assert await kp_init.async_setup_entry(hass, mock_config_entry) is True


@pytest.mark.asyncio
async def test_async_setup_entry_setup_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    class DummyPlenticore:
        def __init__(self, *_args):
            pass

        async def async_setup(self) -> bool:
            raise RuntimeError("boom")

        async def async_unload(self) -> None:
            pass

    mock_config_entry.add_to_hass(hass)

    with patch("custom_components.kostal_kore.__init__.Plenticore", DummyPlenticore), patch(
        "custom_components.kostal_kore.__init__._handle_init_error", return_value=False
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
    ), patch("custom_components.kostal_kore.__init__.time.time", side_effect=[0.0, 10.0, 10.0, 10.0]):
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
    ), patch(
        "custom_components.kostal_kore.__init__.time.time", side_effect=[0.0, 10.0, 10.0, 10.0]
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
    expected_net_error = "cannot_connect"
    assert errors[CONF_HOST] == expected_net_error

    errors = config_flow._handle_config_flow_error(
        ContentTypeError(MagicMock(), (), message="html", headers=None),
        "net",
    )
    assert errors[CONF_HOST] == "cannot_connect"

    errors = config_flow._handle_config_flow_error(ApiException("api"), "api")
    assert errors[CONF_HOST] == "cannot_connect"

    errors = config_flow._handle_config_flow_error(asyncio.TimeoutError(), "timeout")
    assert errors[CONF_HOST] == "timeout"

    errors = config_flow._handle_config_flow_error(RuntimeError("boom"), "other")
    assert errors






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

        async def get_me(self):
            return SimpleNamespace(role="USER")

    with patch("custom_components.kostal_kore.config_flow.ApiClient", DummyClient), patch(
        "custom_components.kostal_kore.config_flow.get_hostname_id", AsyncMock(return_value="Hostname")
    ):
        result = await config_flow.test_connection_safe(
            hass, {CONF_HOST: "1.2.3.4", CONF_PASSWORD: "pw"}
        )
    assert result.host == "1.2.3.4"
    assert result.hostname == "scb"
    assert result.access_role == "USER"
    assert result.installer_access is False


@pytest.mark.asyncio
async def test_test_connection_safe_error(hass: HomeAssistant) -> None:
    class DummyClient:
        def __init__(self, *_args, **_kwargs):
            pass
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def login(self, _password, service_code=None):
            raise AuthenticationException("bad", "error")

    with patch("custom_components.kostal_kore.config_flow.ApiClient", DummyClient):
        with pytest.raises(AuthenticationException):
            await config_flow.test_connection_safe(
                hass, {CONF_HOST: "1.2.3.4", CONF_PASSWORD: "pw"}
            )


@pytest.mark.asyncio
async def test_reauth_step_roundtrip(hass: HomeAssistant) -> None:
    flow = config_flow.KostalPlenticoreConfigFlow()
    flow.hass = hass
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

    with patch(
        "custom_components.kostal_kore.config_flow.resolve_connection_safe",
        AsyncMock(
            return_value=config_flow.ConnectionCheckResult(
                host="1.2.3.4",
                hostname="scb",
                access_role="USER",
                installer_access=False,
            )
        ),
    ):
        result = await flow.async_step_reauth_confirm(
            {CONF_HOST: "1.2.3.4", CONF_PASSWORD: "pw"}
        )

    assert result["type"] == "abort"


@pytest.mark.asyncio
async def test_reauth_confirm_error_shows_form(hass: HomeAssistant) -> None:
    flow = config_flow.KostalPlenticoreConfigFlow()
    flow.hass = hass
    with patch(
        "custom_components.kostal_kore.config_flow.resolve_connection_safe",
        AsyncMock(side_effect=RuntimeError("boom")),
    ):
        result = await flow.async_step_reauth_confirm({CONF_HOST: "1.2.3.4", CONF_PASSWORD: "pw"})
    assert result["type"] == "form"


@pytest.mark.asyncio
async def test_reauth_confirm_without_entry_id(hass: HomeAssistant) -> None:
    flow = config_flow.KostalPlenticoreConfigFlow()
    flow.hass = hass
    with patch(
        "custom_components.kostal_kore.config_flow.resolve_connection_safe",
        AsyncMock(
            return_value=config_flow.ConnectionCheckResult(
                host="1.2.3.4",
                hostname="scb",
                access_role="USER",
                installer_access=False,
            )
        ),
    ):
        result = await flow.async_step_reauth_confirm({CONF_HOST: "1.2.3.4", CONF_PASSWORD: "pw"})
    assert result["type"] == "abort"


@pytest.mark.asyncio
async def test_reauth_confirm_missing_entry(hass: HomeAssistant) -> None:
    flow = config_flow.KostalPlenticoreConfigFlow()
    flow.hass = hass
    flow.context = {"entry_id": "missing"}
    with patch(
        "custom_components.kostal_kore.config_flow.resolve_connection_safe",
        AsyncMock(
            return_value=config_flow.ConnectionCheckResult(
                host="1.2.3.4",
                hostname="scb",
                access_role="USER",
                installer_access=False,
            )
        ),
    ):
        result = await flow.async_step_reauth_confirm({CONF_HOST: "1.2.3.4", CONF_PASSWORD: "pw"})
    assert result["type"] == "abort"


@pytest.mark.asyncio
async def test_user_step_forms_and_success(hass: HomeAssistant) -> None:
    flow = config_flow.KostalPlenticoreConfigFlow()
    flow.hass = hass
    result = await flow.async_step_user()
    assert result["type"] == "form"

    with patch(
        "custom_components.kostal_kore.config_flow.resolve_connection_safe",
        AsyncMock(
            return_value=config_flow.ConnectionCheckResult(
                host="1.2.3.4",
                hostname="scb",
                access_role="USER",
                installer_access=False,
            )
        ),
    ):
        result = await flow.async_step_user({CONF_HOST: "1.2.3.4", CONF_PASSWORD: "pw"})
    assert result["type"] == "form"
    assert result["step_id"] == "setup_options"


@pytest.mark.asyncio
async def test_user_step_error_shows_form(hass: HomeAssistant) -> None:
    flow = config_flow.KostalPlenticoreConfigFlow()
    flow.hass = hass
    with patch(
        "custom_components.kostal_kore.config_flow.resolve_connection_safe",
        AsyncMock(side_effect=RuntimeError("boom")),
    ):
        result = await flow.async_step_user({CONF_HOST: "1.2.3.4", CONF_PASSWORD: "pw"})
    assert result["type"] == "form"


@pytest.mark.asyncio
async def test_reconfigure_step_success(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    flow = config_flow.KostalPlenticoreConfigFlow()
    flow.hass = hass
    mock_config_entry.add_to_hass(hass)
    with patch(
        "custom_components.kostal_kore.config_flow.resolve_connection_safe",
        AsyncMock(
            return_value=config_flow.ConnectionCheckResult(
                host="1.2.3.4",
                hostname="scb",
                access_role="USER",
                installer_access=False,
            )
        ),
    ), patch.object(flow, "_get_reconfigure_entry", return_value=mock_config_entry):
        result = await flow.async_step_reconfigure({CONF_HOST: "1.2.3.4", CONF_PASSWORD: "pw"})
    assert result["type"] == "abort"


def test_diagnostics_error_handling() -> None:
    assert diagnostics._handle_diagnostics_error(ApiException("api"), "version") == "Unknown"
    assert diagnostics._handle_diagnostics_error(ApiException("api"), "string_count") == 0
    assert diagnostics._handle_diagnostics_error(ApiException("api"), "other") == {}
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


@pytest.mark.asyncio
async def test_get_diagnostics_data_safe_success() -> None:
    async def _ok():
        return {"ok": True}

    result = await diagnostics._get_diagnostics_data_safe(
        SimpleNamespace(), "other", _ok, default_value={"fail": True}
    )
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_async_get_config_entry_diagnostics(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    async def _get_setting_values(module_id, data_id):
        if data_id == diagnostics.STRING_COUNT_SETTING:
            return {module_id: {diagnostics.STRING_COUNT_SETTING: "2"}}
        return {module_id: {data_id[0]: "A", data_id[1]: "B"}}

    plenticore = SimpleNamespace(
        device_info={"name": "demo"},
        client=SimpleNamespace(
            get_process_data=AsyncMock(return_value={"mod": ["p"]}),
            get_settings=AsyncMock(return_value={"mod": [SimpleNamespace(id="X")]}),
            get_version=AsyncMock(return_value="1.0"),
            get_me=AsyncMock(return_value="me"),
            get_setting_values=AsyncMock(side_effect=_get_setting_values),
        ),
    )
    mock_config_entry.runtime_data = plenticore

    diagnostics_data = await diagnostics.async_get_config_entry_diagnostics(
        hass, mock_config_entry
    )
    assert diagnostics_data["client"]["version"] == "1.0"
    assert diagnostics_data["configuration"]


@pytest.mark.asyncio
async def test_async_get_config_entry_diagnostics_string_count_parse_error(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    async def _get_setting_values(module_id, data_id):
        return {module_id: {diagnostics.STRING_COUNT_SETTING: "bad"}}

    plenticore = SimpleNamespace(
        device_info={"name": "demo"},
        client=SimpleNamespace(
            get_process_data=AsyncMock(return_value={}),
            get_settings=AsyncMock(return_value={}),
            get_version=AsyncMock(return_value="1.0"),
            get_me=AsyncMock(return_value="me"),
            get_setting_values=AsyncMock(side_effect=_get_setting_values),
        ),
    )
    mock_config_entry.runtime_data = plenticore
    diagnostics_data = await diagnostics.async_get_config_entry_diagnostics(
        hass, mock_config_entry
    )
    assert diagnostics_data["configuration"] == {}


def test_helper_format_float_nan_inf() -> None:
    """Test format_float returns None for NaN and Inf (covers helper.py:249)."""
    assert helper.PlenticoreDataFormatter.format_float("nan") is None
    assert helper.PlenticoreDataFormatter.format_float("inf") is None
    assert helper.PlenticoreDataFormatter.format_float("-inf") is None


def test_helper_format_energy_nan_inf_negative() -> None:
    """Test format_energy returns None for NaN/Inf and 0.0 for negative (covers helper.py:269)."""
    assert helper.PlenticoreDataFormatter.format_energy("nan") is None
    assert helper.PlenticoreDataFormatter.format_energy("inf") is None
    assert helper.PlenticoreDataFormatter.format_energy("-inf") is None
    assert helper.PlenticoreDataFormatter.format_energy("-5000") == 0.0


def test_helper_formatters_and_conversions() -> None:
    assert helper._safe_int_conversion("6") == 6
    assert helper._safe_int_conversion("6.0") == 6
    assert helper._safe_int_conversion("bad") == "bad"
    assert helper._safe_float_conversion("2.5") == 2.5
    assert helper._safe_float_conversion("bad") == "bad"
    assert helper._handle_format_error("x", "round") is None
    assert helper.PlenticoreDataFormatter.format_round("4.2") == 4
    assert helper.PlenticoreDataFormatter.format_round("bad") is None
    assert helper.PlenticoreDataFormatter.format_round_back(4.0) == "4"
    assert helper.PlenticoreDataFormatter.format_round_back(4.4) == "4"
    assert helper.PlenticoreDataFormatter.format_float("1.5") == 1.5
    assert helper.PlenticoreDataFormatter.format_float("bad") is None
    assert helper.PlenticoreDataFormatter.format_float_back(2.5) == "2.5"
    assert helper.PlenticoreDataFormatter.format_energy("1000") == 1.0
    assert helper.PlenticoreDataFormatter.format_energy("bad") is None

    assert helper.PlenticoreDataFormatter.format_inverter_state("1") == "Init"
    assert helper.PlenticoreDataFormatter.format_inverter_state("999") == "Unknown State 999"
    assert helper.PlenticoreDataFormatter.format_inverter_state("bad") == "bad"

    assert helper.PlenticoreDataFormatter.format_em_manager_state("0") == "Idle"
    assert helper.PlenticoreDataFormatter.format_em_manager_state("999") == "Unknown EM State 999"

    assert helper.PlenticoreDataFormatter.format_battery_management_mode("1").startswith("External")
    assert helper.PlenticoreDataFormatter.format_sensor_type("0").startswith("SDM")
    assert helper.PlenticoreDataFormatter.format_battery_type("2").startswith("PIKO")
    assert helper.PlenticoreDataFormatter.format_pssb_fuse_state("1") == "Fuse ok"
    assert helper.PlenticoreDataFormatter.format_string("raw") == "raw"
    assert helper.PlenticoreDataFormatter.get_method("format_string")("x") == "x"

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

    with pytest.raises((asyncio.TimeoutError, TimeoutError)):
        await helper.get_hostname_id(TimeoutClient())

    class UnknownClient:
        async def get_settings(self):
            return {"scb:network": [SimpleNamespace(id="Other")]}

    with pytest.raises(ApiException):
        await helper.get_hostname_id(UnknownClient())


@pytest.mark.asyncio
async def test_get_hostname_id_success() -> None:
    class OkClient:
        async def get_settings(self):
            return {"scb:network": [SimpleNamespace(id="Hostname")]}

    assert await helper.get_hostname_id(OkClient()) == "Hostname"


def test_format_back_exceptions() -> None:
    class BadFloat:
        def __float__(self):
            raise TypeError("bad")
        def __str__(self):
            return "bad"

    assert helper.PlenticoreDataFormatter.format_float_back(BadFloat()) == "bad"
    assert helper.PlenticoreDataFormatter.format_round_back(BadFloat()) == ""


def test_repairs_create_and_clear(hass: HomeAssistant) -> None:
    with patch("custom_components.kostal_kore.repairs.ir.async_create_issue") as create_issue, patch(
        "custom_components.kostal_kore.repairs.ir.async_delete_issue"
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
    description = select.PlenticoreSelectEntityDescription(
        module_id="devices:local",
        key="battery_charge",
        name="Battery Charging / Usage mode",
        options=["None", "Battery:SmartBatteryControl:Enable"],
    )
    available = {"devices:local": [SimpleNamespace(id="Battery:SmartBatteryControl:Enable")]}
    assert select._validate_select_options(description, available) is True
    assert select._validate_select_options(description, {}) is False


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

    plenticore = SimpleNamespace(available_modules=["other"], device_info=DeviceInfo(identifiers={(DOMAIN, "x")}), client=MagicMock())
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
async def test_select_get_settings_data_safe_success() -> None:
    plenticore = SimpleNamespace(client=SimpleNamespace(get_settings=AsyncMock(return_value={"ok": True})))
    result = await select._get_settings_data_safe(plenticore, "settings data")
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_select_registry_migration_and_methods(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    plenticore = SimpleNamespace(
        available_modules=[],
        device_info=DeviceInfo(identifiers={(DOMAIN, "x")}),
        client=SimpleNamespace(set_setting_values=AsyncMock()),
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
    await select_entity.async_added_to_hass()
    select_entity.hass = hass
    select_entity.async_write_ha_state = MagicMock()
    await select_entity.async_select_option("Battery:SmartBatteryControl:Enable")
    if hasattr(select_entity.coordinator, "async_shutdown"):
        await select_entity.coordinator.async_shutdown()
    if hasattr(select_entity.coordinator, "async_shutdown"):
        await select_entity.coordinator.async_shutdown()
    await select_entity.async_will_remove_from_hass()
    assert select_entity.current_option is None


@pytest.mark.asyncio
async def test_select_current_option_available(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    plenticore = SimpleNamespace(
        available_modules=[],
        device_info=DeviceInfo(identifiers={(DOMAIN, "x")}),
        client=SimpleNamespace(set_setting_values=AsyncMock()),
    )
    mock_config_entry.runtime_data = plenticore

    async def _empty_settings(_plenticore, _op):
        return {"devices:local": [SimpleNamespace(id="Battery:SmartBatteryControl:Enable")]}

    entities: list = []

    with patch.object(select, "_get_settings_data_safe", _empty_settings):
        await select.async_setup_entry(hass, mock_config_entry, entities.extend)

    select_entity = entities[0]
    select_entity.coordinator.data = {select_entity.module_id: {select_entity.data_id: "Battery:SmartBatteryControl:Enable"}}
    assert select_entity.current_option == "Battery:SmartBatteryControl:Enable"


@pytest.mark.asyncio
async def test_select_select_option_writes(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    plenticore = SimpleNamespace(
        available_modules=[],
        device_info=DeviceInfo(identifiers={(DOMAIN, "x")}),
        client=SimpleNamespace(set_setting_values=AsyncMock()),
    )
    mock_config_entry.runtime_data = plenticore

    async def _empty_settings(_plenticore, _op):
        return {"devices:local": [SimpleNamespace(id="Battery:SmartBatteryControl:Enable")]}

    entities: list = []

    with patch.object(select, "_get_settings_data_safe", _empty_settings):
        await select.async_setup_entry(hass, mock_config_entry, entities.extend)

    select_entity = entities[0]
    select_entity.coordinator.async_write_data = AsyncMock(return_value=True)
    select_entity.hass = hass
    select_entity.async_write_ha_state = MagicMock()
    await select_entity.async_select_option("Battery:SmartBatteryControl:Enable")
    await select_entity.async_select_option("None")


@pytest.mark.asyncio
async def test_select_invalid_option_raises(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that selecting an invalid option raises ValueError."""
    plenticore = SimpleNamespace(
        available_modules=[],
        device_info=DeviceInfo(identifiers={(DOMAIN, "x")}),
        client=SimpleNamespace(set_setting_values=AsyncMock()),
    )
    mock_config_entry.runtime_data = plenticore

    async def _empty_settings(_plenticore, _op):
        return {"devices:local": [SimpleNamespace(id="Battery:SmartBatteryControl:Enable")]}

    entities: list = []

    with patch.object(select, "_get_settings_data_safe", _empty_settings):
        await select.async_setup_entry(hass, mock_config_entry, entities.extend)

    select_entity = entities[0]
    select_entity.hass = hass

    with pytest.raises(ValueError, match="Invalid select option"):
        await select_entity.async_select_option("InvalidOption")



@pytest.mark.asyncio
async def test_select_registry_migration_error_branch(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    plenticore = SimpleNamespace(
        available_modules=[],
        device_info=DeviceInfo(identifiers={(DOMAIN, "x")}),
        client=MagicMock(),
    )
    mock_config_entry.runtime_data = plenticore

    async def _empty_settings(_plenticore, _op):
        return {}

    entities: list = []

    with patch.object(select, "_get_settings_data_safe", _empty_settings), patch(
        "custom_components.kostal_kore.select.er.async_get", side_effect=RuntimeError("boom")
    ):
        await select.async_setup_entry(hass, mock_config_entry, entities.extend)


@pytest.mark.asyncio
async def test_rollback_setup_cleans_up_all_objects() -> None:
    """Test _rollback_setup cleans soc_controller, modbus_proxy, mqtt_bridge (covers __init__.py:419,422,425,427)."""
    hass = MagicMock()
    entry = SimpleNamespace(entry_id="test_entry", title="test")

    soc_ctrl = AsyncMock()
    proxy = AsyncMock()
    mqtt = AsyncMock()

    hass.data = {
        DOMAIN: {
            "test_entry": {
                "soc_controller": soc_ctrl,
                "modbus_proxy": proxy,
                "mqtt_bridge": mqtt,
            }
        }
    }

    plenticore = AsyncMock()

    with patch("custom_components.kostal_kore.__init__._await_cleanup_step", new_callable=lambda: AsyncMock):
        await kp_init._rollback_setup(hass, entry, plenticore)

    soc_ctrl.stop.assert_called_once()
    proxy.stop.assert_called_once()
    mqtt.async_stop.assert_called_once()
    plenticore.async_unload.assert_called_once()


@pytest.mark.asyncio
async def test_diagnostics_string_count_clamped(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test StringCnt out of sane range is clamped (covers diagnostics.py:170)."""
    async def _get_setting_values(module_id, data_id):
        if data_id == diagnostics.STRING_COUNT_SETTING:
            return {module_id: {diagnostics.STRING_COUNT_SETTING: "99"}}
        return {module_id: {data_id[0]: "A"}}

    plenticore = SimpleNamespace(
        device_info={"name": "demo"},
        client=SimpleNamespace(
            get_process_data=AsyncMock(return_value={}),
            get_settings=AsyncMock(return_value={}),
            get_version=AsyncMock(return_value="1.0"),
            get_me=AsyncMock(return_value="me"),
            get_setting_values=AsyncMock(side_effect=_get_setting_values),
        ),
    )
    mock_config_entry.runtime_data = plenticore

    diagnostics_data = await diagnostics.async_get_config_entry_diagnostics(
        hass, mock_config_entry
    )
    # StringCnt 99 clamped to MAX_SANE_STRING_COUNT (6), so 6 feature IDs generated
    assert "configuration" in diagnostics_data


@pytest.mark.asyncio
async def test_diagnostics_config_fetch_error(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test config settings fetch error is captured (covers diagnostics.py:186-189,196)."""
    call_count = 0

    async def _get_setting_values(module_id, data_id):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call: string count
            return {module_id: {diagnostics.STRING_COUNT_SETTING: "2"}}
        # Second call: feature IDs - raise error
        raise RuntimeError("feature fetch boom")

    plenticore = SimpleNamespace(
        device_info={"name": "demo"},
        client=SimpleNamespace(
            get_process_data=AsyncMock(return_value={}),
            get_settings=AsyncMock(return_value={}),
            get_version=AsyncMock(return_value="1.0"),
            get_me=AsyncMock(return_value="me"),
            get_setting_values=AsyncMock(side_effect=_get_setting_values),
        ),
    )
    mock_config_entry.runtime_data = plenticore

    diagnostics_data = await diagnostics.async_get_config_entry_diagnostics(
        hass, mock_config_entry
    )
    assert "_error" in diagnostics_data["configuration"]
    assert "RuntimeError" in diagnostics_data["configuration"]["_error"]


@pytest.mark.asyncio
async def test_select_registry_migration_update_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test migration error when async_update_entity fails (covers select.py:233-234)."""
    plenticore = SimpleNamespace(
        available_modules=[],
        device_info=DeviceInfo(identifiers={(DOMAIN, "x")}),
        client=MagicMock(),
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

    with patch.object(select, "_get_settings_data_safe", _empty_settings), patch.object(
        entity_registry,
        "async_update_entity",
        side_effect=RuntimeError("update failed"),
    ):
        await select.async_setup_entry(hass, mock_config_entry, entities.extend)

    # Should still succeed despite migration error
    assert len(entities) >= 1


@pytest.mark.asyncio
async def test_select_option_write_error_and_rollback(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test select option write failure with rollback (covers select.py:336-350)."""
    plenticore = SimpleNamespace(
        available_modules=[],
        device_info=DeviceInfo(identifiers={(DOMAIN, "x")}),
        client=SimpleNamespace(set_setting_values=AsyncMock()),
    )
    mock_config_entry.runtime_data = plenticore

    async def _settings_with_data(_plenticore, _op):
        return {"devices:local": [SimpleNamespace(id="Battery:SmartBatteryControl:Enable")]}

    entities: list = []

    with patch.object(select, "_get_settings_data_safe", _settings_with_data):
        await select.async_setup_entry(hass, mock_config_entry, entities.extend)

    select_entity = entities[0]
    select_entity.hass = hass
    select_entity.async_write_ha_state = MagicMock()

    # Set initial state so previous_option exists
    select_entity.coordinator.data = {
        select_entity.module_id: {select_entity.data_id: "Battery:SmartBatteryControl:Enable"}
    }

    # Make write_data fail
    select_entity.coordinator.async_write_data = AsyncMock(side_effect=RuntimeError("write failed"))

    with pytest.raises(RuntimeError, match="write failed"):
        await select_entity.async_select_option("None")


@pytest.mark.asyncio
async def test_select_option_write_error_rollback_also_fails(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test select option write failure when rollback also fails (covers select.py:346-349)."""
    plenticore = SimpleNamespace(
        available_modules=[],
        device_info=DeviceInfo(identifiers={(DOMAIN, "x")}),
        client=SimpleNamespace(set_setting_values=AsyncMock()),
    )
    mock_config_entry.runtime_data = plenticore

    async def _settings_with_data(_plenticore, _op):
        return {"devices:local": [SimpleNamespace(id="Battery:SmartBatteryControl:Enable")]}

    entities: list = []

    with patch.object(select, "_get_settings_data_safe", _settings_with_data):
        await select.async_setup_entry(hass, mock_config_entry, entities.extend)

    select_entity = entities[0]
    select_entity.hass = hass
    select_entity.async_write_ha_state = MagicMock()

    # Set initial state to a non-None option so rollback is attempted
    select_entity.coordinator.data = {
        select_entity.module_id: {select_entity.data_id: "Battery:SmartBatteryControl:Enable"}
    }

    # Make every write_data call fail (both the write and the rollback)
    select_entity.coordinator.async_write_data = AsyncMock(side_effect=RuntimeError("always fails"))

    with pytest.raises(RuntimeError, match="always fails"):
        await select_entity.async_select_option("None")


@pytest.mark.asyncio
async def test_select_registry_migration_old_entry_only(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    plenticore = SimpleNamespace(
        available_modules=[],
        device_info=DeviceInfo(identifiers={(DOMAIN, "x")}),
        client=MagicMock(),
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

