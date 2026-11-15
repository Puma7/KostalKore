"""A rejected password ends in Home Assistant's reauth flow.

At setup and while polling, an inverter that rejects the stored credentials
raises ``ConfigEntryAuthFailed``: Home Assistant stops retrying, marks the
entry, and starts the reauth flow, which stores the new password and reloads
the entry.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientError
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kostal_kore import config_flow, coordinator
from custom_components.kostal_kore.const import CONF_SERVICE_CODE, DOMAIN
from custom_components.kostal_kore.coordinator import (
    EventDataUpdateCoordinator,
    Plenticore,
    ProcessDataUpdateCoordinator,
    SelectDataUpdateCoordinator,
    SettingDataUpdateCoordinator,
)
from custom_components.kostal_kore.pykoplenti import (
    ApiException,
    AuthenticationException,
)

_CONNECTION_OK = config_flow.ConnectionCheckResult(
    host="192.168.1.2",
    hostname="scb",
    access_role="USER",
    installer_access=False,
)


def _reauth_flows(hass: HomeAssistant, entry: MockConfigEntry) -> list[dict[str, Any]]:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"].get("source") == SOURCE_REAUTH
        and flow["context"].get("entry_id") == entry.entry_id
    ]


@pytest.fixture
def probe_login() -> Generator[AsyncMock]:
    """Patch the separate client that checks the stored credentials."""
    probe = MagicMock()
    probe.login = AsyncMock()
    with patch.object(coordinator, "ApiClient") as probe_cls:
        probe_cls.return_value.__aenter__.return_value = probe
        probe_cls.return_value.__aexit__.return_value = None
        yield probe.login


# --- setup -------------------------------------------------------------------


async def test_wrong_password_at_setup_starts_reauth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_plenticore_client: MagicMock,
) -> None:
    """The entry ends in SETUP_ERROR (no retries) with a reauth flow open."""
    issue_id = f"{DOMAIN}_{mock_config_entry.entry_id}_auth_failed"
    # An issue left behind by an older version is superseded by the reauth flow.
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key="auth_failed",
    )
    mock_plenticore_client.login.side_effect = AuthenticationException(400, "bad password")
    mock_config_entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    assert mock_config_entry.reason is not None
    assert len(_reauth_flows(hass, mock_config_entry)) == 1
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert mock_plenticore_client.login.await_count == 1


async def test_reauth_flow_stores_password_and_reloads(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_plenticore_client: MagicMock,
) -> None:
    """Submitting the new password updates the entry and loads it."""
    mock_plenticore_client.login.side_effect = AuthenticationException(400, "bad password")
    mock_config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    (flow,) = _reauth_flows(hass, mock_config_entry)

    result = await hass.config_entries.flow.async_configure(flow["flow_id"])
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"]["host"] == "192.168.1.2"
    assert CONF_HOST not in result["data_schema"].schema

    mock_plenticore_client.login.side_effect = None
    with patch.object(
        config_flow, "resolve_connection_safe", AsyncMock(return_value=_CONNECTION_OK)
    ) as resolve:
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {CONF_PASSWORD: "NewPassword"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    resolve.assert_awaited_once()
    assert resolve.await_args.args[1] == {
        CONF_HOST: "192.168.1.2",
        CONF_PASSWORD: "NewPassword",
    }
    assert mock_config_entry.data[CONF_PASSWORD] == "NewPassword"
    assert mock_config_entry.title == "scb"
    assert mock_config_entry.state is ConfigEntryState.LOADED
    mock_plenticore_client.login.assert_awaited_with("NewPassword", service_code=None)
    assert _reauth_flows(hass, mock_config_entry) == []

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_reauth_flow_rejects_wrong_password_again(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A rejected new password shows the form again and changes nothing."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM

    with patch.object(
        config_flow,
        "resolve_connection_safe",
        AsyncMock(side_effect=AuthenticationException(400, "still wrong")),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "StillWrong"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_PASSWORD: "invalid_auth"}
    assert mock_config_entry.data[CONF_PASSWORD] == "SecretPassword"


@pytest.mark.parametrize(
    ("entered", "expected"),
    [
        ({}, "stored-code"),
        ({CONF_SERVICE_CODE: "new-code"}, "new-code"),
    ],
)
async def test_reauth_keeps_the_stored_service_code_unless_a_new_one_is_entered(
    hass: HomeAssistant, entered: dict[str, str], expected: str
) -> None:
    """A password change must not silently drop installer access."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="scb",
        data={
            CONF_HOST: "192.168.1.2",
            CONF_PASSWORD: "MasterKey",
            CONF_SERVICE_CODE: "stored-code",
        },
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)

    with (
        patch.object(
            config_flow,
            "resolve_connection_safe",
            AsyncMock(return_value=_CONNECTION_OK),
        ) as resolve,
        patch(
            "custom_components.kostal_kore.startup_trace.async_request_config_reload",
            AsyncMock(return_value=True),
        ) as reload,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "NewKey", **entered}
        )

    assert result["reason"] == "reauth_successful"
    assert resolve.await_args.args[1][CONF_SERVICE_CODE] == expected
    assert entry.data[CONF_SERVICE_CODE] == expected
    assert entry.data[CONF_PASSWORD] == "NewKey"
    reload.assert_awaited_once()


# --- polling -----------------------------------------------------------------


async def test_rejected_password_while_polling_starts_reauth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_plenticore_client: MagicMock,
    probe_login: AsyncMock,
) -> None:
    """A password changed on the inverter after setup leads to the reauth flow."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    plenticore = mock_config_entry.runtime_data
    # Set by ApiClient.__init__, so the autospec mock does not have it.
    mock_plenticore_client.websession = MagicMock()
    process = next(
        c
        for c in plenticore._rest_coordinators
        if isinstance(c, ProcessDataUpdateCoordinator)
    )
    process.start_fetch_data("devices:local", "P")

    mock_plenticore_client.get_process_data_values.side_effect = (
        AuthenticationException(400, "bad password")
    )
    probe_login.side_effect = AuthenticationException(400, "bad password")
    await process.async_refresh()
    await hass.async_block_till_done()

    assert not process.last_update_success
    assert isinstance(process.last_exception, ConfigEntryAuthFailed)
    assert len(_reauth_flows(hass, mock_config_entry)) == 1
    probe_login.assert_awaited_once_with("SecretPassword", service_code=None)

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


def _plenticore(hass: HomeAssistant, entry: MockConfigEntry) -> Plenticore:
    entry.add_to_hass(hass)
    plenticore = Plenticore(hass, entry)
    plenticore._client = MagicMock()
    return plenticore


def _coordinator(cls: type, hass: HomeAssistant, entry: MockConfigEntry, plenticore: Plenticore) -> Any:
    return cls(
        hass,
        entry,
        logging.getLogger(__name__),
        cls.__name__,
        timedelta(seconds=10),
        plenticore,
    )


async def _run_update(coord: Any) -> Any:
    try:
        return await coord._async_update_data()
    finally:
        await coord.async_shutdown()


async def _process_update(
    hass: HomeAssistant, entry: MockConfigEntry, plenticore: Plenticore
) -> Any:
    process = _coordinator(ProcessDataUpdateCoordinator, hass, entry, plenticore)
    process._fetch["devices:local"].append("P")
    return await _run_update(process)


async def _settings_update(
    hass: HomeAssistant, entry: MockConfigEntry, plenticore: Plenticore
) -> Any:
    settings = _coordinator(SettingDataUpdateCoordinator, hass, entry, plenticore)
    settings._fetch["devices:local"].append("Battery:MinSoc")
    return await _run_update(settings)


async def _event_update(
    hass: HomeAssistant, entry: MockConfigEntry, plenticore: Plenticore
) -> Any:
    events = _coordinator(EventDataUpdateCoordinator, hass, entry, plenticore)
    return await _run_update(events)


async def _select_update(
    hass: HomeAssistant, entry: MockConfigEntry, plenticore: Plenticore
) -> Any:
    select = _coordinator(SelectDataUpdateCoordinator, hass, entry, plenticore)
    select._fetch["devices:local"] = {
        "battery_charge": ["None", "Battery:SmartBatteryControl:Enable"]
    }
    return await _run_update(select)


_UPDATES = [_process_update, _settings_update, _event_update, _select_update]


def _fail_requests(plenticore: Plenticore, err: Exception) -> None:
    client = plenticore._client
    client.get_process_data_values = AsyncMock(side_effect=err)
    client.get_setting_values = AsyncMock(side_effect=err)
    client.get_events = AsyncMock(side_effect=err)


@pytest.mark.parametrize("update", _UPDATES)
async def test_every_coordinator_raises_auth_failed_on_rejected_password(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    probe_login: AsyncMock,
    update: Any,
) -> None:
    """Process, settings, event and select polling all hand over to reauth."""
    plenticore = _plenticore(hass, mock_config_entry)
    _fail_requests(plenticore, AuthenticationException(400, "bad password"))
    probe_login.side_effect = AuthenticationException(400, "bad password")

    with pytest.raises(ConfigEntryAuthFailed):
        await update(hass, mock_config_entry, plenticore)

    # Later polls stop before sending a request (each would be a failed login).
    _fail_requests(plenticore, AssertionError("no request after rejection"))
    with pytest.raises(ConfigEntryAuthFailed):
        await update(hass, mock_config_entry, plenticore)
    probe_login.assert_awaited_once()


@pytest.mark.parametrize(
    "probe_result",
    [None, ClientError("unreachable"), ApiException("busy")],
    ids=["login-works", "network-error", "api-error"],
)
async def test_bad_request_is_not_taken_for_a_wrong_password(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    probe_login: AsyncMock,
    probe_result: Exception | None,
) -> None:
    """pykoplenti maps every HTTP 400 to AuthenticationException.

    When a fresh login works (or cannot be checked), the error stays an
    ordinary failed poll and no reauth is started.
    """
    plenticore = _plenticore(hass, mock_config_entry)
    _fail_requests(plenticore, AuthenticationException(400, "bad request"))
    probe_login.side_effect = probe_result

    with pytest.raises(UpdateFailed):
        await _process_update(hass, mock_config_entry, plenticore)
    assert not plenticore._credentials_rejected

    # The event coordinator keeps serving its (empty) snapshot as before.
    assert await _event_update(hass, mock_config_entry, plenticore) == {}
    assert probe_login.await_count == 2
    assert not plenticore._credentials_rejected


async def test_credential_check_uses_a_default_session_before_login(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, probe_login: AsyncMock
) -> None:
    """Without a polling client the check opens its own session."""
    mock_config_entry.add_to_hass(hass)
    plenticore = Plenticore(hass, mock_config_entry)
    probe_login.side_effect = AuthenticationException(400, "bad password")

    with pytest.raises(ConfigEntryAuthFailed):
        await plenticore.async_raise_if_credentials_rejected(
            AuthenticationException(400, "bad password")
        )
    coordinator.ApiClient.assert_called_once()  # type: ignore[attr-defined]


async def test_auth_failure_in_first_event_refresh_starts_reauth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_plenticore_client: MagicMock,
) -> None:
    """The first refresh re-raises instead of starting reauth; setup does it."""
    mock_config_entry.add_to_hass(hass)
    with patch.object(
        EventDataUpdateCoordinator,
        "_async_update_data",
        AsyncMock(side_effect=ConfigEntryAuthFailed("rejected")),
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert len(_reauth_flows(hass, mock_config_entry)) == 1
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


def test_repair_issues_are_not_fixable(hass: HomeAssistant) -> None:
    """Without async_create_fix_flow, a fixable issue breaks the Fix button."""
    from custom_components.kostal_kore import repairs

    assert not hasattr(repairs, "async_create_fix_flow")
    with patch.object(repairs.ir, "async_create_issue") as create_issue:
        repairs.create_installer_required_issue(hass, entry_id="e")
        repairs.create_api_unreachable_issue(hass, entry_id="e")
        repairs.create_inverter_busy_issue(hass, entry_id="e")
        repairs.create_battery_capacity_unit_migration_issue(hass, entry_id="e")
    assert create_issue.call_count == 4
    assert all(call.kwargs["is_fixable"] is False for call in create_issue.call_args_list)
