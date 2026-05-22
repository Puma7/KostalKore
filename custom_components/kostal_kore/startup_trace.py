"""Structured logging for config-entry setup, unload, and entity registration.

Filter Home Assistant logs with:

- ``Kostal setup trace`` — startup phases, per-platform timing, entity batches
- ``Kostal lifecycle`` — setup/unload counts, reload requests, and skip reasons
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, TypedDict, cast

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

LOG_PREFIX: str = "Kostal setup trace"
LIFECYCLE_PREFIX: str = "Kostal lifecycle"
KEY_ENTRY_LIFECYCLE: str = "_entry_lifecycle"
RAPID_CYCLE_THRESHOLD_S: float = 45.0

_LOGGER = logging.getLogger(__name__)


class EntryLifecycleStats(TypedDict):
    setup_count: int
    unload_count: int
    last_unload_mono: float | None
    last_reload_source: str | None


def entity_unique_id(entity: object) -> str:
    """Best-effort unique_id for log lines before entities are registered."""
    uid = getattr(entity, "unique_id", None)
    if uid is None:
        uid = getattr(entity, "_attr_unique_id", None)
    if uid is not None:
        return str(uid)
    name = getattr(entity, "name", None) or getattr(entity, "_attr_name", "?")
    return f"<no-uid:{name}>"


class SetupTrace:
    """Phase-oriented setup logger tied to one config entry."""

    def __init__(self, entry_id: str, title: str) -> None:
        self.entry_id = entry_id
        self.title = title
        self._t0 = time.monotonic()
        self.current_phase: str | None = None
        self.current_platform: Platform | None = None

    def _elapsed(self) -> float:
        return time.monotonic() - self._t0

    def _prefix(self) -> str:
        return f"{LOG_PREFIX} [{self.title}]"

    def phase_begin(self, phase: str, **details: object) -> None:
        """Log the start of a setup phase (login, modbus, platform forward, …)."""
        self.current_phase = phase
        extra = _format_details(details)
        _LOGGER.info(
            "%s phase BEGIN %s (+%.2fs)%s",
            self._prefix(),
            phase,
            self._elapsed(),
            extra,
        )

    def phase_end(self, phase: str, **details: object) -> None:
        """Log successful completion of a setup phase."""
        extra = _format_details(details)
        _LOGGER.info(
            "%s phase END %s (+%.2fs)%s",
            self._prefix(),
            phase,
            self._elapsed(),
            extra,
        )
        if self.current_phase == phase:
            self.current_phase = None

    def info(self, msg: str, *args: object) -> None:
        _LOGGER.info("%s " + msg, self._prefix(), *args)

    def warning(self, msg: str, *args: object) -> None:
        _LOGGER.warning("%s " + msg, self._prefix(), *args)

    def debug(self, msg: str, *args: object) -> None:
        _LOGGER.debug("%s " + msg, self._prefix(), *args)

    def log_reload_trigger(
        self,
        *,
        reason: str,
        entry_state: ConfigEntryState,
        setup_in_progress: bool,
        unload_in_progress: bool,
    ) -> None:
        _LOGGER.info(
            "%s reload trigger: %s (entry_state=%s, setup_in_progress=%s, "
            "unload_in_progress=%s, phase=%s, platform=%s)",
            self._prefix(),
            reason,
            entry_state,
            setup_in_progress,
            unload_in_progress,
            self.current_phase,
            self.current_platform,
        )

    def log_reload_skipped(self, reason: str) -> None:
        _LOGGER.info("%s reload skipped: %s", self._prefix(), reason)

    def log_unload_phase(self, phase: str, *, ok: bool | None = None) -> None:
        suffix = ""
        if ok is not None:
            suffix = f" ok={ok}"
        _LOGGER.info(
            "%s unload %s (+%.2fs)%s",
            self._prefix(),
            phase,
            self._elapsed(),
            suffix,
        )


def _format_details(details: dict[str, object]) -> str:
    if not details:
        return ""
    parts = [f"{key}={value!r}" for key, value in details.items()]
    return " (" + ", ".join(parts) + ")"


def _lifecycle_stats(hass: HomeAssistant, entry_id: str) -> EntryLifecycleStats:
    """Persistent per-entry counters (survive unload; keyed under DOMAIN)."""
    from .const import DOMAIN

    root = hass.data.setdefault(DOMAIN, {})
    store_raw = root.setdefault(KEY_ENTRY_LIFECYCLE, {})
    if not isinstance(store_raw, dict):
        store_raw = {}
        root[KEY_ENTRY_LIFECYCLE] = store_raw
    store = cast(dict[str, EntryLifecycleStats], store_raw)
    stats = store.get(entry_id)
    if stats is None:
        stats = EntryLifecycleStats(
            setup_count=0,
            unload_count=0,
            last_unload_mono=None,
            last_reload_source=None,
        )
        store[entry_id] = stats
    return stats


def log_setup_entry_lifecycle(
    hass: HomeAssistant,
    *,
    entry_id: str,
    title: str,
    entry_state: object,
) -> None:
    """Log setup begin with cycle counters (filter: ``Kostal lifecycle``)."""
    stats = _lifecycle_stats(hass, entry_id)
    stats["setup_count"] = stats["setup_count"] + 1
    setup_n = stats["setup_count"]
    unload_n = stats["unload_count"]
    last_unload = stats["last_unload_mono"]
    secs_since_unload: float | None = None
    if isinstance(last_unload, (int, float)):
        secs_since_unload = time.monotonic() - float(last_unload)
    _LOGGER.info(
        "%s [%s] setup BEGIN #%d (unload_count=%d, entry_state=%s, "
        "secs_since_last_unload=%s, last_reload_source=%r)",
        LIFECYCLE_PREFIX,
        title,
        setup_n,
        unload_n,
        entry_state,
        f"{secs_since_unload:.1f}" if secs_since_unload is not None else None,
        stats["last_reload_source"],
    )
    if (
        secs_since_unload is not None
        and secs_since_unload < RAPID_CYCLE_THRESHOLD_S
        and unload_n > 0
    ):
        _LOGGER.warning(
            "%s [%s] RAPID RELOAD CYCLE: setup #%d started %.1fs after unload #%d "
            "(threshold=%.0fs) — check logs for 'reload REQUEST' or HA/core reload",
            LIFECYCLE_PREFIX,
            title,
            setup_n,
            secs_since_unload,
            unload_n,
            RAPID_CYCLE_THRESHOLD_S,
        )


def log_unload_entry_lifecycle(
    hass: HomeAssistant,
    *,
    entry_id: str,
    title: str,
    entry_state: object,
) -> None:
    """Log unload begin and stamp time for rapid-cycle detection."""
    stats = _lifecycle_stats(hass, entry_id)
    stats["unload_count"] = stats["unload_count"] + 1
    stats["last_unload_mono"] = time.monotonic()
    _LOGGER.info(
        "%s [%s] unload BEGIN #%d (setup_count=%d, entry_state=%s, "
        "last_reload_source=%r)",
        LIFECYCLE_PREFIX,
        title,
        stats["unload_count"],
        stats["setup_count"],
        entry_state,
        stats["last_reload_source"],
    )


def log_reload_skipped_lifecycle(
    hass: HomeAssistant,
    *,
    entry_id: str,
    title: str,
    reason: str,
    entry_state: object | None = None,
) -> None:
    """Log why a reload was not triggered (always INFO)."""
    stats = _lifecycle_stats(hass, entry_id)
    _LOGGER.info(
        "%s [%s] reload SKIPPED: %s (entry_state=%s, setup_count=%d, "
        "unload_count=%d, last_reload_source=%r)",
        LIFECYCLE_PREFIX,
        title,
        reason,
        entry_state,
        stats["setup_count"],
        stats["unload_count"],
        stats["last_reload_source"],
    )


async def async_request_config_reload(
    hass: HomeAssistant,
    entry_id: str,
    *,
    source: str,
    title: str | None = None,
) -> bool:
    """Request a config-entry reload with an explicit, grep-friendly source tag."""
    stats = _lifecycle_stats(hass, entry_id)
    stats["last_reload_source"] = source
    display = title or entry_id
    entry = hass.config_entries.async_get_entry(entry_id)
    entry_state = entry.state if entry is not None else "missing"
    _LOGGER.info(
        "%s [%s] reload REQUEST source=%r (entry_state=%s, setup_count=%d, "
        "unload_count=%d)",
        LIFECYCLE_PREFIX,
        display,
        source,
        entry_state,
        stats["setup_count"],
        stats["unload_count"],
    )
    return await hass.config_entries.async_reload(entry_id)


def log_entity_batch(
    *,
    entry_title: str,
    platform: str,
    batch: str,
    entities: Sequence[object],
    sample_limit: int = 8,
) -> None:
    """Log an ``async_add_entities`` batch with count and sample unique_ids."""
    count = len(entities)
    if count == 0:
        _LOGGER.info(
            "%s [%s] %s.%s: 0 entities (skipped)",
            LOG_PREFIX,
            entry_title,
            platform,
            batch,
        )
        return
    sample_ids = [entity_unique_id(entity) for entity in entities[:sample_limit]]
    extra = count - sample_limit
    suffix = f" (+{extra} more)" if extra > 0 else ""
    _LOGGER.info(
        "%s [%s] %s.%s: registering %d entities%s — sample: %s",
        LOG_PREFIX,
        entry_title,
        platform,
        batch,
        count,
        suffix,
        sample_ids,
    )
    if count <= 40:
        _LOGGER.debug(
            "%s [%s] %s.%s full unique_ids: %s",
            LOG_PREFIX,
            entry_title,
            platform,
            batch,
            [entity_unique_id(entity) for entity in entities],
        )
