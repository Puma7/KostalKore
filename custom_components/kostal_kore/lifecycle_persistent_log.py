"""Append-only lifecycle log files that survive config-entry reloads.

Logs are written under ``<config>/custom_components/kostal_kore/logs/`` so they
remain available after HA log rotation or integration unload/reload. Enable via
integration options (on by default for reload-loop diagnosis).
"""

from __future__ import annotations

import logging
import re
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from .const import CONF_LIFECYCLE_FILE_LOG, CONF_LIFECYCLE_FILE_VERBOSE

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

LOG_SUBDIR: str = "custom_components/kostal_kore/logs"
MAX_FILE_BYTES: int = 2 * 1024 * 1024
MAX_BACKUP_COUNT: int = 2
_ENTRY_ID_SAFE = re.compile(r"[^a-zA-Z0-9_-]+")

_locks: dict[str, threading.Lock] = {}


def _lock_for(entry_id: str) -> threading.Lock:
    lock = _locks.get(entry_id)
    if lock is None:
        lock = threading.Lock()
        _locks[entry_id] = lock
    return lock


def _safe_entry_slug(entry_id: str) -> str:
    return _ENTRY_ID_SAFE.sub("_", entry_id)[:48]


def lifecycle_log_path(hass: HomeAssistant, entry_id: str) -> Path:
    """Absolute path to the lifecycle log file for one config entry."""
    filename = f"lifecycle_{_safe_entry_slug(entry_id)}.log"
    return Path(hass.config.config_dir) / LOG_SUBDIR / filename


def lifecycle_file_log_enabled(hass: HomeAssistant, entry_id: str) -> bool:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        return True
    return bool(entry.options.get(CONF_LIFECYCLE_FILE_LOG, True))


def lifecycle_file_log_verbose(hass: HomeAssistant, entry_id: str) -> bool:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        return False
    return bool(entry.options.get(CONF_LIFECYCLE_FILE_VERBOSE, False))


def _rotate_if_needed(path: Path) -> None:
    if not path.exists() or path.stat().st_size <= MAX_FILE_BYTES:
        return
    for index in range(MAX_BACKUP_COUNT, 0, -1):
        older = path.with_name(f"{path.name}.{index}")
        newer = path.with_name(f"{path.name}.{index + 1}")
        if older.exists():
            if index == MAX_BACKUP_COUNT:
                older.unlink(missing_ok=True)
            else:
                older.replace(newer)
    backup = path.with_name(f"{path.name}.1")
    path.replace(backup)


def _append_sync(hass: HomeAssistant, entry_id: str, message: str) -> None:
    path = lifecycle_log_path(hass, entry_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = _lock_for(entry_id)
    with lock:
        _rotate_if_needed(path)
        stamp = datetime.now(tz=timezone.utc).isoformat()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")


def schedule_lifecycle_file_log(
    hass: HomeAssistant,
    entry_id: str,
    message: str,
    *,
    verbose_only: bool = False,
    include_stack: bool = False,
) -> None:
    """Queue a line for the persistent log (non-blocking)."""
    if not lifecycle_file_log_enabled(hass, entry_id):
        return
    if verbose_only and not lifecycle_file_log_verbose(hass, entry_id):
        return
    body = message
    if include_stack and lifecycle_file_log_verbose(hass, entry_id):
        stack = "".join(traceback.format_stack(limit=14)[:-1])
        body = f"{message}\n--- stack ---\n{stack}--- end stack ---"
    hass.async_create_task(
        async_append_lifecycle_file_log(hass, entry_id, body, verbose_only=False)
    )


async def async_append_lifecycle_file_log(
    hass: HomeAssistant,
    entry_id: str,
    message: str,
    *,
    verbose_only: bool = False,
) -> None:
    """Append one line (or block) to the on-disk lifecycle log."""
    if not lifecycle_file_log_enabled(hass, entry_id):
        return
    if verbose_only and not lifecycle_file_log_verbose(hass, entry_id):
        return
    try:
        await hass.async_add_executor_job(_append_sync, hass, entry_id, message)
    except Exception as err:  # pragma: no cover - defensive
        _LOGGER.debug("Lifecycle file log write failed: %s", err)


def read_lifecycle_log_tail(
    hass: HomeAssistant, entry_id: str, *, max_lines: int = 200
) -> list[str]:
    """Return the last *max_lines* from the on-disk log (for debug bundles)."""
    path = lifecycle_log_path(hass, entry_id)
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = text.splitlines()
    return lines[-max_lines:]


def log_session_banner(
    hass: HomeAssistant,
    *,
    entry_id: str,
    title: str,
    event: str,
    detail: str,
    log_path_info: bool = True,
) -> None:
    """Write a visible session separator; optionally log the file path at INFO."""
    if not lifecycle_file_log_enabled(hass, entry_id):
        return
    path = lifecycle_log_path(hass, entry_id)
    if log_path_info:
        from .startup_trace import LIFECYCLE_PREFIX

        _LOGGER.info(
            "%s [%s] lifecycle file log: %s",
            LIFECYCLE_PREFIX,
            title,
            path,
        )
    schedule_lifecycle_file_log(
        hass,
        entry_id,
        f"========== {event} ========== {detail} path={path}",
    )
