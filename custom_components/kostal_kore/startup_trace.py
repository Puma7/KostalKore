"""Structured logging for config-entry setup, unload, and entity registration.

Filter Home Assistant logs with ``Kostal setup trace`` to follow startup phases,
per-platform timing, and entity batches during reload loops.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform

LOG_PREFIX: str = "Kostal setup trace"

_LOGGER = logging.getLogger(__name__)


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
        _LOGGER.debug("%s reload skipped: %s", self._prefix(), reason)

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
