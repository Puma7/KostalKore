"""Diagnostics support for Kostal Plenticore."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import time
from collections.abc import Mapping
from dataclasses import asdict  # noqa: F401
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from typing import Any, Awaitable, Callable, Final

from aiohttp import web
from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.components.http.auth import async_sign_path
from homeassistant.components.http.const import KEY_HASS_REFRESH_TOKEN_ID, KEY_HASS_USER
from homeassistant.components.websocket_api.connection import current_connection
from homeassistant.const import ATTR_CONFIGURATION_URL, ATTR_IDENTIFIERS, ATTR_NAME, CONF_PASSWORD
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import config_validation as cv  # noqa: F401
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.http import HomeAssistantView, current_request
from homeassistant.helpers.system_info import async_get_system_info

from .const import CONF_HOST, CONF_SERVICE_CODE, DOMAIN, MAX_SANE_STRING_COUNT
from .const_ids import STRING_FEATURE_TEMPLATE, ModuleId, SettingId, string_feature_id  # noqa: F401
from .coordinator import PlenticoreConfigEntry

# Import MODBUS exception handling from coordinator
from .helper import async_require_admin, parse_modbus_exception
from .pykoplenti import ApiException

_LOGGER = logging.getLogger(__name__)

SERVICE_EXPORT_DEBUG_BUNDLE = "export_debug_bundle"

# Debug bundles hold household telemetry and the write audit. They live in a
# private directory of the HA config dir (never ``www``, which /local serves
# without authentication) and are downloaded through an authenticated view.
DEBUG_BUNDLE_DIR: Final[str] = "kostal_kore_debug"
DEBUG_BUNDLE_URL: Final[str] = "/api/kostal_kore/debug_bundle"
DEBUG_BUNDLE_LINK_TTL: Final[timedelta] = timedelta(hours=1)
DEBUG_BUNDLE_RETENTION_S: Final[float] = 24 * 3600.0
DEBUG_BUNDLE_PRUNE_INTERVAL: Final[timedelta] = timedelta(hours=1)
_DEBUG_BUNDLE_NAME_RE: Final = re.compile(r"kore_debug_[A-Za-z0-9_-]{0,8}_\d{8}T\d{9}\.json")
_DATA_DEBUG_BUNDLE_VIEW: Final[str] = f"{DOMAIN}_debug_bundle_view"
_DATA_DEBUG_BUNDLE_PRUNE: Final[str] = f"{DOMAIN}_debug_bundle_prune"

DEVICE_CONTEXT_FIELDS: Final[tuple[str, ...]] = (
    "sw_version", "product_name", "inverter_state", "inverter_max_power",
    "battery_mgmt_mode", "num_pv_strings", "num_bidirectional",
)
CONTROL_SETTING_FIELDS: Final[tuple[str, ...]] = (
    "Battery:MinSoc", "Battery:MinSocRel", "Battery:ManualCharge", "Battery:Strategy",
    "Battery:SmartBatteryControl:Enable", "Battery:TimeControl:Enable", "EnergyMgmt:AcStorage",
    "EnergyMgmt:BatCtrl:DisabelDischarge", "Battery:ExternControl:AcPowerAbs",
    "Battery:ExternControl:MaxChargePowerAbs", "Battery:ExternControl:MaxDischargePowerAbs",
    "Battery:ExternControl:MinSocRel", "Battery:ExternControl:MaxSocRel",
    "Battery:Limit:Charge_P", "Battery:Limit:Discharge_P",
)


def _numeric_setting(value: Any) -> float | None:
    """Export only finite numeric control settings, never arbitrary strings."""
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def communication_snapshot(entry_store: dict[str, Any]) -> dict[str, Any]:
    """Read local health only: no requests, file writes or raw exception text."""
    result: dict[str, Any] = {}
    modbus = entry_store.get("modbus_coordinator")
    if modbus is not None:
        result["modbus"] = modbus.communication.snapshot()
        result["modbus"]["observed_cache_entries"] = len(modbus.proxy_cache_data)
    process = entry_store.get("process_coordinator")
    owner = getattr(process, "_plenticore", None)
    health = getattr(owner, "rest_communication", None)
    if health is not None:
        result["rest"] = health.snapshot()
    proxy = entry_store.get("modbus_proxy")
    if proxy is not None:
        result["proxy"] = proxy.communication_diagnostics()
    return result


def runtime_snapshot(entry_store: dict[str, Any]) -> dict[str, Any]:
    """Expose cached values and polling demand without contacting the device."""
    result: dict[str, Any] = {}
    modbus = entry_store.get("modbus_coordinator")
    if modbus is not None:
        device_info = getattr(modbus, "device_info_data", {})
        if not isinstance(device_info, Mapping):
            device_info = {}
        result["modbus"] = {
            "device_info_cached": {
                key: device_info[key] for key in DEVICE_CONTEXT_FIELDS
                if key in device_info and isinstance(device_info[key], (str, int, float, bool))
            },
            "snapshot": dict(modbus.data or {}),
            "endianness": modbus.client.endianness,
            "last_update_success": modbus.last_update_success,
            "slow_poll_stale": modbus.slow_poll_stale,
            "slow_data_age_s": modbus.slow_data_age_s,
            "retained_quality_addresses": sorted(modbus.client.retained_quality_addresses),
        }
    process = entry_store.get("process_coordinator")
    owner = getattr(process, "_plenticore", None)
    coordinators = getattr(owner, "_rest_coordinators", [])
    channels: list[dict[str, Any]] = []
    for coordinator in coordinators:
        requested = {
            (str(module), str(key))
            for module, keys in coordinator._fetch.items() for key in keys
        }
        requested.update((str(module), str(key)) for module, key in coordinator.async_contexts())
        data = coordinator.data or {}
        missing = sorted(
            (module, key) for module, key in requested
            if key not in data.get(module, {})
        )
        stale = getattr(coordinator, "communication_stale", None)
        fresh = getattr(coordinator, "fresh_fields", None)
        if fresh is None:
            # Settings polls replace data on success but can retain it after a
            # failed request. Only a confirmed non-stale result is evidence.
            fresh = (requested - set(missing)
                     if coordinator.last_update_success and stale is False else set())
        channels.append({
            "name": coordinator.name,
            "source": coordinator.rest_communication_source,
            "last_update_success": coordinator.last_update_success,
            "communication_stale": stale,
            "requested_fields": sorted(requested),
            "missing_fields": missing,
            "fresh_fields": sorted(fresh),
            "control_settings": {
                key: {"value": _numeric_setting(data[DEVICES_LOCAL_MODULE][key]),
                      "fresh": (DEVICES_LOCAL_MODULE, key) in fresh}
                for key in CONTROL_SETTING_FIELDS if key in data.get(DEVICES_LOCAL_MODULE, {})
            },
        })
    result["rest_channels"] = channels
    return async_redact_data(result, TO_REDACT)

# Data redaction constants (config entry diagnostics + export_debug_bundle).
TO_REDACT: Final[set[str]] = {
    CONF_PASSWORD,
    CONF_SERVICE_CODE,
    CONF_HOST,
    "password",
    "service_code",
    "host",
    "ip",
    "ip_address",
    "api_key",
    "token",
    "secret",
    "authorization",
    "serial_number",
    "battery_serial",
    "battery_serial_alt",
    "network_name",
}

# Diagnostics constants
DEVICES_LOCAL_MODULE: Final[str] = ModuleId.DEVICES_LOCAL
STRING_COUNT_SETTING: Final[str] = SettingId.STRING_COUNT
STRING_FEATURE_PATTERN: Final[str] = STRING_FEATURE_TEMPLATE
DIAGNOSTICS_TIMEOUT_SECONDS: Final[float] = 30.0


def _handle_diagnostics_error(err: Exception, operation: str) -> Any:
    """
    Centralized error handling for diagnostics operations.
    
    Args:
        err: Exception that occurred
        operation: Description of the operation being performed
        
    Returns:
        Appropriate default value for the operation
    """  # noqa: W293
    if isinstance(err, ApiException):
        modbus_err = parse_modbus_exception(err)
        _LOGGER.warning("Could not get %s for diagnostics: %s", operation, modbus_err.message)
        if operation == "version" or operation == "me":
            return "Unknown"
        elif operation == "string_count":
            return 0
        else:
            return {}
    elif isinstance(err, (ValueError, AttributeError)):
        _LOGGER.warning("Could not parse %s for diagnostics: %s", operation, err)
        if operation == "string_count":
            return 0
        else:
            return {}
    elif isinstance(err, asyncio.TimeoutError):
        _LOGGER.warning("Timeout getting %s for diagnostics", operation)
        if operation == "version" or operation == "me":
            return "Unknown"
        elif operation == "string_count":
            return 0
        else:
            return {}
    else:
        _LOGGER.error("Unexpected error getting %s for diagnostics: %s", operation, err)
        if operation == "version" or operation == "me":
            return "Unknown"
        elif operation == "string_count":
            return 0
        else:
            return {}


async def _get_diagnostics_data_safe(
    plenticore: Any,
    operation: str,
    fetch_func: Callable[..., Awaitable[Any]],
    default_value: Any = None,
) -> Any:
    """
    Get diagnostics data with timeout and error handling.
    
    Args:
        plenticore: Plenticore client instance
        operation: Description of the operation
        fetch_func: Async function to fetch data
        default_value: Default value if operation fails
        
    Returns:
        Fetched data or default value
    """  # noqa: W293
    try:
        result = await asyncio.wait_for(fetch_func(), timeout=DIAGNOSTICS_TIMEOUT_SECONDS)
        return result
    except Exception as err:
        return _handle_diagnostics_error(err, operation) or default_value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: PlenticoreConfigEntry
) -> dict[str, dict[str, Any]]:
    """Return diagnostics for a config entry."""
    data: dict[str, dict[str, Any]] = {"config_entry": async_redact_data(config_entry.as_dict(), TO_REDACT)}

    plenticore = config_entry.runtime_data

    process_getter = (
        (lambda: plenticore.async_get_process_data_cached(ttl_seconds=0.0))
        if hasattr(plenticore, "async_get_process_data_cached")
        else plenticore.client.get_process_data
    )
    settings_getter = (
        (lambda: plenticore.async_get_settings_cached(ttl_seconds=0.0))
        if hasattr(plenticore, "async_get_settings_cached")
        else plenticore.client.get_settings
    )

    # Get information from Kostal Plenticore library with timeout protection
    available_process_data = await _get_diagnostics_data_safe(
        plenticore,
        "process data",
        process_getter,
    )

    available_settings_data = await _get_diagnostics_data_safe(
        plenticore,
        "settings data",
        settings_getter,
    )
    available_process_data = available_process_data or {}
    available_settings_data = available_settings_data or {}

    version = await _get_diagnostics_data_safe(
        plenticore, "version", plenticore.client.get_version, "Unknown"
    )

    me = await _get_diagnostics_data_safe(
        plenticore, "me", plenticore.client.get_me, "Unknown"
    )

    data["client"] = {
        "version": version,
        "me": me,
        "available_process_data": available_process_data,
        "available_settings_data": {
            module_id: [str(setting) for setting in settings]
            for module_id, settings in available_settings_data.items()
        },
    }

    # Add important information how the inverter is configured
    string_count_setting = await _get_diagnostics_data_safe(
        plenticore,
        "string count",
        lambda: plenticore.client.get_setting_values({DEVICES_LOCAL_MODULE: [STRING_COUNT_SETTING]})
    )

    string_count = 0
    try:
        raw_count = int(
            string_count_setting.get(DEVICES_LOCAL_MODULE, {})
            .get(STRING_COUNT_SETTING, 0)
        )
        string_count = max(0, min(raw_count, MAX_SANE_STRING_COUNT))
        if raw_count != string_count:
            _LOGGER.warning(
                "StringCnt value %d out of sane range, clamped to %d",
                raw_count, string_count,
            )
    except (ValueError, AttributeError):
        string_count = 0

    # Generate feature IDs dynamically based on string count
    feature_ids = [STRING_FEATURE_PATTERN.format(index=idx) for idx in range(string_count)]
    config_fetch_error: str | None = None
    if feature_ids:
        try:
            configuration_settings = await asyncio.wait_for(
                plenticore.client.get_setting_values({DEVICES_LOCAL_MODULE: feature_ids}),
                timeout=DIAGNOSTICS_TIMEOUT_SECONDS,
            )
        except Exception as err:
            _handle_diagnostics_error(err, "configuration settings")
            configuration_settings = {}
            config_fetch_error = f"{type(err).__name__}: {err}"
    else:
        configuration_settings = {}

    configuration_settings = configuration_settings or {}
    config_block: dict[str, Any] = {**configuration_settings}
    if config_fetch_error is not None:
        config_block["_error"] = config_fetch_error
    data["configuration"] = config_block

    device_info = {**plenticore.device_info}
    device_info[ATTR_IDENTIFIERS] = REDACTED  # contains serial number
    # configuration_url embeds the configured host, and the device name falls
    # back to the host when the inverter reports no hostname.
    device_info[ATTR_CONFIGURATION_URL] = REDACTED
    if device_info.get(ATTR_NAME) == config_entry.data.get(CONF_HOST):
        device_info[ATTR_NAME] = REDACTED
    data["device"] = device_info

    # Event intelligence diagnostics (bounded history + latest snapshot).
    entry_store = hass.data.get(DOMAIN, {}).get(config_entry.entry_id, {})
    event_coordinator = entry_store.get("event_coordinator") if entry_store else None
    if event_coordinator is not None:
        data["events"] = {
            "snapshot": dict(event_coordinator.data or {}),
            "history": list(event_coordinator.history),
        }
    ksem_coordinator = entry_store.get("ksem_coordinator") if entry_store else None
    if ksem_coordinator is not None:  # pragma: no cover
        data["ksem"] = {
            "connected": bool(ksem_coordinator.connected),
            "snapshot": dict(ksem_coordinator.data or {}),
        }

    data["communication"] = communication_snapshot(entry_store or {})
    data["runtime"] = runtime_snapshot(entry_store or {})
    return data


# ---------------------------------------------------------------------------
# export_debug_bundle service
# ---------------------------------------------------------------------------

async def _handle_export_debug_bundle(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Write diagnostic snapshots and return their download URLs to the caller."""
    bundles: list[str] = []
    errors: list[str] = []

    for entry_id, entry_store in hass.data.get(DOMAIN, {}).items():
        if entry_id.startswith("_") or not isinstance(entry_store, dict):
            continue
        try:
            path = await _export_bundle_for_entry(hass, entry_id, entry_store)
            bundles.append(path)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("export_debug_bundle failed for entry %s: %s", entry_id, err)
            errors.append(f"{entry_id}: {err}")

    links = [_debug_bundle_link(hass, os.path.basename(path)) for path in bundles]
    if bundles:
        # Persistent notifications are visible to every HA user, so the signed
        # links (usable without a login) go only to the caller's response.
        names = "\n".join(f"- `{os.path.basename(p)}`" for p in bundles)
        msg = (
            f"Debug bundle(s) written:\n{names}\n\n"
            "Download links are in the action response (Developer Tools → Actions). "
            f"They are valid for {int(DEBUG_BUNDLE_LINK_TTL.total_seconds() // 60)} "
            f"minutes; files are deleted after {int(DEBUG_BUNDLE_RETENTION_S // 3600)} hours."
        )
        if None in links:
            msg += (
                "\n\nStarted outside the UI/API (e.g. by an automation), so no download "
                f"link was created. Copy the files from `{hass.config.path(DEBUG_BUNDLE_DIR)}` "
                "or run the action from Developer Tools → Actions."
            )
    else:
        msg = "No debug bundle written."
    if errors:
        msg += "\n\nErrors:\n" + "\n".join(errors)

    await hass.services.async_call(
        "persistent_notification",
        "create",
        {
            "title": "KostalKore Debug Bundle",
            "message": msg,
            "notification_id": "kore_debug_bundle",
        },
        blocking=True,
    )
    return {
        "bundles": [
            {"file": os.path.basename(path), "url": link}
            for path, link in zip(bundles, links)
        ],
        "errors": [error for error in errors],
    }


def _debug_bundle_link(hass: HomeAssistant, filename: str) -> str | None:
    """Return a short-lived download path signed for the caller, if possible.

    Signs with the refresh token of the caller's WebSocket connection or HTTP
    request. Without one (automation, script) async_sign_path would fall back
    to HA's read-only content user, whose links the admin-only view rejects.
    """
    if getattr(hass, "http", None) is None:
        return None
    refresh_token_id: str | None = None
    if (connection := current_connection.get()) is not None:
        refresh_token_id = connection.refresh_token_id
    elif (request := current_request.get()) is not None:
        refresh_token_id = request.get(KEY_HASS_REFRESH_TOKEN_ID)
    if refresh_token_id is None:
        return None
    return async_sign_path(
        hass, f"{DEBUG_BUNDLE_URL}/{filename}", DEBUG_BUNDLE_LINK_TTL,
        refresh_token_id=refresh_token_id,
    )


class DebugBundleView(HomeAssistantView):
    """Serve exported debug bundles to authenticated administrators."""

    url = DEBUG_BUNDLE_URL + "/{filename}"
    name = "api:kostal_kore:debug_bundle"
    requires_auth = True

    def __init__(self, hass: HomeAssistant, directory: str) -> None:
        self._hass = hass
        self._directory = directory

    async def get(self, request: web.Request, filename: str) -> web.StreamResponse:
        """Return one bundle file as a download."""
        user = request.get(KEY_HASS_USER)
        if user is None or not user.is_admin:
            raise Unauthorized
        if not _DEBUG_BUNDLE_NAME_RE.fullmatch(filename):
            return web.Response(status=HTTPStatus.NOT_FOUND)
        path = os.path.join(self._directory, filename)
        if not await self._hass.async_add_executor_job(os.path.isfile, path):
            return web.Response(status=HTTPStatus.NOT_FOUND)
        return web.FileResponse(
            path,
            headers={
                # Never let a browser or proxy cache serve the bundle again
                # without the admin check.
                "Cache-Control": "no-store",
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )


def _private_opener(path: str, flags: int) -> int:
    """Create bundle files readable by the HA user only."""
    return os.open(path, flags, 0o600)


def _prune_debug_bundles(directory: str, now: float) -> int:
    """Delete exported bundles older than the retention window; return how many remain."""
    try:
        names = os.listdir(directory)
    except OSError as err:
        _LOGGER.debug("Cannot list debug bundle directory %s: %s", directory, err)
        return 0
    remaining = 0
    for name in names:
        path = os.path.join(directory, name)
        if not _DEBUG_BUNDLE_NAME_RE.fullmatch(name) or not os.path.isfile(path):
            continue
        try:
            if now - os.path.getmtime(path) > DEBUG_BUNDLE_RETENTION_S:
                os.remove(path)
                continue
        except OSError as err:
            _LOGGER.debug("Cannot prune debug bundle %s: %s", path, err)
        remaining += 1
    return remaining


async def _export_bundle_for_entry(
    hass: HomeAssistant, entry_id: str, entry_store: dict[str, Any]
) -> str:
    """Build and write the debug bundle JSON for one config entry. Returns path."""
    ha_info: dict[str, Any] = {}
    try:
        ha_info = await async_get_system_info(hass)
    except Exception:  # noqa: BLE001
        pass

    modbus_coord = entry_store.get("modbus_coordinator")
    health_mon = entry_store.get("health_monitor")
    fire_safety = entry_store.get("fire_safety")
    write_audit = entry_store.get("write_audit")
    scheduler = entry_store.get("request_scheduler")
    proxy = entry_store.get("modbus_proxy")

    bundle: dict[str, Any] = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "entry_id": entry_id,
        "ha_version": ha_info.get("version", "unknown"),
        "communication": communication_snapshot(entry_store),
        "runtime": runtime_snapshot(entry_store),
    }

    if health_mon is not None and hasattr(health_mon, "get_health_summary"):
        try:
            bundle["health_summary"] = health_mon.get_health_summary()
        except Exception as err:  # noqa: BLE001
            bundle["health_summary"] = {"error": str(err)}

    if fire_safety is not None:
        try:
            bundle["fire_safety"] = {
                "risk_level": str(fire_safety.current_risk_level),
                "active_alert_count": fire_safety.alert_count,
                "active_alerts": [
                    {
                        "risk_level": str(a.risk_level),
                        "category": getattr(a, "category", ""),
                        "title": a.title,
                    }
                    for a in list(fire_safety.active_alerts)[:10]
                ],
            }
        except Exception as err:  # noqa: BLE001
            bundle["fire_safety"] = {"error": str(err)}

    if write_audit is not None:
        recent = write_audit.recent
        bundle["write_audit_last100"] = [e.as_dict() for e in recent[-100:]]
        bundle["write_audit_stats"] = {
            "total_count": write_audit.total_count,
            "error_count_5min": write_audit.error_count_5min,
            "write_rate_per_min": write_audit.write_rate_per_min,
        }

    if scheduler is not None and hasattr(scheduler, "get_stats"):
        bundle["scheduler_stats"] = scheduler.get_stats()

    if proxy is not None:
        try:
            bundle["proxy_state"] = {
                "running": proxy.running,
                "client_count": len(proxy._clients),
                "fc06_count": proxy._fc06_count,
                "fc16_count": proxy._fc16_count,
                "last_ext_writes_seconds_ago": {
                    str(addr): round(time.monotonic() - ts, 1)
                    for addr, ts in proxy._last_ext_write.items()
                },
            }
        except Exception as err:  # noqa: BLE001
            bundle["proxy_state"] = {"error": str(err)}

    if modbus_coord is not None:
        bundle["modbus_snapshot"] = dict(modbus_coord.data or {})
        bundle["coordinator_state"] = {
            "update_count": modbus_coord.update_count,
            "poll_phase": modbus_coord.poll_phase,
            "slow_data_age_s": modbus_coord.slow_data_age_s,
            "slow_poll_stale": modbus_coord.slow_poll_stale,
            "fast_error_count": modbus_coord._fast_error_count,
        }

    process_coord = entry_store.get("process_coordinator")
    if process_coord is not None:
        try:
            rest_data = getattr(process_coord, "data", None) or {}
            # ProcessDataUpdateCoordinator.data: dict[module_id, dict[key, str]].
            # Coerce nested values so json.dump() with default=str handles any
            # exotic types deterministically.
            bundle["rest_snapshot"] = {
                str(mod): dict(values) if isinstance(values, dict) else values
                for mod, values in rest_data.items()
            }
        except Exception as err:  # noqa: BLE001
            bundle["rest_snapshot"] = {"error": str(err)}

    bundle = async_redact_data(bundle, TO_REDACT)

    _now = datetime.now(tz=timezone.utc)
    ts_str = _now.strftime("%Y%m%dT%H%M%S") + f"{_now.microsecond // 1000:03d}"
    filename = f"kore_debug_{entry_id[:8]}_{ts_str}.json"
    path = hass.config.path(DEBUG_BUNDLE_DIR, filename)

    def _write(p: str, data: dict[str, Any]) -> None:
        directory = os.path.dirname(p)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        _prune_debug_bundles(directory, time.time())
        with open(p, "w", encoding="utf-8", opener=_private_opener) as fh:
            json.dump(data, fh, indent=2, default=str)

    try:
        await hass.async_add_executor_job(_write, path, bundle)
        _LOGGER.info("Debug bundle written to %s", path)
    except OSError as err:
        raise OSError(f"Cannot write to {path}: {err}") from err

    return path


def async_register_debug_bundle_service(hass: HomeAssistant) -> None:
    """Register the export_debug_bundle service (idempotent)."""
    async def communication_diagnostics(call: ServiceCall) -> ServiceResponse:
        return {str(entry_id): communication_snapshot(store)
                for entry_id, store in hass.data.get(DOMAIN, {}).items()
                if not str(entry_id).startswith("_") and isinstance(store, dict)}

    if not hass.services.has_service(DOMAIN, "communication_diagnostics"):
        hass.services.async_register(
            DOMAIN, "communication_diagnostics", communication_diagnostics,
            supports_response=SupportsResponse.ONLY,
        )
    _async_register_debug_bundle_view(hass)
    if hass.services.has_service(DOMAIN, SERVICE_EXPORT_DEBUG_BUNDLE):
        return

    async def _handler(call: ServiceCall) -> ServiceResponse:
        await async_require_admin(hass, call.context)
        # HTTP may have finished setting up after this integration.
        _async_register_debug_bundle_view(hass)
        return await _handle_export_debug_bundle(hass, call)

    hass.services.async_register(
        DOMAIN, SERVICE_EXPORT_DEBUG_BUNDLE, _handler,
        supports_response=SupportsResponse.OPTIONAL,
    )

    # Enforce the retention window even when no further export follows. The
    # timer outlives the last entry until every bundle has expired.
    if _DATA_DEBUG_BUNDLE_PRUNE in hass.data:
        return
    directory = hass.config.path(DEBUG_BUNDLE_DIR)

    async def _prune(_now: datetime) -> None:
        remaining = await hass.async_add_executor_job(
            _prune_debug_bundles, directory, time.time()
        )
        if not remaining and not hass.services.has_service(DOMAIN, SERVICE_EXPORT_DEBUG_BUNDLE):
            hass.data.pop(_DATA_DEBUG_BUNDLE_PRUNE)()

    hass.data[_DATA_DEBUG_BUNDLE_PRUNE] = async_track_time_interval(
        hass, _prune, DEBUG_BUNDLE_PRUNE_INTERVAL,
        name="kostal_kore debug bundle pruning", cancel_on_shutdown=True,
    )


def _async_register_debug_bundle_view(hass: HomeAssistant) -> None:
    """Register the download view once HTTP is available."""
    if getattr(hass, "http", None) is None or hass.data.get(_DATA_DEBUG_BUNDLE_VIEW):
        return
    # Views cannot be unregistered: register once per HA instance.
    hass.http.register_view(DebugBundleView(hass, hass.config.path(DEBUG_BUNDLE_DIR)))
    hass.data[_DATA_DEBUG_BUNDLE_VIEW] = True


def async_unregister_debug_bundle_service_if_unused(hass: HomeAssistant) -> None:
    """Unregister the service when no more KORE entries remain."""
    if any(not str(key).startswith("_") and isinstance(store, dict)
           for key, store in hass.data.get(DOMAIN, {}).items()):
        return
    hass.services.async_remove(DOMAIN, SERVICE_EXPORT_DEBUG_BUNDLE)
    hass.services.async_remove(DOMAIN, "communication_diagnostics")
