"""Tests for export_debug_bundle service (diagnostics.py additions)."""

from __future__ import annotations

import os
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch, call

import pytest

from custom_components.kostal_kore.const import DOMAIN
from custom_components.kostal_kore.diagnostics import (
    SERVICE_EXPORT_DEBUG_BUNDLE,
    async_get_config_entry_diagnostics,
    async_register_debug_bundle_service,
    async_unregister_debug_bundle_service_if_unused,
    _handle_export_debug_bundle,
    _export_bundle_for_entry,
)
from custom_components.kostal_kore.write_audit import WriteAuditLog, WriteEvent


# ---------------------------------------------------------------------------
# Helper: minimal entry_store for the bundle export
# ---------------------------------------------------------------------------


def _minimal_store() -> dict:
    audit = WriteAuditLog()
    audit.log(WriteEvent(ts=time.monotonic(), source="modbus_coord",
                          key="bat_charge", value=5000, result="ok"))

    scheduler = MagicMock()
    scheduler.get_stats.return_value = {"total_requests": 10, "waits": 1,
                                         "timeouts": 0, "lock_held": False}

    health_mon = MagicMock()
    health_mon.get_health_summary.return_value = {"score": 95}

    fire = MagicMock()
    fire.current_risk_level = "safe"
    fire.alert_count = 0
    fire.active_alerts = []

    proxy = MagicMock()
    proxy.running = True
    proxy._clients = set()
    proxy._fc06_count = 3
    proxy._fc16_count = 1
    proxy._last_ext_write = {}

    modbus_coord = MagicMock()
    modbus_coord.data = {"battery_soc": 67.0}
    modbus_coord.update_count = 42
    modbus_coord.poll_phase = 3
    modbus_coord.slow_data_age_s = 12.5
    modbus_coord._fast_error_count = 0

    process_coord = MagicMock()
    process_coord.data = {"devices:local": {"Dc_P": "4200"}}

    return {
        "modbus_coordinator": modbus_coord,
        "health_monitor": health_mon,
        "fire_safety": fire,
        "write_audit": audit,
        "request_scheduler": scheduler,
        "modbus_proxy": proxy,
        "process_coordinator": process_coord,
    }


# ---------------------------------------------------------------------------
# async_register_debug_bundle_service
# ---------------------------------------------------------------------------


def test_register_debug_bundle_service_registers_once():
    hass = MagicMock()
    hass.services.has_service.return_value = False

    async_register_debug_bundle_service(hass)

    hass.services.async_register.assert_called_once_with(
        DOMAIN, SERVICE_EXPORT_DEBUG_BUNDLE, hass.services.async_register.call_args[0][2]
    )


def test_register_debug_bundle_service_idempotent():
    hass = MagicMock()
    hass.services.has_service.return_value = True

    async_register_debug_bundle_service(hass)

    hass.services.async_register.assert_not_called()


# ---------------------------------------------------------------------------
# async_unregister_debug_bundle_service_if_unused
# ---------------------------------------------------------------------------


def test_unregister_debug_bundle_service_skips_when_domain_data_present():
    hass = MagicMock()
    hass.data = {DOMAIN: {"entry_id_1": {}}}

    async_unregister_debug_bundle_service_if_unused(hass)

    hass.services.async_remove.assert_not_called()


def test_unregister_debug_bundle_service_removes_when_no_domain_data():
    hass = MagicMock()
    hass.data = {DOMAIN: {}}  # empty → falsy

    async_unregister_debug_bundle_service_if_unused(hass)

    hass.services.async_remove.assert_called_once_with(DOMAIN, SERVICE_EXPORT_DEBUG_BUNDLE)


# ---------------------------------------------------------------------------
# _export_bundle_for_entry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_bundle_for_entry_happy_path(tmp_path):
    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(side_effect=lambda fn, *args: fn(*args))

    store = _minimal_store()
    entry_id = "abcdef12test"

    with (
        patch("custom_components.kostal_kore.diagnostics.async_get_system_info",
              new=AsyncMock(return_value={"version": "2024.3.3"})),
        patch("custom_components.kostal_kore.diagnostics.os.makedirs"),
        patch("builtins.open", MagicMock()),
        patch("custom_components.kostal_kore.diagnostics.json.dump") as mock_dump,
    ):
        path = await _export_bundle_for_entry(hass, entry_id, store)

    assert "kore_debug_" in path
    assert entry_id[:8] in path
    mock_dump.assert_called_once()
    bundle = mock_dump.call_args[0][0]
    assert "health_summary" in bundle
    assert "fire_safety" in bundle
    assert "write_audit_last100" in bundle
    assert "scheduler_stats" in bundle
    assert "proxy_state" in bundle
    assert "modbus_snapshot" in bundle
    assert "coordinator_state" in bundle
    assert "rest_snapshot" in bundle
    assert bundle["rest_snapshot"]["devices:local"]["Dc_P"] == "4200"
    assert "last_ext_writes_seconds_ago" in bundle["proxy_state"]


@pytest.mark.asyncio
async def test_export_bundle_for_entry_health_exception_handled(tmp_path):
    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(side_effect=lambda fn, *args: fn(*args))

    store = _minimal_store()
    store["health_monitor"].get_health_summary.side_effect = RuntimeError("boom")

    with (
        patch("custom_components.kostal_kore.diagnostics.async_get_system_info",
              new=AsyncMock(return_value={})),
        patch("custom_components.kostal_kore.diagnostics.os.makedirs"),
        patch("builtins.open", MagicMock()),
        patch("custom_components.kostal_kore.diagnostics.json.dump") as mock_dump,
    ):
        await _export_bundle_for_entry(hass, "entry1", store)

    bundle = mock_dump.call_args[0][0]
    assert bundle["health_summary"] == {"error": "boom"}


@pytest.mark.asyncio
async def test_export_bundle_for_entry_fire_safety_exception_handled():
    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(side_effect=lambda fn, *args: fn(*args))

    store = _minimal_store()
    fire_mock = MagicMock()
    type(fire_mock).current_risk_level = PropertyMock(side_effect=RuntimeError("fire-err"))
    store["fire_safety"] = fire_mock

    with (
        patch("custom_components.kostal_kore.diagnostics.async_get_system_info",
              new=AsyncMock(return_value={})),
        patch("custom_components.kostal_kore.diagnostics.os.makedirs"),
        patch("builtins.open", MagicMock()),
        patch("custom_components.kostal_kore.diagnostics.json.dump") as mock_dump,
    ):
        await _export_bundle_for_entry(hass, "entry1", store)

    bundle = mock_dump.call_args[0][0]
    assert "error" in bundle["fire_safety"]


@pytest.mark.asyncio
async def test_export_bundle_for_entry_proxy_exception_handled():
    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(side_effect=lambda fn, *args: fn(*args))

    store = _minimal_store()
    proxy = MagicMock()
    type(proxy).running = PropertyMock(side_effect=RuntimeError("proxy-err"))
    store["modbus_proxy"] = proxy

    with (
        patch("custom_components.kostal_kore.diagnostics.async_get_system_info",
              new=AsyncMock(return_value={})),
        patch("custom_components.kostal_kore.diagnostics.os.makedirs"),
        patch("builtins.open", MagicMock()),
        patch("custom_components.kostal_kore.diagnostics.json.dump") as mock_dump,
    ):
        await _export_bundle_for_entry(hass, "entry1", store)

    bundle = mock_dump.call_args[0][0]
    assert "error" in bundle["proxy_state"]


@pytest.mark.asyncio
async def test_export_bundle_for_entry_rest_snapshot_exception_handled():
    """process_coordinator.data access failure must not crash the bundle."""
    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(side_effect=lambda fn, *args: fn(*args))

    store = _minimal_store()
    bad_proc = MagicMock()
    type(bad_proc).data = PropertyMock(side_effect=RuntimeError("rest-err"))
    store["process_coordinator"] = bad_proc

    with (
        patch("custom_components.kostal_kore.diagnostics.async_get_system_info",
              new=AsyncMock(return_value={})),
        patch("custom_components.kostal_kore.diagnostics.os.makedirs"),
        patch("builtins.open", MagicMock()),
        patch("custom_components.kostal_kore.diagnostics.json.dump") as mock_dump,
    ):
        await _export_bundle_for_entry(hass, "entry1", store)

    bundle = mock_dump.call_args[0][0]
    assert "error" in bundle["rest_snapshot"]


@pytest.mark.asyncio
async def test_export_bundle_for_entry_oserror_raises():
    hass = MagicMock()

    def _raise_os(*args, **kwargs):
        raise OSError("no space left")

    hass.async_add_executor_job = AsyncMock(side_effect=_raise_os)
    store = _minimal_store()

    with (
        patch("custom_components.kostal_kore.diagnostics.async_get_system_info",
              new=AsyncMock(return_value={})),
    ):
        with pytest.raises(OSError, match="Cannot write to"):
            await _export_bundle_for_entry(hass, "entry1", store)


@pytest.mark.asyncio
async def test_export_bundle_for_entry_system_info_exception_handled():
    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(side_effect=lambda fn, *args: fn(*args))
    store = _minimal_store()
    # Remove non-essential items to simplify
    store.pop("health_monitor")
    store.pop("fire_safety")
    store.pop("write_audit")
    store.pop("request_scheduler")
    store.pop("modbus_proxy")
    store.pop("modbus_coordinator")
    store.pop("process_coordinator")

    with (
        patch("custom_components.kostal_kore.diagnostics.async_get_system_info",
              new=AsyncMock(side_effect=RuntimeError("info-err"))),
        patch("custom_components.kostal_kore.diagnostics.os.makedirs"),
        patch("builtins.open", MagicMock()),
        patch("custom_components.kostal_kore.diagnostics.json.dump") as mock_dump,
    ):
        path = await _export_bundle_for_entry(hass, "entry1", store)

    bundle = mock_dump.call_args[0][0]
    assert bundle["ha_version"] == "unknown"


# ---------------------------------------------------------------------------
# _handle_export_debug_bundle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_export_debug_bundle_happy_path():
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    hass.data = {
        DOMAIN: {
            "entry1": _minimal_store(),
        }
    }

    with patch(
        "custom_components.kostal_kore.diagnostics._export_bundle_for_entry",
        new=AsyncMock(return_value="/config/www/kore_debug_abc.json"),
    ):
        await _handle_export_debug_bundle(hass, MagicMock())

    hass.services.async_call.assert_called_once()
    msg_arg = hass.services.async_call.call_args[0][2]["message"]
    assert "/local/kore_debug_abc.json" in msg_arg


@pytest.mark.asyncio
async def test_handle_export_debug_bundle_skips_non_dict_entry():
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    hass.data = {
        DOMAIN: {
            "entry1": ["not", "a", "dict"],   # should be skipped
        }
    }

    with patch(
        "custom_components.kostal_kore.diagnostics._export_bundle_for_entry",
        new=AsyncMock(return_value="/config/www/x.json"),
    ) as mock_export:
        await _handle_export_debug_bundle(hass, MagicMock())

    mock_export.assert_not_called()
    msg_arg = hass.services.async_call.call_args[0][2]["message"]
    assert "No debug bundle" in msg_arg


@pytest.mark.asyncio
async def test_handle_export_debug_bundle_records_error_on_exception():
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    hass.data = {
        DOMAIN: {
            "entry1": _minimal_store(),
        }
    }

    with patch(
        "custom_components.kostal_kore.diagnostics._export_bundle_for_entry",
        new=AsyncMock(side_effect=OSError("disk full")),
    ):
        await _handle_export_debug_bundle(hass, MagicMock())

    msg_arg = hass.services.async_call.call_args[0][2]["message"]
    assert "Errors" in msg_arg or "disk full" in msg_arg


@pytest.mark.asyncio
async def test_handle_export_debug_bundle_no_domain_data():
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    hass.data = {}  # no DOMAIN key

    await _handle_export_debug_bundle(hass, MagicMock())

    msg_arg = hass.services.async_call.call_args[0][2]["message"]
    assert "No debug bundle" in msg_arg


# ---------------------------------------------------------------------------
# async_register_debug_bundle_service — handler closure coverage (line 374)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_debug_bundle_service_handler_delegates():
    """The registered _handler closure delegates to _handle_export_debug_bundle."""
    hass = MagicMock()
    hass.services.has_service.return_value = False

    async_register_debug_bundle_service(hass)

    handler = hass.services.async_register.call_args[0][2]
    with patch(
        "custom_components.kostal_kore.diagnostics._handle_export_debug_bundle",
        new=AsyncMock(),
    ) as mock_handle:
        await handler(MagicMock())

    mock_handle.assert_called_once()


# ---------------------------------------------------------------------------
# async_get_config_entry_diagnostics — event_coordinator branch (line 216)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_diagnostics_event_coordinator_branch():
    """event_coordinator in entry_store populates data['events']."""
    entry_id = "testentry01"
    hass = MagicMock()

    event_coord = MagicMock()
    event_coord.data = {"AlarmEvent": "active"}
    event_coord.history = [{"ts": 1, "type": "alarm"}]

    hass.data = {
        DOMAIN: {
            entry_id: {"event_coordinator": event_coord},
        }
    }

    plenticore = SimpleNamespace(
        device_info={"name": "demo"},
        client=SimpleNamespace(
            get_process_data=AsyncMock(return_value={}),
            get_settings=AsyncMock(return_value={}),
            get_version=AsyncMock(return_value="1.0"),
            get_me=AsyncMock(return_value="me"),
            get_setting_values=AsyncMock(return_value={}),
        ),
    )
    config_entry = MagicMock()
    config_entry.entry_id = entry_id
    config_entry.runtime_data = plenticore

    result = await async_get_config_entry_diagnostics(hass, config_entry)

    assert "events" in result
    assert result["events"]["snapshot"] == {"AlarmEvent": "active"}
    assert result["events"]["history"] == [{"ts": 1, "type": "alarm"}]
