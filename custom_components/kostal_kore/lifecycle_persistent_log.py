"""Append-only lifecycle log files that survive config-entry reloads.

Logs are written under ``<config>/custom_components/kostal_kore/logs/`` as one file
per UTC day (``lifecycle_<entry>.YYYY-MM-DD.log``). At most five daily files are
kept (~5×24 h). Disabled by default to reduce SD/flash wear — enable in options.
"""

from __future__ import annotations

import logging
import re
import threading
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from .const import CONF_LIFECYCLE_FILE_LOG, CONF_LIFECYCLE_FILE_VERBOSE

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

LOG_SUBDIR: str = "custom_components/kostal_kore/logs"
RETENTION_DAYS: int = 5
MAX_DAILY_FILE_BYTES: int = 1 * 1024 * 1024
_ENTRY_ID_SAFE = re.compile(r"[^a-zA-Z0-9_-]+")
_DAILY_NAME = re.compile(
    r"^lifecycle_(?P<slug>[^.]+)\.(?P<day>\d{4}-\d{2}-\d{2})\.log$"
)

_locks: dict[str, threading.Lock] = {}


def _lock_for(entry_id: str) -> threading.Lock:
    lock = _locks.get(entry_id)
    if lock is None:
        lock = threading.Lock()
        _locks[entry_id] = lock
    return lock


def _safe_entry_slug(entry_id: str) -> str:
    return _ENTRY_ID_SAFE.sub("_", entry_id)[:48]


def _utc_day_string(when: datetime | None = None) -> str:
    stamp = when or datetime.now(tz=timezone.utc)
    return stamp.strftime("%Y-%m-%d")


def _log_dir(hass: HomeAssistant) -> Path:
    return Path(hass.config.config_dir) / LOG_SUBDIR


def _daily_filename(slug: str, day: str) -> str:
    return f"lifecycle_{slug}.{day}.log"


def lifecycle_log_path(hass: HomeAssistant, entry_id: str, *, day: str | None = None) -> Path:
    """Absolute path to today's (or *day*'s) lifecycle log for one config entry."""
    slug = _safe_entry_slug(entry_id)
    return _log_dir(hass) / _daily_filename(slug, day or _utc_day_string())


def lifecycle_file_log_enabled(hass: HomeAssistant, entry_id: str) -> bool:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        return False
    return bool(entry.options.get(CONF_LIFECYCLE_FILE_LOG, False))


def lifecycle_file_log_verbose(hass: HomeAssistant, entry_id: str) -> bool:
    if not lifecycle_file_log_enabled(hass, entry_id):
        return False
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        return False
    return bool(entry.options.get(CONF_LIFECYCLE_FILE_VERBOSE, False))


def _list_daily_log_files(log_dir: Path, slug: str) -> list[tuple[str, Path]]:
    if not log_dir.is_dir():
        return []
    found: list[tuple[str, Path]] = []
    for path in log_dir.iterdir():
        match = _DAILY_NAME.match(path.name)
        if match is None or match.group("slug") != slug:
            continue
        found.append((match.group("day"), path))
    return sorted(found, key=lambda item: item[0], reverse=True)


def _purge_old_daily_logs(log_dir: Path, slug: str) -> None:
    """Delete daily log files older than ``RETENTION_DAYS`` (keep newest five days)."""
    dated = _list_daily_log_files(log_dir, slug)
    if len(dated) <= RETENTION_DAYS:
        return
    cutoff = (
        datetime.now(tz=timezone.utc).date() - timedelta(days=RETENTION_DAYS - 1)
    ).isoformat()
    for day, path in dated:
        if day < cutoff:
            path.unlink(missing_ok=True)


def _purge_legacy_single_file(log_dir: Path, slug: str) -> None:
    legacy = log_dir / f"lifecycle_{slug}.log"
    legacy.unlink(missing_ok=True)
    for index in range(1, 10):
        backup = log_dir / f"lifecycle_{slug}.log.{index}"
        backup.unlink(missing_ok=True)


def _daily_file_over_limit(path: Path) -> bool:
    return path.exists() and path.stat().st_size >= MAX_DAILY_FILE_BYTES


def _append_sync(hass: HomeAssistant, entry_id: str, message: str) -> None:
    slug = _safe_entry_slug(entry_id)
    log_dir = _log_dir(hass)
    log_dir.mkdir(parents=True, exist_ok=True)
    path = lifecycle_log_path(hass, entry_id)
    lock = _lock_for(entry_id)
    with lock:
        _purge_legacy_single_file(log_dir, slug)
        _purge_old_daily_logs(log_dir, slug)
        if _daily_file_over_limit(path):
            _LOGGER.debug(
                "Lifecycle file log skipped for %s: daily size cap (%d bytes) reached",
                entry_id,
                MAX_DAILY_FILE_BYTES,
            )
            return
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
    """Return the last *max_lines* across recent daily files (newest days first)."""
    log_dir = _log_dir(hass)
    slug = _safe_entry_slug(entry_id)
    dated = _list_daily_log_files(log_dir, slug)[:RETENTION_DAYS]
    if not dated:
        return []
    collected: list[str] = []
    for _day, path in dated:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        collected = lines + collected
        if len(collected) >= max_lines:
            break
    return collected[-max_lines:]


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
            "%s [%s] lifecycle file log: %s (retention=%d days, enable in options)",
            LIFECYCLE_PREFIX,
            title,
            path,
            RETENTION_DAYS,
        )
    schedule_lifecycle_file_log(
        hass,
        entry_id,
        f"========== {event} ========== {detail} path={path}",
    )
