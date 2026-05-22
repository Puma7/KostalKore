"""The Kostal Plenticore Solar Inverter integration.

This integration provides monitoring and control capabilities for Kostal Plenticore
solar inverters through Home Assistant. It supports real-time data collection,
settings control, and calculated sensors.

Key Features:
- Real-time monitoring of power generation, consumption, and battery status
- Control of inverter settings and operating modes
- Calculated sensors for derived metrics (e.g., PV sum power, battery efficiency)
- Request deduplication and caching to reduce API load
- Comprehensive diagnostic support
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from datetime import timedelta
import logging
import time
from typing import Final

from aiohttp.client_exceptions import ClientError
from pykoplenti import ApiException

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_ACCESS_ROLE,
    CONF_INSTALLER_ACCESS,
    CONF_KSEM_ENABLED,
    CONF_KSEM_HOST,
    CONF_KSEM_PORT,
    CONF_KSEM_UNIT_ID,
    CONF_MODBUS_ENABLED,
    CONF_MODBUS_ENDIANNESS,
    CONF_MODBUS_PORT,
    CONF_MODBUS_PROXY_ENABLED,
    CONF_MODBUS_PROXY_BIND,
    CONF_MODBUS_PROXY_PORT,
    CONF_MODBUS_UNIT_ID,
    CONF_MQTT_BRIDGE_ENABLED,
    DEFAULT_KSEM_PORT,
    DEFAULT_KSEM_UNIT_ID,
    DEFAULT_MODBUS_PORT,
    DEFAULT_MODBUS_UNIT_ID,
    DOMAIN,
)
from .coordinator import EventDataUpdateCoordinator, Plenticore, PlenticoreConfigEntry
from .helper import parse_modbus_exception
from .battery_chemistry import detect_chemistry
from .degradation_tracker import DegradationTracker
from .diagnostics_engine import DiagnosticsEngine
from .fire_safety import FireSafetyMonitor
from .health_monitor import InverterHealthMonitor
from .longevity_advisor import LongevityAdvisor
from .modbus_client import KostalModbusClient, ModbusClientError
from .request_scheduler import RequestScheduler
from .modbus_coordinator import ModbusDataUpdateCoordinator
from .ksem_coordinator import KsemDataUpdateCoordinator
from .migration_services import (
    async_register_migration_services,
    async_unregister_migration_services_if_unused,
)
from .orphan_history import (
    async_register_orphan_history_services,
    async_unregister_orphan_history_services_if_unused,
)
from .mqtt_bridge import KostalMqttBridge
from .repairs import clear_issue, create_battery_capacity_unit_migration_issue  # GEÄNDERT
from .write_audit import WriteAuditLog

_LOGGER = logging.getLogger(__name__)

# Platform constants
PLATFORMS: Final[list[Platform]] = [
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
]

MODBUS_PLATFORMS: Final[list[Platform]] = [
    Platform.BINARY_SENSOR,
]

# Platforms that completed async_forward_entry_setups for this entry.
KEY_LOADED_PLATFORMS: Final[str] = "_loaded_platforms"
KEY_SETUP_IN_PROGRESS: Final[str] = "_setup_in_progress"

# Performance constants
SETUP_TIMEOUT_SECONDS: Final[float] = 90.0
UNLOAD_TIMEOUT_SECONDS: Final[float] = 5.0
EVENT_POLL_INTERVAL_SECONDS: Final[int] = 30

# Performance metrics constants
MEMORY_CLEANUP_MAX_MS: Final[int] = 3000


def _handle_init_error(err: Exception, operation: str) -> bool:
    """
    Handle initialization errors with appropriate logging.

    Args:
        err: Exception that occurred
        operation: Description of the operation being performed

    Returns:
        False to indicate failure
    """
    if isinstance(err, ApiException):
        modbus_err = parse_modbus_exception(err)
        _LOGGER.error("API error during %s: %s", operation, modbus_err.message)
    elif isinstance(err, TimeoutError):
        _LOGGER.warning("Timeout during %s", operation)
    elif isinstance(err, (ClientError, asyncio.TimeoutError)):
        _LOGGER.error("Network error during %s: %s", operation, err)
    else:
        _LOGGER.error("Unexpected error during %s: %s", operation, err)

    return False


def _log_setup_metrics(start_time: float, setup_success: bool) -> None:
    """
    Log setup performance metrics.

    Args:
        start_time: Setup start time
        setup_success: Whether setup was successful
    """
    setup_time = time.time() - start_time
    if setup_success:
        _LOGGER.info("Kostal Plenticore setup completed in %.2fs", setup_time)
    else:
        _LOGGER.warning("Kostal Plenticore setup failed after %.2fs", setup_time)


async def async_setup_entry(hass: HomeAssistant, entry: PlenticoreConfigEntry) -> bool:
    """Set up the Kostal Plenticore integration.

    Initializes the API client, authenticates with the inverter,
    and forwards platform setup (sensors, switches, numbers, selects).
    """
    start_time = time.time()

    request_scheduler = RequestScheduler()
    plenticore = Plenticore(hass, entry)
    plenticore._request_scheduler = request_scheduler

    try:
        setup_success = await asyncio.wait_for(
            plenticore.async_setup(), timeout=SETUP_TIMEOUT_SECONDS
        )
    except Exception as err:
        setup_success = _handle_init_error(err, "setup")

    if not setup_success:
        await plenticore.async_unload()
        _log_setup_metrics(start_time, False)
        return False

    # Clear both legacy (unscoped) and new (entry-scoped) issue IDs so that
    # issues created by a previous version are also dismissed after upgrade.
    for _suffix in ("auth_failed", "api_unreachable", "inverter_busy", "installer_required"):
        clear_issue(hass, _suffix)  # legacy unscoped ID
        clear_issue(hass, _suffix, entry_id=entry.entry_id)  # new scoped ID

    # One-shot migration notice for Battery Work Capacity unit (Ah -> Wh).
    # Triggered only when the entity registry still records the old "Ah" unit.
    from homeassistant.helpers import entity_registry as er
    _ent_reg = er.async_get(hass)
    _existing_id = _ent_reg.async_get_entity_id(
        "sensor",
        DOMAIN,
        f"{entry.entry_id}_devices:local:battery_WorkCapacity",
    )
    if _existing_id is not None:
        _entry_reg = _ent_reg.async_get(_existing_id)
        _effective_unit = (
            _entry_reg.unit_of_measurement if _entry_reg is not None else None
        )
        if _effective_unit == "Ah":
            create_battery_capacity_unit_migration_issue(hass, entry_id=entry.entry_id)
        else:
            clear_issue(hass, "battery_capacity_unit_migration", entry_id=entry.entry_id)
    else:
        clear_issue(hass, "battery_capacity_unit_migration", entry_id=entry.entry_id)

    entry.runtime_data = plenticore

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    # Event intelligence coordinator (REST /events/latest), independent from
    # process/settings coordinators so transient event API failures don't impact
    # regular entity updates.
    event_coordinator = EventDataUpdateCoordinator(
        hass=hass,
        config_entry=entry,
        logger=_LOGGER,
        name="Event Data",
        update_interval=timedelta(seconds=EVENT_POLL_INTERVAL_SECONDS),
        plenticore=plenticore,
    )
    try:
        await event_coordinator.async_config_entry_first_refresh()
    except Exception as event_err:
        _LOGGER.debug("Initial event refresh failed (non-fatal): %s", event_err)

    # Optional Modbus TCP setup
    modbus_coordinator = None
    ksem_coordinator = None
    mqtt_bridge = None
    modbus_proxy = None
    soc_controller = None
    # Default to False when the persisted flag is missing. The config flow
    # already evaluates _installer_access_from_role() and stores the result;
    # the legacy "service code present ⇒ installer access" fallback would
    # bypass that role check (e.g. USER + service code, which the wizard
    # explicitly denies). Safer to be locked out than to silently unlock
    # writes for an unknown role.
    installer_access = bool(entry.data.get(CONF_INSTALLER_ACCESS, False))
    access_role = str(entry.data.get(CONF_ACCESS_ROLE, "UNKNOWN"))
    _LOGGER.info(
        "Authenticated inverter access role: %s (installer writes: %s)",
        access_role,
        installer_access,
    )
    if entry.options.get(CONF_MODBUS_ENABLED, False):
        host = entry.data[CONF_HOST]
        port = entry.options.get(CONF_MODBUS_PORT, DEFAULT_MODBUS_PORT)
        unit_id = entry.options.get(CONF_MODBUS_UNIT_ID, DEFAULT_MODBUS_UNIT_ID)
        endianness = entry.options.get(CONF_MODBUS_ENDIANNESS, "auto")

        modbus_client = KostalModbusClient(
            host=host,
            port=port,
            unit_id=unit_id,
            endianness="little" if endianness == "auto" else endianness,
            request_scheduler=request_scheduler,
        )
        modbus_coordinator = ModbusDataUpdateCoordinator(hass, modbus_client)
        try:
            await modbus_coordinator.async_setup()
            # detect_endianness() is already called inside async_setup(),
            # no need to call it again here.
            _LOGGER.info("Modbus TCP connected to %s:%s", host, port)
        except Exception as err:
            _LOGGER.warning(
                "Modbus TCP setup failed: %s (REST API still active)", err
            )
            modbus_coordinator = None

        # SoC Controller (must be created before MQTT bridge + proxy for arbitration)
        if modbus_coordinator is not None:  # pragma: no cover
            from .battery_soc_controller import BatterySocController
            soc_controller = BatterySocController(modbus_coordinator, hass=hass, entry_id=entry.entry_id)

        if modbus_coordinator and entry.options.get(
            CONF_MQTT_BRIDGE_ENABLED, False
        ):
            # Extract device identifier from DeviceInfo identifiers set.
            device_id: str = str(entry.entry_id)
            for _domain, _ident in plenticore.device_info.get("identifiers", set()):
                if _domain == DOMAIN and _ident != "unknown":
                    device_id = str(_ident)
                    break
            mqtt_bridge = KostalMqttBridge(
                hass, modbus_coordinator, str(device_id),
                soc_controller=soc_controller,
                installer_access=installer_access,
            )
            await mqtt_bridge.async_start()

        if modbus_coordinator and entry.options.get(  # pragma: no cover
            CONF_MODBUS_PROXY_ENABLED, False
        ):
            from .modbus_proxy import (
                ModbusTcpProxyServer,
                DEFAULT_PROXY_PORT,
                DEFAULT_PROXY_BIND,
            )

            proxy_port = entry.options.get(CONF_MODBUS_PROXY_PORT, DEFAULT_PROXY_PORT)
            proxy_bind = str(entry.options.get(CONF_MODBUS_PROXY_BIND, DEFAULT_PROXY_BIND))
            endianness = entry.options.get(CONF_MODBUS_ENDIANNESS, "auto")
            if endianness == "auto":
                endianness = modbus_client.endianness if hasattr(modbus_client, "endianness") else "little"
            modbus_proxy = ModbusTcpProxyServer(
                modbus_coordinator,
                port=int(proxy_port),
                bind_host=proxy_bind,
                unit_id=int(entry.options.get(CONF_MODBUS_UNIT_ID, DEFAULT_MODBUS_UNIT_ID)),
                endianness=endianness,
                soc_controller=soc_controller,
                installer_access=installer_access,
            )
            try:
                await modbus_proxy.start()
            except OSError as proxy_err:
                _LOGGER.warning("Modbus proxy failed to start on port %s: %s", proxy_port, proxy_err)
                modbus_proxy = None

    # Optional standalone KSEM source (separate failure domain).
    if entry.options.get(CONF_KSEM_ENABLED, False):  # pragma: no cover
        ksem_host = str(entry.options.get(CONF_KSEM_HOST, "")).strip() or str(
            entry.data[CONF_HOST]
        )
        ksem_port = int(entry.options.get(CONF_KSEM_PORT, DEFAULT_KSEM_PORT))
        ksem_unit_id = int(
            entry.options.get(CONF_KSEM_UNIT_ID, DEFAULT_KSEM_UNIT_ID)
        )
        ksem_coordinator = KsemDataUpdateCoordinator(
            hass=hass,
            config_entry=entry,
            host=ksem_host,
            port=ksem_port,
            unit_id=ksem_unit_id,
        )
        try:
            await ksem_coordinator.async_setup()
            await ksem_coordinator.async_config_entry_first_refresh()
            _LOGGER.info(
                "KSEM source active on %s:%s (unit %s)",
                ksem_host,
                ksem_port,
                ksem_unit_id,
            )
        except Exception as ksem_err:
            if ksem_coordinator is not None:
                try:
                    await ksem_coordinator.async_shutdown()
                except Exception as shutdown_err:
                    _LOGGER.debug(
                        "KSEM cleanup after setup failure also failed: %s", shutdown_err
                    )
            _LOGGER.warning(
                "KSEM setup failed (%s:%s, unit %s): %s",
                ksem_host,
                ksem_port,
                ksem_unit_id,
                ksem_err,
            )
            ksem_coordinator = None

    # Health + Fire Safety + Degradation monitors
    health_monitor = None
    fire_safety = None
    degradation_tracker = None
    battery_soh_calc = None
    if modbus_coordinator is not None:
        num_bi = 0
        _bi_raw = modbus_coordinator.device_info_data.get("num_bidirectional")
        if _bi_raw is not None:  # pragma: no cover
            try:
                num_bi = int(_bi_raw)
            except (TypeError, ValueError):
                pass
        health_monitor = InverterHealthMonitor(num_bidirectional=num_bi)
        fire_safety = FireSafetyMonitor(num_bidirectional=num_bi)
        degradation_tracker = DegradationTracker()
        from .battery_soh_calculator import BatterySohCalculator
        battery_soh_calc = BatterySohCalculator(
            hass,
            f"kostal_kore_battery_soh_{entry.entry_id}",
        )
        await battery_soh_calc.async_load()
        _clear_sent: dict[str, bool] = {"value": False}

        modbus_coordinator._health_monitor = health_monitor
        # Await restore synchronously so the listener registered below cannot
        # observe a partially-restored isolation deque. The task-based form
        # had a race where the first coordinator update arrived before the
        # restore coroutine ran.
        await modbus_coordinator._restore_isolation_sample()

        @callback
        def _feed_health_data() -> None:  # pragma: no cover
            data = modbus_coordinator.data
            if data:
                health_monitor.update_from_modbus(data)
                degradation_tracker.update_from_modbus(data)
                if battery_soh_calc.update_from_modbus(data):
                    battery_soh_calc.schedule_save()
                iso_current = health_monitor.isolation.current
                if iso_current is not None:
                    hass.async_create_task(
                        modbus_coordinator._save_isolation_sample(iso_current)
                    )
                new_alerts = fire_safety.analyze(data)
                if new_alerts:
                    _clear_sent["value"] = False
                    from .notifications import notify_safety_alert, notify_safety_clear
                    for alert in new_alerts:
                        hass.async_create_task(
                            notify_safety_alert(
                                hass, alert.risk_level, alert.title,
                                alert.detail, alert.action,
                                entry_id=entry.entry_id,
                                category=getattr(alert, "category", ""),
                            )
                        )
                elif fire_safety.alert_count == 0 and fire_safety._total_polls > 0:
                    if not _clear_sent["value"]:
                        _clear_sent["value"] = True
                        from .notifications import notify_safety_clear
                        hass.async_create_task(notify_safety_clear(hass, entry_id=entry.entry_id))

        # Defer subscription until platform setup succeeds (below) so a
        # ConfigEntryNotReady rollback does not leave a listener firing on a
        # coordinator that is being shut down.
        _feed_health_listener = _feed_health_data
        fire_safety._total_polls = 0
    else:
        _feed_health_listener = None

    diagnostics_engine = None
    longevity_advisor = None
    if health_monitor is not None and fire_safety is not None:
        diagnostics_engine = DiagnosticsEngine(health_monitor, fire_safety)
        bat_type = modbus_coordinator.device_info_data.get("battery_type") if modbus_coordinator else None
        bat_type_int = None  # pragma: no cover
        if bat_type is not None:  # pragma: no cover
            try:
                bat_type_int = int(bat_type)
            except (TypeError, ValueError):
                pass
        bat_thresholds = detect_chemistry(bat_type_int)
        longevity_advisor = LongevityAdvisor(health_monitor, bat_thresholds)
        _LOGGER.info("Battery chemistry: %s (%s)", bat_thresholds.chemistry, bat_thresholds.chemistry_full)

    write_audit = WriteAuditLog()
    if modbus_coordinator is not None:
        modbus_coordinator._write_audit = write_audit

    # Pre-instantiate the Grid Feed-In Limiter switch BEFORE forwarding platform
    # setup. number.py and switch.py both reach for this object, and platform
    # setup runs them concurrently — without pre-instantiation the number
    # platform raced ahead of the switch platform and found "grid_feedin_limiter"
    # missing, silently dropping the FeedInLimitNumber entity.
    grid_feedin_limiter = None
    if modbus_coordinator is not None and entry.options.get(CONF_MODBUS_ENABLED, False):
        try:
            from .grid_charge_limiter import GridFeedInLimiterSwitch
            grid_feedin_limiter = GridFeedInLimiterSwitch(
                modbus_coordinator, entry.entry_id, plenticore.device_info, hass=hass,
            )
        except Exception as err:  # pragma: no cover
            _LOGGER.error("Could not pre-instantiate Grid Feed-In Limiter: %s", err)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        KEY_SETUP_IN_PROGRESS: True,
        KEY_LOADED_PLATFORMS: [],
        "modbus_coordinator": modbus_coordinator,
        "ksem_coordinator": ksem_coordinator,
        "event_coordinator": event_coordinator,
        "mqtt_bridge": mqtt_bridge,
        "modbus_proxy": modbus_proxy if modbus_coordinator is not None else None,
        "health_monitor": health_monitor,
        "fire_safety": fire_safety,
        "degradation_tracker": degradation_tracker,
        "battery_soh_calc": battery_soh_calc,
        "grid_feedin_limiter": grid_feedin_limiter,
        "diagnostics_engine": diagnostics_engine,
        "longevity_advisor": longevity_advisor,
        "request_scheduler": request_scheduler,
        "soc_controller": soc_controller,
        "num_bidirectional": num_bi if modbus_coordinator is not None else 0,
        "write_audit": write_audit,
    }
    entry_store = hass.data[DOMAIN][entry.entry_id]

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        entry_store[KEY_LOADED_PLATFORMS].extend(PLATFORMS)
        if modbus_coordinator is not None:
            # Modbus platforms expose fire-safety and health binary sensors,
            # so a silent skip would hide critical safety entities. Surface
            # the failure as ConfigEntryNotReady so HA can retry, instead of
            # returning True with the binary-sensor platform missing.
            try:
                await hass.config_entries.async_forward_entry_setups(entry, MODBUS_PLATFORMS)
                entry_store[KEY_LOADED_PLATFORMS].extend(MODBUS_PLATFORMS)
            except Exception as modbus_err:
                _LOGGER.error(
                    "Modbus platform setup failed - safety binary sensors will be missing: %s",
                    modbus_err,
                )
                raise ConfigEntryNotReady(
                    f"Modbus platform setup failed: {modbus_err}"
                ) from modbus_err
    except ConfigEntryNotReady:
        # Let HA retry; still roll back the runtime objects we started.
        await _rollback_setup(hass, entry, plenticore)
        _log_setup_metrics(start_time, False)
        raise
    except Exception as err:
        _handle_init_error(err, "platform setup")
        # Rollback runtime objects that were started before platform forwarding
        await _rollback_setup(hass, entry, plenticore)
        _log_setup_metrics(start_time, False)
        return False

    if _feed_health_listener is not None and modbus_coordinator is not None:
        _unsub_health = modbus_coordinator.async_add_listener(_feed_health_listener)
        entry.async_on_unload(_unsub_health)

    async_register_migration_services(hass)
    async_register_orphan_history_services(hass)
    from .diagnostics import async_register_debug_bundle_service
    async_register_debug_bundle_service(hass)
    # Save normalized options AFTER successful setup so the options-update
    # listener can compare like-for-like and not retrigger reloads when HA
    # canonicalizes the dict between writes.
    from .config_flow import _normalize_options
    entry_store[KEY_SETUP_IN_PROGRESS] = False
    entry_store["_setup_options"] = _normalize_options(entry.options)
    _log_setup_metrics(start_time, True)
    return True


def _platforms_to_unload(entry_data: dict[str, object]) -> list[Platform]:
    """Return platforms that completed setup; tolerate missing tracking in tests."""
    loaded = entry_data.get(KEY_LOADED_PLATFORMS)
    if isinstance(loaded, list):
        return loaded
    # Legacy / test path: no tracking present. Assume PLATFORMS were forwarded
    # and include MODBUS_PLATFORMS only when a modbus coordinator exists.
    platforms: list[Platform] = list(PLATFORMS)
    if entry_data.get("modbus_coordinator") is not None:
        platforms = list(MODBUS_PLATFORMS) + platforms
    return platforms


async def _async_unload_loaded_platforms(
    hass: HomeAssistant,
    entry: PlenticoreConfigEntry,
    loaded: list[Platform],
) -> bool:
    """Unload only platforms that were forwarded; skip HA 'never loaded' races."""
    if not loaded:
        return True
    modbus_loaded = [p for p in MODBUS_PLATFORMS if p in loaded]
    main_loaded = [p for p in PLATFORMS if p in loaded]
    unload_ok = True
    for platforms in (modbus_loaded, main_loaded):
        if not platforms:
            continue
        try:
            unload_ok = (
                await hass.config_entries.async_unload_platforms(entry, platforms)
                and unload_ok
            )
        except ValueError as err:
            # HA can race "Config entry was never loaded!" during reload cycles
            # where the platform was forwarded earlier but already torn down by
            # HA's own machinery. Tolerate that exact message and keep going.
            if "never loaded" not in str(err).lower():
                raise
            _LOGGER.debug(
                "Skipped unload of %s for %s (platform not registered with HA)",
                platforms,
                entry.title,
            )
    return unload_ok


async def _rollback_setup(
    hass: HomeAssistant,
    entry: PlenticoreConfigEntry,
    plenticore: Plenticore,
) -> None:
    """Clean up runtime objects after a failed platform-forwarding attempt."""
    entry_data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, {})
    loaded = _platforms_to_unload(entry_data)
    if loaded:
        await _async_unload_loaded_platforms(hass, entry, loaded)
    proxy = entry_data.get("modbus_proxy")
    if proxy:
        await _await_cleanup_step("Modbus proxy stop", proxy.stop())
    mqtt = entry_data.get("mqtt_bridge")
    if mqtt:
        await _await_cleanup_step("MQTT bridge stop", mqtt.async_stop())
    # Coordinators that may have started background polling / TCP connections
    # before the platform forward failed must be shut down too — leaving them
    # running creates zombie tasks that keep talking to the inverter until
    # HA is restarted.
    soc_ctrl = entry_data.get("soc_controller")
    if soc_ctrl:
        await _await_cleanup_step("SoC controller stop", soc_ctrl.stop())
    modbus_coordinator = entry_data.get("modbus_coordinator")
    if modbus_coordinator is not None:
        await _await_cleanup_step(
            "Modbus coordinator shutdown", modbus_coordinator.async_shutdown()
        )
    ksem_coordinator = entry_data.get("ksem_coordinator")
    if ksem_coordinator is not None:
        await _await_cleanup_step(
            "KSEM coordinator shutdown", ksem_coordinator.async_shutdown()
        )
    event_coordinator = entry_data.get("event_coordinator")
    if event_coordinator is not None:
        await _await_cleanup_step(
            "Event coordinator shutdown", event_coordinator.async_shutdown()
        )
    await plenticore.async_unload()
    _LOGGER.warning("Rolled back partial setup for %s", entry.title)


async def _async_options_updated(
    hass: HomeAssistant, entry: PlenticoreConfigEntry
) -> None:
    """Reload integration when options actually change.

    HA fires options updates during the unload-reload gap and while setup
    is still in progress. Triggering async_reload in those windows produced
    a self-sustaining reload loop where each cycle's teardown immediately
    spawned another reload (visible as "Config entry was never loaded!"
    for binary_sensor right after a successful setup). Guard the listener
    so it only fires when there are actual, normalized option changes.
    """
    domain_data = hass.data.get(DOMAIN, {})
    entry_data = domain_data.get(entry.entry_id)
    if entry_data is None:
        # Unload/reload gap: hass.data was popped but HA may still emit updates.
        _LOGGER.debug(
            "Options update for %s ignored (integration not loaded)",
            entry.entry_id,
        )
        return
    if entry_data.get(KEY_SETUP_IN_PROGRESS):
        _LOGGER.debug(
            "Options update for %s ignored (setup still in progress)",
            entry.entry_id,
        )
        return
    if entry.state is not ConfigEntryState.LOADED:
        _LOGGER.debug(
            "Options update for %s ignored (entry state=%s)",
            entry.entry_id,
            entry.state,
        )
        return

    from .config_flow import _normalize_options

    new_options = _normalize_options(entry.options)
    prev = entry_data.get("_setup_options")
    if prev is not None and new_options == prev:
        _LOGGER.debug(
            "Config entry %s updated but options unchanged – skipping reload",
            entry.entry_id,
        )
        return
    _LOGGER.info("Options changed for %s, reloading integration", entry.title)
    await hass.config_entries.async_reload(entry.entry_id)


async def _await_cleanup_step(
    label: str,
    step: Awaitable[object],
    *,
    timeout: float = UNLOAD_TIMEOUT_SECONDS,
) -> None:
    """Await a cleanup coroutine with timeout protection."""
    try:
        await asyncio.wait_for(step, timeout=timeout)
    except asyncio.TimeoutError:
        _LOGGER.warning("%s timed out after %.1fs during unload", label, timeout)
    except Exception as err:  # pragma: no cover - defensive logging
        _LOGGER.debug("%s failed during unload: %s", label, err)


async def async_unload_entry(hass: HomeAssistant, entry: PlenticoreConfigEntry) -> bool:
    """Unload the Kostal Plenticore integration with graceful cleanup."""
    start_time = time.time()

    # Read entry_data WITHOUT popping. The platform unload below uses these
    # references (entities call coordinator.async_remove_listener etc.), and
    # if platform unload fails HA will retry — leaving the store intact lets
    # the retry find the same coordinator instances instead of building new
    # zombies on top of half-stopped ones.
    domain_store = hass.data.get(DOMAIN, {})
    entry_data = domain_store.get(entry.entry_id, {})
    loaded = _platforms_to_unload(entry_data)

    # Stop Modbus consumers before tearing down the shared client/coordinator.
    # SoC/grid limiter may still write registers; stopping them first prevents
    # write attempts against a half-closed client.  Then shut the coordinator
    # down (closes TCP) so HA does not log "refresh did not complete in time"
    # while the platform entities unload.
    soc_ctrl = entry_data.get("soc_controller")
    if soc_ctrl:  # pragma: no cover
        await _await_cleanup_step("SoC controller stop", soc_ctrl.stop())
    modbus_proxy = entry_data.get("modbus_proxy")
    if modbus_proxy:  # pragma: no cover
        await _await_cleanup_step("Modbus proxy stop", modbus_proxy.stop())
    mqtt_bridge = entry_data.get("mqtt_bridge")
    if mqtt_bridge:
        await _await_cleanup_step("MQTT bridge stop", mqtt_bridge.async_stop())
    modbus_coordinator = entry_data.get("modbus_coordinator")
    if modbus_coordinator:
        await _await_cleanup_step(
            "Modbus coordinator shutdown", modbus_coordinator.async_shutdown()
        )

    # Tear down entity platforms while coordinators are stopped or idle —
    # entities call coordinator.async_remove_listener during their teardown.
    unload_ok = await _async_unload_loaded_platforms(hass, entry, loaded)
    ksem_coordinator = entry_data.get("ksem_coordinator")
    if ksem_coordinator:  # pragma: no cover
        await _await_cleanup_step(
            "KSEM coordinator shutdown", ksem_coordinator.async_shutdown()
        )
    # Mirror _rollback_setup: explicitly stop the event coordinator's polling
    # so a reload race cannot leave a zombie task talking to the inverter
    # while platform entities are torn down.
    event_coordinator = entry_data.get("event_coordinator")
    if event_coordinator is not None:
        await _await_cleanup_step(
            "Event coordinator shutdown", event_coordinator.async_shutdown()
        )

    if unload_ok:
        # Only drop the entry store once every platform has unloaded; on
        # failure HA can retry and find the same in-flight objects.
        domain_store.pop(entry.entry_id, None)
        try:
            await asyncio.wait_for(
                entry.runtime_data.async_unload(), timeout=UNLOAD_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            _LOGGER.warning("Timeout during inverter logout")
        except ApiException as err:
            _LOGGER.error("Error logging out from inverter: %s", err)
        except Exception as err:
            _LOGGER.error("Unexpected error during inverter logout: %s", err)

    cleanup_time = time.time() - start_time
    if cleanup_time > MEMORY_CLEANUP_MAX_MS / 1000:
        _LOGGER.warning(
            "Cleanup took %.2fs (expected < %.1fs)",
            cleanup_time,
            MEMORY_CLEANUP_MAX_MS / 1000,
        )

    async_unregister_migration_services_if_unused(
        hass,
        unloading_entry_id=entry.entry_id,
    )
    async_unregister_orphan_history_services_if_unused(
        hass,
        unloading_entry_id=entry.entry_id,
    )
    from .diagnostics import async_unregister_debug_bundle_service_if_unused
    async_unregister_debug_bundle_service_if_unused(hass)
    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: PlenticoreConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Refuse to remove the primary inverter device while the entry is active.

    HA exposes a "Delete device" button on every device tile. For this
    integration, the primary device IS the config entry's representation —
    removing it while the entry is loaded leaves orphan entities and a
    confusing UI. Stale auxiliary devices (e.g. legacy duplicates from older
    versions) remain removable.
    """
    plenticore = getattr(config_entry, "runtime_data", None)
    if plenticore is None:
        # Entry unloaded or never set up — let HA clean up.
        return True

    primary_id: str | None = None
    try:
        primary_id = plenticore._get_persistent_device_id()  # noqa: SLF001
    except Exception:  # noqa: BLE001 – defensive; do not block UI on lookup
        primary_id = None

    if primary_id is None:
        # The entry is loaded (runtime_data is set) but we cannot identify
        # the primary device. Refuse the deletion: allowing it here would
        # let a transient registry / lookup hiccup wipe the user's main
        # inverter tile and leave orphan entities behind. The user can
        # still remove the integration via "Delete config entry".
        _LOGGER.warning(
            "Refusing device removal for active entry %s: primary device id lookup failed",
            config_entry.entry_id,
        )
        return False

    for domain, identifier in device_entry.identifiers:
        if domain == DOMAIN and identifier == primary_id:
            return False
    return True
