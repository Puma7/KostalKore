"""Diagnostics support for Kostal Plenticore."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import asdict  # noqa: F401
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Final

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.const import ATTR_IDENTIFIERS, CONF_PASSWORD
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv  # noqa: F401
from homeassistant.helpers.system_info import async_get_system_info
from pykoplenti import ApiException

from .const import CONF_HOST, CONF_SERVICE_CODE, DOMAIN, MAX_SANE_STRING_COUNT
from .const_ids import STRING_FEATURE_TEMPLATE, ModuleId, SettingId, string_feature_id  # noqa: F401
from .coordinator import PlenticoreConfigEntry

# Import MODBUS exception handling from coordinator
from .helper import parse_modbus_exception

_LOGGER = logging.getLogger(__name__)

SERVICE_EXPORT_DEBUG_BUNDLE = "export_debug_bundle"

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
        lambda: plenticore.client.get_setting_values(DEVICES_LOCAL_MODULE, STRING_COUNT_SETTING)
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
                plenticore.client.get_setting_values(DEVICES_LOCAL_MODULE, feature_ids),
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

    return data


# ---------------------------------------------------------------------------
# export_debug_bundle service
# ---------------------------------------------------------------------------

async def _handle_export_debug_bundle(hass: HomeAssistant, call: ServiceCall) -> None:
    """Collect a full diagnostic snapshot and write it to /config/www/."""
    bundles: list[str] = []
    errors: list[str] = []

    for entry_id, entry_store in hass.data.get(DOMAIN, {}).items():
        if not isinstance(entry_store, dict):
            continue
        try:
            path = await _export_bundle_for_entry(hass, entry_id, entry_store)
            bundles.append(path)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("export_debug_bundle failed for entry %s: %s", entry_id, err)
            errors.append(f"{entry_id}: {err}")

    if bundles:
        urls = "\n".join(f"/local/{os.path.basename(p)}" for p in bundles)
        msg = f"Debug bundle(s) written:\n{urls}"
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
    )


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
    path = f"/config/www/{filename}"

    def _write(p: str, data: dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str)

    try:
        await hass.async_add_executor_job(_write, path, bundle)
        _LOGGER.info("Debug bundle written to %s", path)
    except OSError as err:
        raise OSError(f"Cannot write to {path}: {err}") from err

    return path


def async_register_debug_bundle_service(hass: HomeAssistant) -> None:
    """Register the export_debug_bundle service (idempotent)."""
    if hass.services.has_service(DOMAIN, SERVICE_EXPORT_DEBUG_BUNDLE):
        return

    async def _handler(call: ServiceCall) -> None:
        await _handle_export_debug_bundle(hass, call)

    hass.services.async_register(DOMAIN, SERVICE_EXPORT_DEBUG_BUNDLE, _handler)


def async_unregister_debug_bundle_service_if_unused(hass: HomeAssistant) -> None:
    """Unregister the service when no more KORE entries remain."""
    if hass.data.get(DOMAIN):
        return
    hass.services.async_remove(DOMAIN, SERVICE_EXPORT_DEBUG_BUNDLE)
