"""Tests for on-disk lifecycle persistent logging."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kostal_kore.const import (
    CONF_LIFECYCLE_FILE_LOG,
    CONF_LIFECYCLE_FILE_VERBOSE,
    DOMAIN,
)
from custom_components.kostal_kore.lifecycle_persistent_log import (
    RETENTION_DAYS,
    async_append_lifecycle_file_log,
    lifecycle_file_log_enabled,
    lifecycle_file_log_verbose,
    lifecycle_log_path,
    read_lifecycle_log_tail,
    schedule_lifecycle_file_log,
)


def _entry_with_file_log(hass: HomeAssistant, entry_id: str = "persist_entry_1") -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id=entry_id,
        data={"host": "192.168.1.2", "password": "x"},
        options={CONF_LIFECYCLE_FILE_LOG: True},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.asyncio
async def test_append_and_read_daily_lifecycle_log(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    entry = _entry_with_file_log(hass)
    with patch.object(hass.config, "config_dir", str(tmp_path)):
        assert lifecycle_file_log_enabled(hass, entry.entry_id) is True
        await async_append_lifecycle_file_log(hass, entry.entry_id, "line one")
        await hass.async_block_till_done()
        path = lifecycle_log_path(hass, entry.entry_id)
        assert path.name.startswith("lifecycle_persist_entry_1.")
        assert path.name.endswith(".log")
        assert "line one" in path.read_text(encoding="utf-8")
        tail = read_lifecycle_log_tail(hass, entry.entry_id, max_lines=10)
        assert any("line one" in line for line in tail)


@pytest.mark.asyncio
async def test_lifecycle_log_disabled_by_default(hass: HomeAssistant, tmp_path: Path) -> None:
    entry_id = "no_file_log"
    with patch.object(hass.config, "config_dir", str(tmp_path)):
        assert lifecycle_file_log_enabled(hass, entry_id) is False
        await async_append_lifecycle_file_log(hass, entry_id, "skip me")
        await hass.async_block_till_done()
        log_dir = tmp_path / "custom_components/kostal_kore/logs"
        assert not list(log_dir.glob("lifecycle_*")) if log_dir.exists() else True


@pytest.mark.asyncio
async def test_lifecycle_log_disabled_via_options(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "192.168.1.2", "password": "x"},
        options={CONF_LIFECYCLE_FILE_LOG: False},
    )
    entry.add_to_hass(hass)
    with patch.object(hass.config, "config_dir", str(tmp_path)):
        await async_append_lifecycle_file_log(hass, entry.entry_id, "skip me")
        await hass.async_block_till_done()
        assert not lifecycle_log_path(hass, entry.entry_id).exists()


@pytest.mark.asyncio
async def test_schedule_verbose_only_when_file_log_and_verbose(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "192.168.1.2", "password": "x"},
        options={
            CONF_LIFECYCLE_FILE_LOG: True,
            CONF_LIFECYCLE_FILE_VERBOSE: False,
        },
    )
    entry.add_to_hass(hass)
    with patch.object(hass.config, "config_dir", str(tmp_path)):
        schedule_lifecycle_file_log(
            hass,
            entry.entry_id,
            "verbose line",
            verbose_only=True,
        )
        await hass.async_block_till_done()
        assert not lifecycle_log_path(hass, entry.entry_id).exists()


@pytest.mark.asyncio
async def test_purge_keeps_only_five_daily_files(hass: HomeAssistant, tmp_path: Path) -> None:
    from custom_components.kostal_kore import lifecycle_persistent_log as mod

    entry = _entry_with_file_log(hass, "retention_entry")
    slug = "retention_entry"
    log_dir = tmp_path / "custom_components/kostal_kore/logs"
    log_dir.mkdir(parents=True)
    today = datetime.now(tz=timezone.utc).date()
    for offset in range(7):
        day = (today - timedelta(days=offset)).isoformat()
        (log_dir / mod._daily_filename(slug, day)).write_text("x\n", encoding="utf-8")
    with patch.object(hass.config, "config_dir", str(tmp_path)):
        await async_append_lifecycle_file_log(hass, entry.entry_id, "trigger purge")
        await hass.async_block_till_done()
    remaining = [name for name, _ in mod._list_daily_log_files(log_dir, slug)]
    assert len(remaining) == RETENTION_DAYS


@pytest.mark.asyncio
async def test_daily_size_cap_skips_further_writes(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    from custom_components.kostal_kore import lifecycle_persistent_log as mod

    entry = _entry_with_file_log(hass, "cap_entry")
    with patch.object(hass.config, "config_dir", str(tmp_path)):
        path = lifecycle_log_path(hass, entry.entry_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * mod.MAX_DAILY_FILE_BYTES)
        await async_append_lifecycle_file_log(hass, entry.entry_id, "should not append")
        await hass.async_block_till_done()
        assert path.read_bytes() == b"x" * mod.MAX_DAILY_FILE_BYTES


def test_lifecycle_verbose_false_when_entry_missing(hass: HomeAssistant) -> None:
    with patch(
        "custom_components.kostal_kore.lifecycle_persistent_log.lifecycle_file_log_enabled",
        return_value=True,
    ):
        assert lifecycle_file_log_verbose(hass, "gone") is False


def test_lifecycle_verbose_requires_file_log(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "1.2.3.4", "password": "x"},
        options={CONF_LIFECYCLE_FILE_VERBOSE: True},
    )
    entry.add_to_hass(hass)
    assert lifecycle_file_log_verbose(hass, entry.entry_id) is False


def test_lifecycle_verbose_and_read_errors(hass: HomeAssistant, tmp_path: Path) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "1.2.3.4", "password": "x"},
        options={
            CONF_LIFECYCLE_FILE_LOG: True,
            CONF_LIFECYCLE_FILE_VERBOSE: True,
        },
    )
    entry.add_to_hass(hass)
    with patch.object(hass.config, "config_dir", str(tmp_path)):
        assert lifecycle_file_log_verbose(hass, entry.entry_id) is True
        assert read_lifecycle_log_tail(hass, "missing") == []
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.side_effect = OSError("denied")
        with patch(
            "custom_components.kostal_kore.lifecycle_persistent_log._list_daily_log_files",
            return_value=[("2026-05-22", mock_path)],
        ):
            assert read_lifecycle_log_tail(hass, entry.entry_id) == []


@pytest.mark.asyncio
async def test_schedule_with_stack_trace(hass: HomeAssistant, tmp_path: Path) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "1.2.3.4", "password": "x"},
        options={
            CONF_LIFECYCLE_FILE_LOG: True,
            CONF_LIFECYCLE_FILE_VERBOSE: True,
        },
    )
    entry.add_to_hass(hass)
    with patch.object(hass.config, "config_dir", str(tmp_path)):
        schedule_lifecycle_file_log(
            hass,
            entry.entry_id,
            "reload",
            include_stack=True,
        )
        await hass.async_block_till_done()
        text = lifecycle_log_path(hass, entry.entry_id).read_text(encoding="utf-8")
        assert "--- stack ---" in text


@pytest.mark.asyncio
async def test_async_append_verbose_only_skipped(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "1.2.3.4", "password": "x"},
        options={CONF_LIFECYCLE_FILE_LOG: True, CONF_LIFECYCLE_FILE_VERBOSE: False},
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.kostal_kore.lifecycle_persistent_log._append_sync"
    ) as mock_append:
        await async_append_lifecycle_file_log(
            hass, entry.entry_id, "hidden", verbose_only=True
        )
    mock_append.assert_not_called()


@pytest.mark.asyncio
async def test_read_tail_merges_multiple_days(hass: HomeAssistant, tmp_path: Path) -> None:
    from custom_components.kostal_kore import lifecycle_persistent_log as mod

    entry = _entry_with_file_log(hass, "multi_day")
    slug = "multi_day"
    log_dir = tmp_path / "custom_components/kostal_kore/logs"
    log_dir.mkdir(parents=True)
    today = datetime.now(tz=timezone.utc).date()
    for offset, marker in enumerate((2, 1, 0)):
        day = (today - timedelta(days=offset)).isoformat()
        (log_dir / mod._daily_filename(slug, day)).write_text(
            f"day-{marker}\n", encoding="utf-8"
        )
    with patch.object(hass.config, "config_dir", str(tmp_path)):
        tail = read_lifecycle_log_tail(hass, entry.entry_id, max_lines=10)
    assert "day-0" in tail[0]
    assert "day-2" in tail[-1]


def test_schedule_returns_when_file_log_disabled(hass: HomeAssistant) -> None:
    with patch(
        "custom_components.kostal_kore.lifecycle_persistent_log.lifecycle_file_log_enabled",
        return_value=False,
    ):
        with patch.object(hass, "async_create_task") as mock_task:
            schedule_lifecycle_file_log(hass, "e1", "msg")
    mock_task.assert_not_called()


def test_lock_for_reuses_existing_lock() -> None:
    from custom_components.kostal_kore.lifecycle_persistent_log import _lock_for

    first = _lock_for("reuse_entry")
    second = _lock_for("reuse_entry")
    assert first is second


def test_list_daily_logs_empty_dir(hass: HomeAssistant, tmp_path: Path) -> None:
    from custom_components.kostal_kore.lifecycle_persistent_log import _list_daily_log_files

    missing = tmp_path / "no_logs"
    assert _list_daily_log_files(missing, "slug") == []
    not_a_dir = tmp_path / "file.txt"
    not_a_dir.write_text("x", encoding="utf-8")
    assert _list_daily_log_files(not_a_dir, "slug") == []


def test_list_daily_logs_ignores_other_slugs(hass: HomeAssistant, tmp_path: Path) -> None:
    from custom_components.kostal_kore import lifecycle_persistent_log as mod

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    day = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    (log_dir / mod._daily_filename("other", day)).write_text("x\n", encoding="utf-8")
    assert mod._list_daily_log_files(log_dir, "mine") == []


@pytest.mark.asyncio
async def test_read_tail_respects_max_lines(hass: HomeAssistant, tmp_path: Path) -> None:
    from custom_components.kostal_kore import lifecycle_persistent_log as mod

    entry = _entry_with_file_log(hass, "many_lines")
    slug = "many_lines"
    log_dir = tmp_path / "custom_components/kostal_kore/logs"
    log_dir.mkdir(parents=True)
    day = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    (log_dir / mod._daily_filename(slug, day)).write_text(
        "\n".join(f"line-{i}" for i in range(50)),
        encoding="utf-8",
    )
    with patch.object(hass.config, "config_dir", str(tmp_path)):
        tail = read_lifecycle_log_tail(hass, entry.entry_id, max_lines=5)
    assert len(tail) == 5


def test_log_session_banner_enabled(hass: HomeAssistant, caplog) -> None:
    from custom_components.kostal_kore.lifecycle_persistent_log import log_session_banner

    entry = _entry_with_file_log(hass, "banner_entry")
    with patch.object(hass.config, "config_dir", "/tmp/ha"):
        with caplog.at_level(logging.INFO):
            log_session_banner(
                hass,
                entry_id=entry.entry_id,
                title="WR",
                event="SETUP",
                detail="test",
            )
    assert "lifecycle file log" in caplog.text


def test_log_session_banner_without_path_info(hass: HomeAssistant, caplog) -> None:
    from custom_components.kostal_kore.lifecycle_persistent_log import log_session_banner

    entry = _entry_with_file_log(hass, "banner_quiet")
    with patch.object(hass.config, "config_dir", "/tmp/ha"):
        with caplog.at_level(logging.INFO):
            log_session_banner(
                hass,
                entry_id=entry.entry_id,
                title="WR",
                event="UNLOAD",
                detail="test",
                log_path_info=False,
            )
    assert "lifecycle file log" not in caplog.text


def test_log_session_banner_disabled(hass: HomeAssistant, caplog) -> None:
    from custom_components.kostal_kore.lifecycle_persistent_log import log_session_banner

    with patch(
        "custom_components.kostal_kore.lifecycle_persistent_log.lifecycle_file_log_enabled",
        return_value=False,
    ):
        log_session_banner(
            hass,
            entry_id="x",
            title="WR",
            event="SETUP",
            detail="test",
        )
    assert "lifecycle file log" not in caplog.text


@pytest.mark.asyncio
async def test_append_logs_debug_on_executor_error(hass: HomeAssistant, caplog) -> None:
    entry = _entry_with_file_log(hass, "err_entry")
    with patch.object(
        hass, "async_add_executor_job", AsyncMock(side_effect=RuntimeError("disk full"))
    ):
        with caplog.at_level(logging.DEBUG):
            await async_append_lifecycle_file_log(hass, entry.entry_id, "msg")
    assert "Lifecycle file log write failed" in caplog.text


@pytest.mark.asyncio
async def test_legacy_log_files_removed_on_append(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    entry = _entry_with_file_log(hass, "legacy")
    log_dir = tmp_path / "custom_components/kostal_kore/logs"
    log_dir.mkdir(parents=True)
    legacy = log_dir / "lifecycle_legacy.log"
    legacy.write_text("old\n", encoding="utf-8")
    (log_dir / "lifecycle_legacy.log.1").write_text("old\n", encoding="utf-8")
    with patch.object(hass.config, "config_dir", str(tmp_path)):
        await async_append_lifecycle_file_log(hass, entry.entry_id, "new")
        await hass.async_block_till_done()
    assert not legacy.exists()
    assert not (log_dir / "lifecycle_legacy.log.1").exists()
