"""Tests for startup_trace structured logging helpers."""

from __future__ import annotations

import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.kostal_kore.const import DOMAIN
from custom_components.kostal_kore.startup_trace import (
    LIFECYCLE_PREFIX,
    LOG_PREFIX,
    SetupTrace,
    async_request_config_reload,
    entity_unique_id,
    log_entity_batch,
    log_reload_skipped_lifecycle,
    log_setup_entry_lifecycle,
    log_unload_entry_lifecycle,
)


def test_entity_unique_id_from_attr() -> None:
    entity = MagicMock()
    entity.unique_id = "entry_sensor_foo"
    assert entity_unique_id(entity) == "entry_sensor_foo"


def test_entity_unique_id_fallback_name() -> None:
    entity = MagicMock(spec=[])
    entity.name = "Battery SoH"
    assert entity_unique_id(entity) == "<no-uid:Battery SoH>"


def test_log_entity_batch_empty(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        log_entity_batch(
            entry_title="WR",
            platform="sensor",
            batch="process_data",
            entities=[],
        )
    assert LOG_PREFIX in caplog.text
    assert "0 entities (skipped)" in caplog.text


def test_log_entity_batch_with_sample(caplog: pytest.LogCaptureFixture) -> None:
    entities = [MagicMock(unique_id=f"id_{i}") for i in range(12)]
    with caplog.at_level(logging.INFO):
        log_entity_batch(
            entry_title="WR",
            platform="sensor",
            batch="process_data",
            entities=entities,
            sample_limit=3,
        )
    assert "registering 12 entities (+9 more)" in caplog.text
    assert "id_0" in caplog.text


def test_log_entity_batch_debug_full_list(caplog: pytest.LogCaptureFixture) -> None:
    entities = [MagicMock(unique_id=f"uid_{i}") for i in range(3)]
    with caplog.at_level(logging.DEBUG):
        log_entity_batch(
            entry_title="WR",
            platform="number",
            batch="rest",
            entities=entities,
        )
    assert "full unique_ids" in caplog.text


def test_setup_trace_debug(caplog: pytest.LogCaptureFixture) -> None:
    trace = SetupTrace("abc", "WR")
    with caplog.at_level(logging.DEBUG):
        trace.debug("detail %s", 1)
    assert "detail 1" in caplog.text


def test_setup_trace_warning(caplog: pytest.LogCaptureFixture) -> None:
    trace = SetupTrace("abc", "WR")
    with caplog.at_level(logging.WARNING):
        trace.warning("slow platform")
    assert "slow platform" in caplog.text


def test_log_reload_skipped_is_info(caplog: pytest.LogCaptureFixture) -> None:
    trace = SetupTrace("abc", "WR")
    with caplog.at_level(logging.INFO):
        trace.log_reload_skipped("setup still in progress")
    assert "reload skipped" in caplog.text


def test_lifecycle_setup_and_rapid_cycle_warning(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    entry_id = "test_entry"
    hass.data.setdefault(DOMAIN, {})["_entry_lifecycle"] = {
        entry_id: {
            "setup_count": 0,
            "unload_count": 1,
            "last_unload_mono": time.monotonic(),
            "last_reload_source": "external:test",
        }
    }
    with caplog.at_level(logging.INFO):
        log_setup_entry_lifecycle(
            hass,
            entry_id=entry_id,
            title="WR",
            entry_state="loaded",
        )
    assert LIFECYCLE_PREFIX in caplog.text
    assert "setup BEGIN #1" in caplog.text
    assert "RAPID RELOAD CYCLE" in caplog.text


def test_lifecycle_expected_ha_reload_is_info_not_rapid_warning(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    entry_id = "test_entry_ha_reload"
    hass.data.setdefault(DOMAIN, {})["_entry_lifecycle"] = {
        entry_id: {
            "setup_count": 0,
            "unload_count": 1,
            "last_unload_mono": time.monotonic(),
            "last_reload_source": "ha_core:entity_registry_disabled_by",
        }
    }
    with caplog.at_level(logging.INFO):
        log_setup_entry_lifecycle(
            hass,
            entry_id=entry_id,
            title="WR",
            entry_state="loaded",
        )
    assert "RAPID RELOAD CYCLE" not in caplog.text
    assert "expected HA entity-registry reload" in caplog.text


@pytest.mark.asyncio
async def test_lifecycle_unload_and_reload_request(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    entry_id = "lifecycle_reload"
    with caplog.at_level(logging.INFO):
        log_unload_entry_lifecycle(
            hass,
            entry_id=entry_id,
            title="WR",
            entry_state="loaded",
        )
        log_reload_skipped_lifecycle(
            hass,
            entry_id=entry_id,
            title="WR",
            reason="options unchanged",
            entry_state="loaded",
        )
    assert "unload BEGIN" in caplog.text
    assert "reload SKIPPED" in caplog.text

    with patch.object(
        hass.config_entries, "async_reload", AsyncMock(return_value=True)
    ) as mock_reload:
        await async_request_config_reload(
            hass,
            entry_id,
            source="test:manual",
            title="WR",
        )
    mock_reload.assert_awaited_once_with(entry_id)
    assert "reload REQUEST source='test:manual'" in caplog.text


def test_lifecycle_stats_recovers_from_invalid_store(hass: HomeAssistant) -> None:
    hass.data[DOMAIN] = {"_entry_lifecycle": "not-a-dict"}
    log_unload_entry_lifecycle(
        hass,
        entry_id="bad_store",
        title="WR",
        entry_state="loaded",
    )
    stats = hass.data[DOMAIN]["_entry_lifecycle"]
    assert isinstance(stats, dict)
    assert stats["bad_store"]["unload_count"] == 1


def test_setup_trace_phases(caplog: pytest.LogCaptureFixture) -> None:
    trace = SetupTrace("abc", "WR")
    with caplog.at_level(logging.INFO):
        trace.phase_begin("login")
        trace.phase_end("login", success=True)
        trace.log_reload_trigger(
            reason="options changed",
            entry_state=MagicMock(),
            setup_in_progress=False,
            unload_in_progress=False,
        )
    assert "phase BEGIN login" in caplog.text
    assert "phase END login" in caplog.text
    assert "reload trigger" in caplog.text
