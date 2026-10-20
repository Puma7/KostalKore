"""Exercise the registered action with HA's real service registry and filesystem."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.kostal_kore.communication import CommunicationState
from custom_components.kostal_kore.diagnostics import (
    async_register_debug_bundle_service,
    runtime_snapshot,
)
from custom_components.kostal_kore.write_audit import WriteAuditLog


def _store():
    channel = SimpleNamespace(
        name="Synthetic process", rest_communication_source="process",
        _fetch={"devices:local": ["Grid_P", "Dc_P"]},
        async_contexts=lambda: iter([("devices:local:battery", "SoC")]),
        data={"devices:local": {"Grid_P": "1200"}},
        fresh_fields={("devices:local", "Grid_P")},
        communication_stale=True, last_update_success=True,
    )
    channel._plenticore = SimpleNamespace(
        rest_communication=CommunicationState(), _rest_coordinators=[channel],
    )
    return {
        "modbus_coordinator": SimpleNamespace(
            data={"bat_max_charge_limit": 4500.0, "serial_number": "synthetic-private-id"},
            client=SimpleNamespace(endianness="little", retained_quality_addresses={1038}),
            communication=CommunicationState(), proxy_cache_data={},
            last_update_success=True, slow_poll_stale=True, slow_data_age_s=42,
            update_count=3, poll_phase=2, _fast_error_count=0,
            device_info_data={"sw_version": "synthetic-firmware", "battery_mgmt_mode": 2,
                              "serial_number": "synthetic-private-id", "private_field": "synthetic-secret"},
        ),
        "process_coordinator": channel, "write_audit": WriteAuditLog(),
    }


@pytest.mark.asyncio
async def test_public_export_returns_result_and_writes_real_config(hass: HomeAssistant, tmp_path):
    """Config directories outside /config must work without mocking the writer."""
    hass.config.config_dir = str(tmp_path)
    await async_setup_component(hass, "persistent_notification", {})
    hass.data["kostal_kore"] = {
        "_entry_lifecycle": {"internal": True}, "not_an_entry": None, "synthetic": _store(),
    }
    notifications = {}
    unsubscribe = persistent_notification.async_register_callback(
        hass, lambda update_type, current: notifications.update(current),
    )
    async_register_debug_bundle_service(hass)
    with patch("custom_components.kostal_kore.diagnostics.async_get_system_info", new=AsyncMock(return_value={})):
        result = await hass.services.async_call(
            "kostal_kore", "export_debug_bundle", {}, blocking=True, return_response=True,
        )
    await hass.async_block_till_done()
    unsubscribe()
    assert result is not None and result["errors"] == []
    assert len(result["bundles"]) == 1
    assert not (tmp_path / "www").exists()
    files = list((tmp_path / "kostal_kore_debug").glob("kore_debug_*.json"))
    assert len(files) == 1
    assert files[0].stat().st_mode & 0o077 == 0
    # No HTTP server in this hass: the link is the plain authenticated path.
    assert result["bundles"][0]["url"] == f"/api/kostal_kore/debug_bundle/{files[0].name}"
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["modbus_snapshot"]["bat_max_charge_limit"] == 4500
    assert "synthetic-private-id" not in files[0].read_text(encoding="utf-8")
    runtime = data["runtime"]
    assert runtime["modbus"]["retained_quality_addresses"] == [1038]
    assert runtime["modbus"]["endianness"] == "little"
    assert runtime["modbus"]["device_info_cached"] == {
        "sw_version": "synthetic-firmware", "battery_mgmt_mode": 2,
    }
    assert "synthetic-secret" not in files[0].read_text(encoding="utf-8")
    assert runtime["rest_channels"][0]["missing_fields"] == [
        ["devices:local", "Dc_P"], ["devices:local:battery", "SoC"],
    ]
    assert runtime["rest_channels"][0]["fresh_fields"] == [["devices:local", "Grid_P"]]
    message = notifications["kore_debug_bundle"]["message"]
    assert files[0].name in message and "/api/kostal_kore" not in message
    communication = await hass.services.async_call(
        "kostal_kore", "communication_diagnostics", {}, blocking=True, return_response=True,
    )
    assert set(communication) == {"synthetic"}


@pytest.mark.asyncio
async def test_public_export_reports_write_failure_and_supports_no_response(hass: HomeAssistant, tmp_path):
    hass.config.config_dir = str(tmp_path)
    await async_setup_component(hass, "persistent_notification", {})
    hass.data["kostal_kore"] = {"synthetic": _store()}
    async_register_debug_bundle_service(hass)
    with (
        patch("custom_components.kostal_kore.diagnostics.async_get_system_info", new=AsyncMock(return_value={})),
        patch("custom_components.kostal_kore.diagnostics.os.makedirs", side_effect=OSError("synthetic disk failure")),
    ):
        result = await hass.services.async_call(
            "kostal_kore", "export_debug_bundle", {}, blocking=True, return_response=True,
        )
        assert result["bundles"] == []
        assert "synthetic disk failure" in result["errors"][0]
    hass.data["kostal_kore"] = {}
    assert await hass.services.async_call(
        "kostal_kore", "export_debug_bundle", {}, blocking=True,
    ) is None
    assert not list(Path(tmp_path).rglob("kore_debug_*.json"))


def test_runtime_snapshot_empty_caches_and_settings_channel():
    store = _store()
    store["modbus_coordinator"].data = None
    channel = store["process_coordinator"]
    channel.data = None
    channel._fetch = {}
    channel.async_contexts = lambda: iter([])
    del channel.fresh_fields
    del channel.communication_stale
    snapshot = runtime_snapshot(store)
    assert snapshot["modbus"]["snapshot"] == {}
    assert snapshot["rest_channels"][0]["missing_fields"] == []
    assert snapshot["rest_channels"][0]["fresh_fields"] == []
    assert snapshot["rest_channels"][0]["communication_stale"] is None


@pytest.mark.parametrize("value,expected", [("21.5", 21.5), ("nan", None), ("inf", None), (None, None), ("invalid", None)])
@pytest.mark.parametrize("stale", [False, True])
def test_control_context_exports_only_numeric_allowlisted_cached_settings(value, expected, stale):
    store = _store()
    channel = store["process_coordinator"]
    channel.rest_communication_source = "settings:synthetic"
    channel.data = {"devices:local": {"Battery:MinSoc": value, "Network:Password": "synthetic-secret"}}
    channel._fetch = {"devices:local": ["Battery:MinSoc"]}
    channel.async_contexts = lambda: iter([])
    del channel.fresh_fields
    channel.communication_stale = stale
    context = runtime_snapshot(store)
    assert context["rest_channels"][0]["control_settings"] == {
        "Battery:MinSoc": {"value": expected, "fresh": not stale},
    }
    assert "synthetic-secret" not in json.dumps(context)


@pytest.mark.parametrize("success,stale,expected", [
    (True, False, [("devices:local", "Grid_P")]),
    (True, True, []), (False, False, []), (True, None, []),
])
def test_settings_freshness_requires_a_successful_current_result(success, stale, expected):
    store = _store()
    channel = store["process_coordinator"]
    del channel.fresh_fields
    channel.last_update_success = success
    channel.communication_stale = stale
    snapshot = runtime_snapshot(store)["rest_channels"][0]
    assert snapshot["fresh_fields"] == expected
    assert snapshot["missing_fields"] == [
        ("devices:local", "Dc_P"), ("devices:local:battery", "SoC"),
    ]
