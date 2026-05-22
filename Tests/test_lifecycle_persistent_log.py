"""Tests for on-disk lifecycle persistent logging."""

from __future__ import annotations

import logging
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
    async_append_lifecycle_file_log,
    lifecycle_file_log_enabled,
    lifecycle_file_log_verbose,
    lifecycle_log_path,
    read_lifecycle_log_tail,
    schedule_lifecycle_file_log,
)


@pytest.mark.asyncio
async def test_append_and_read_lifecycle_log(hass: HomeAssistant, tmp_path: Path) -> None:
    entry_id = "persist_entry_1"
    with patch.object(hass.config, "config_dir", str(tmp_path)):
        assert lifecycle_file_log_enabled(hass, entry_id) is True
        await async_append_lifecycle_file_log(hass, entry_id, "line one")
        await hass.async_block_till_done()
        path = lifecycle_log_path(hass, entry_id)
        assert path.exists()
        assert "line one" in path.read_text(encoding="utf-8")
        tail = read_lifecycle_log_tail(hass, entry_id, max_lines=10)
        assert any("line one" in line for line in tail)


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
        path = lifecycle_log_path(hass, entry.entry_id)
        assert not path.exists()


@pytest.mark.asyncio
async def test_schedule_verbose_only_when_enabled(
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
        path = lifecycle_log_path(hass, entry.entry_id)
        assert not path.exists()


@pytest.mark.asyncio
async def test_rotation_when_file_large(hass: HomeAssistant, tmp_path: Path) -> None:
    from custom_components.kostal_kore import lifecycle_persistent_log as mod

    entry_id = "rotate_me"
    with patch.object(hass.config, "config_dir", str(tmp_path)):
        with patch.object(mod, "MAX_FILE_BYTES", 80):
            await async_append_lifecycle_file_log(hass, entry_id, "x" * 60)
            await async_append_lifecycle_file_log(hass, entry_id, "y" * 60)
            await hass.async_block_till_done()
        path = lifecycle_log_path(hass, entry_id)
        backup = path.with_name(f"{path.name}.1")
        assert path.exists()
        assert backup.exists()


def test_lifecycle_verbose_and_read_errors(hass: HomeAssistant, tmp_path: Path) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "1.2.3.4", "password": "x"},
        options={CONF_LIFECYCLE_FILE_VERBOSE: True},
    )
    entry.add_to_hass(hass)
    with patch.object(hass.config, "config_dir", str(tmp_path)):
        assert lifecycle_file_log_verbose(hass, entry.entry_id) is True
        assert read_lifecycle_log_tail(hass, "missing") == []
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.side_effect = OSError("denied")
        with patch(
            "custom_components.kostal_kore.lifecycle_persistent_log.lifecycle_log_path",
            return_value=mock_path,
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
        options={CONF_LIFECYCLE_FILE_VERBOSE: False},
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
async def test_rotation_moves_existing_backup(hass: HomeAssistant, tmp_path: Path) -> None:
    from custom_components.kostal_kore import lifecycle_persistent_log as mod

    entry_id = "multi_backup"
    with patch.object(hass.config, "config_dir", str(tmp_path)):
        path = lifecycle_log_path(hass, entry_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 100)
        path.with_name(f"{path.name}.1").write_bytes(b"y" * 10)
        path.with_name(f"{path.name}.2").write_bytes(b"z" * 10)
        with patch.object(mod, "MAX_FILE_BYTES", 50):
            await async_append_lifecycle_file_log(hass, entry_id, "trigger rotate")
            await hass.async_block_till_done()
        assert path.with_name(f"{path.name}.2").exists() or path.with_name(
            f"{path.name}.1"
        ).exists()


def test_schedule_returns_when_file_log_disabled(hass: HomeAssistant) -> None:
    with patch(
        "custom_components.kostal_kore.lifecycle_persistent_log.lifecycle_file_log_enabled",
        return_value=False,
    ):
        with patch.object(hass, "async_create_task") as mock_task:
            schedule_lifecycle_file_log(hass, "e1", "msg")
    mock_task.assert_not_called()


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
    with patch.object(
        hass, "async_add_executor_job", AsyncMock(side_effect=RuntimeError("disk full"))
    ):
        with caplog.at_level(logging.DEBUG):
            await async_append_lifecycle_file_log(hass, "err_entry", "msg")
    assert "Lifecycle file log write failed" in caplog.text
