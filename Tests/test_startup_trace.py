"""Tests for startup_trace structured logging helpers."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from custom_components.kostal_kore.startup_trace import (
    LOG_PREFIX,
    SetupTrace,
    entity_unique_id,
    log_entity_batch,
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
