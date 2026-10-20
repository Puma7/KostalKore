"""Administrator guards for destructive actions and the debug-bundle download."""

from __future__ import annotations

import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components import websocket_api
from homeassistant.components.diagnostics import REDACTED
from homeassistant.components.http.auth import async_sign_path
from homeassistant.components.http.const import KEY_HASS_REFRESH_TOKEN_ID
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import Unauthorized, UnknownUser
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.http import current_request
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.kostal_kore import button as button_platform
from custom_components.kostal_kore import diagnostics as diagnostics_mod
from custom_components.kostal_kore import migration_services as migration_mod
from custom_components.kostal_kore import orphan_history as orphan_mod
from custom_components.kostal_kore.const import (
    DOMAIN,
    SERVICE_ADOPT_LEGACY_ENTITY_IDS,
    SERVICE_APPLY_ORPHAN_HISTORY_MAPPING,
    SERVICE_COPY_LEGACY_HISTORY,
)
from custom_components.kostal_kore.diagnostics import (
    DEBUG_BUNDLE_DIR,
    DEBUG_BUNDLE_PRUNE_INTERVAL,
    DEBUG_BUNDLE_RETENTION_S,
    DEBUG_BUNDLE_URL,
    SERVICE_EXPORT_DEBUG_BUNDLE,
    _prune_debug_bundles,
    async_register_debug_bundle_service,
    async_unregister_debug_bundle_service_if_unused,
)
from custom_components.kostal_kore.helper import async_require_admin

BUNDLE = "kore_debug_abcdef12_20261020T120000123.json"


# ---------------------------------------------------------------------------
# helper.async_require_admin
# ---------------------------------------------------------------------------


async def test_require_admin_allows_system_and_admin(hass: HomeAssistant, hass_admin_user) -> None:
    await async_require_admin(hass, None)
    await async_require_admin(hass, Context())
    await async_require_admin(hass, Context(user_id=hass_admin_user.id))


async def test_require_admin_rejects_non_admin_and_unknown(
    hass: HomeAssistant, hass_read_only_user,
) -> None:
    with pytest.raises(Unauthorized):
        await async_require_admin(hass, Context(user_id=hass_read_only_user.id))
    with pytest.raises(UnknownUser):
        await async_require_admin(hass, Context(user_id="missing-user"))


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("register", "service", "handler", "data"), [
    (migration_mod.async_register_migration_services, SERVICE_ADOPT_LEGACY_ENTITY_IDS,
     "custom_components.kostal_kore.migration_services._handle_adopt_service", {}),
    (migration_mod.async_register_migration_services, SERVICE_COPY_LEGACY_HISTORY,
     "custom_components.kostal_kore.migration_services._handle_copy_history_service", {}),
    (orphan_mod.async_register_orphan_history_services, SERVICE_APPLY_ORPHAN_HISTORY_MAPPING,
     "custom_components.kostal_kore.orphan_history._handle_apply_service",
     {"mapping": {"sensor.old": "sensor.new"}}),
    (diagnostics_mod.async_register_debug_bundle_service, SERVICE_EXPORT_DEBUG_BUNDLE,
     "custom_components.kostal_kore.diagnostics._handle_export_debug_bundle", {}),
])
async def test_destructive_services_require_admin(
    hass: HomeAssistant, hass_admin_user, hass_read_only_user, register, service, handler, data,
) -> None:
    register(hass)
    with patch(handler, new=AsyncMock(return_value={})) as mock_handler:
        with pytest.raises(Unauthorized):
            await hass.services.async_call(
                DOMAIN, service, data, blocking=True,
                context=Context(user_id=hass_read_only_user.id),
            )
        mock_handler.assert_not_awaited()

        await hass.services.async_call(
            DOMAIN, service, data, blocking=True,
            context=Context(user_id=hass_admin_user.id),
        )
        mock_handler.assert_awaited_once()


# ---------------------------------------------------------------------------
# Buttons
# ---------------------------------------------------------------------------


def _entry() -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "10.0.0.11", "password": "pw"})
    entry.runtime_data = SimpleNamespace(device_info=DeviceInfo(identifiers={(DOMAIN, "S")}))
    return entry


@pytest.mark.parametrize("button_cls", [
    button_platform.LegacyCleanupButton, button_platform.LegacyMigrationButton,
])
async def test_legacy_buttons_require_admin(
    hass: HomeAssistant, hass_read_only_user, button_cls,
) -> None:
    entry = _entry()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {}
    entity = button_cls(entry)
    entity.hass = hass
    entity.async_write_ha_state = MagicMock()
    entity.async_set_context(Context(user_id=hass_read_only_user.id))
    with (
        patch.object(button_platform, "finalize_legacy_cleanup", new=AsyncMock()) as cleanup,
        patch.object(button_platform, "migrate_legacy_plenticore_entry", new=AsyncMock()) as mig,
        pytest.raises(Unauthorized),
    ):
        await entity.async_press()
    cleanup.assert_not_awaited()
    mig.assert_not_awaited()
    entity.async_write_ha_state.assert_not_called()
    assert hass.data[DOMAIN][entry.entry_id] == {}


# ---------------------------------------------------------------------------
# Config-entry diagnostics: host-derived device name
# ---------------------------------------------------------------------------


async def test_device_name_falling_back_to_host_is_redacted(hass: HomeAssistant) -> None:
    client = MagicMock()
    client.get_process_data = AsyncMock(return_value={})
    client.get_settings = AsyncMock(return_value={})
    client.get_version = AsyncMock(return_value="v")
    client.get_me = AsyncMock(return_value="me")
    client.get_setting_values = AsyncMock(return_value={})
    entry = MagicMock()
    entry.entry_id = "e1"
    entry.data = {"host": "192.0.2.7"}
    entry.as_dict.return_value = {"data": {"host": "192.0.2.7"}}
    entry.runtime_data = SimpleNamespace(
        client=client,
        device_info={"name": "192.0.2.7", "configuration_url": "http://192.0.2.7",
                     "identifiers": {(DOMAIN, "S")}},
    )
    data = await diagnostics_mod.async_get_config_entry_diagnostics(hass, entry)
    assert data["device"]["name"] == REDACTED
    assert data["device"]["configuration_url"] == REDACTED
    assert "192.0.2.7" not in str(data)


# ---------------------------------------------------------------------------
# Debug bundle storage and download
# ---------------------------------------------------------------------------


def test_prune_debug_bundles(tmp_path) -> None:
    old = tmp_path / "kore_debug_x_20200101T000000000.json"
    fresh = tmp_path / BUNDLE
    foreign = tmp_path / "notes.json"
    nested = tmp_path / "kore_debug_y_20200101T000000000.json"
    for path in (old, fresh, foreign):
        path.write_text("{}")
    nested.mkdir()
    stale = time.time() - DEBUG_BUNDLE_RETENTION_S - 60
    os.utime(old, (stale, stale))
    os.utime(foreign, (stale, stale))

    assert _prune_debug_bundles(str(tmp_path), time.time()) == 1
    assert not old.exists()
    assert fresh.exists() and foreign.exists() and nested.exists()

    # Missing directory and failing deletes are tolerated.
    assert _prune_debug_bundles(str(tmp_path / "missing"), time.time()) == 0
    os.utime(fresh, (stale, stale))
    with patch.object(diagnostics_mod.os, "remove", side_effect=OSError("busy")):
        assert _prune_debug_bundles(str(tmp_path), time.time()) == 1
    assert fresh.exists()


async def _http_hass(hass: HomeAssistant, tmp_path) -> str:
    hass.config.config_dir = str(tmp_path)
    assert await async_setup_component(hass, "http", {})
    async_register_debug_bundle_service(hass)
    directory = tmp_path / DEBUG_BUNDLE_DIR
    directory.mkdir()
    (directory / BUNDLE).write_text('{"ok": true}')
    return f"{DEBUG_BUNDLE_URL}/{BUNDLE}"


async def test_debug_bundle_view_admin_only(
    hass: HomeAssistant, hass_client, hass_read_only_access_token, tmp_path,
) -> None:
    url = await _http_hass(hass, tmp_path)
    # Registering again (another entry) must not add the route twice.
    async_register_debug_bundle_service(hass)

    admin = await hass_client()
    response = await admin.get(url)
    assert response.status == 200
    assert await response.json() == {"ok": True}
    assert "attachment" in response.headers["Content-Disposition"]

    assert (await admin.get(f"{DEBUG_BUNDLE_URL}/secrets.yaml")).status == 404
    assert (await admin.get(f"{DEBUG_BUNDLE_URL}/kore_debug_x_20200101T000000000.json")).status == 404

    read_only = await hass_client(hass_read_only_access_token)
    assert (await read_only.get(url)).status == 401


async def test_debug_bundle_signed_link(
    hass: HomeAssistant, hass_client_no_auth, hass_access_token, tmp_path,
) -> None:
    url = await _http_hass(hass, tmp_path)
    client = await hass_client_no_auth()
    assert (await client.get(url)).status == 401

    refresh_token = hass.auth.async_validate_access_token(hass_access_token)
    signed = async_sign_path(
        hass, url, diagnostics_mod.DEBUG_BUNDLE_LINK_TTL, refresh_token_id=refresh_token.id,
    )
    assert (await client.get(signed)).status == 200

    # Links are signed with the caller's WebSocket or HTTP refresh token ...
    for var, value in (
        (websocket_api.current_connection, SimpleNamespace(refresh_token_id=refresh_token.id)),
        (current_request, {KEY_HASS_REFRESH_TOKEN_ID: refresh_token.id}),
    ):
        reset = var.set(value)
        try:
            link = diagnostics_mod._debug_bundle_link(hass, BUNDLE)
        finally:
            var.reset(reset)
        assert link is not None and link.startswith(url) and "authSig=" in link
        assert (await client.get(link)).status == 200

    # ... and not at all without one (it would be the read-only content user).
    assert diagnostics_mod._debug_bundle_link(hass, BUNDLE) is None
    reset = current_request.set({})
    try:
        assert diagnostics_mod._debug_bundle_link(hass, BUNDLE) is None
    finally:
        current_request.reset(reset)


async def test_debug_bundles_pruned_without_further_export(hass: HomeAssistant, tmp_path) -> None:
    """Retention holds even when no later export runs, and stops on unregister."""
    hass.config.config_dir = str(tmp_path)
    directory = tmp_path / DEBUG_BUNDLE_DIR
    directory.mkdir()
    old = directory / BUNDLE
    old.write_text("{}")
    stale = time.time() - DEBUG_BUNDLE_RETENTION_S - 60
    os.utime(old, (stale, stale))

    fresh = directory / "kore_debug_x_20261020T120000123.json"
    fresh.write_text("{}")
    now = dt_util.utcnow()

    async_register_debug_bundle_service(hass)
    async_fire_time_changed(hass, now + DEBUG_BUNDLE_PRUNE_INTERVAL)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not old.exists() and fresh.exists()

    # The last entry goes away while a bundle remains: pruning continues.
    async_unregister_debug_bundle_service_if_unused(hass)
    async_fire_time_changed(hass, now + 2 * DEBUG_BUNDLE_PRUNE_INTERVAL)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert diagnostics_mod._DATA_DEBUG_BUNDLE_PRUNE in hass.data

    # Re-registering while the timer runs does not add a second one.
    async_register_debug_bundle_service(hass)
    cancel = hass.data[diagnostics_mod._DATA_DEBUG_BUNDLE_PRUNE]
    async_register_debug_bundle_service(hass)
    assert hass.data[diagnostics_mod._DATA_DEBUG_BUNDLE_PRUNE] is cancel
    async_unregister_debug_bundle_service_if_unused(hass)

    # Once the last bundle has expired, the timer stops itself.
    os.utime(fresh, (stale, stale))
    async_fire_time_changed(hass, now + 3 * DEBUG_BUNDLE_PRUNE_INTERVAL)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not fresh.exists()
    assert diagnostics_mod._DATA_DEBUG_BUNDLE_PRUNE not in hass.data


async def test_debug_bundle_view_registered_when_http_loads_later(
    hass: HomeAssistant, hass_admin_user, tmp_path,
) -> None:
    hass.config.config_dir = str(tmp_path)
    async_register_debug_bundle_service(hass)
    assert not hass.data.get(diagnostics_mod._DATA_DEBUG_BUNDLE_VIEW)

    assert await async_setup_component(hass, "http", {})
    with patch(
        "custom_components.kostal_kore.diagnostics._handle_export_debug_bundle",
        new=AsyncMock(return_value={}),
    ):
        await hass.services.async_call(
            DOMAIN, SERVICE_EXPORT_DEBUG_BUNDLE, {}, blocking=True,
            context=Context(user_id=hass_admin_user.id), return_response=True,
        )
    assert hass.data[diagnostics_mod._DATA_DEBUG_BUNDLE_VIEW] is True


async def test_export_response_carries_signed_links() -> None:
    """A caller with a transport token gets links, and no 'no link' hint."""
    hass = MagicMock()
    hass.data = {DOMAIN: {"e1": {}}}
    hass.services.async_call = AsyncMock()
    with (
        patch.object(diagnostics_mod, "_export_bundle_for_entry",
                     new=AsyncMock(return_value=f"/cfg/{DEBUG_BUNDLE_DIR}/{BUNDLE}")),
        patch.object(diagnostics_mod, "_debug_bundle_link", return_value="/signed"),
    ):
        result = await diagnostics_mod._handle_export_debug_bundle(hass, MagicMock())
    assert result == {"bundles": [{"file": BUNDLE, "url": "/signed"}], "errors": []}
    message = hass.services.async_call.await_args.args[2]["message"]
    assert BUNDLE in message and "no download link" not in message and "/signed" not in message
