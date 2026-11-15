"""End-to-end history copy against a real (in-memory SQLite) recorder.

These tests guard against the recorder's metadata caches going stale after
``copy_legacy_history`` rewrites ``states_meta`` / ``statistics_meta``: the
target's old metadata rows are deleted, so a cached id would make the next
state or statistic for the target fail with ``FOREIGN KEY constraint failed``.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import pytest
from homeassistant.components.recorder import Recorder
from homeassistant.components.recorder.db_schema import States, StatesMeta, StatisticsMeta
from homeassistant.components.recorder.models import StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    statistics_during_period,
)
from homeassistant.components.recorder.util import session_scope
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)
from sqlalchemy import select

from custom_components.kostal_kore.migration_services import _copy_legacy_history

_OLD = "sensor.scb_home_consumption_from_pv"
_NEW = "sensor.kore_home_consumption_from_pv"


@pytest.fixture
def mock_recorder_before_hass(async_setup_recorder_instance: Any) -> None:
    """Set up the recorder before ``hass`` (the conftest autouse fixtures need hass)."""


def _energy_metadata(statistic_id: str) -> StatisticMetaData:
    """Build sum statistics metadata valid for the installed HA version."""
    keys = StatisticMetaData.__required_keys__ | StatisticMetaData.__optional_keys__
    metadata: dict[str, Any] = {
        "has_sum": True,
        "name": None,
        "source": "recorder",
        "statistic_id": statistic_id,
        "unit_of_measurement": "kWh",
    }
    if "mean_type" in keys:
        from homeassistant.components.recorder.models import (  # noqa: PLC0415
            StatisticMeanType,
        )

        metadata["mean_type"] = StatisticMeanType.NONE
    else:
        metadata["has_mean"] = False
    if "unit_class" in keys:
        metadata["unit_class"] = "energy"
    return metadata  # type: ignore[return-value]


def _import_hour(hass: HomeAssistant, statistic_id: str, start: Any, total: float) -> None:
    async_import_statistics(
        hass,
        _energy_metadata(statistic_id),
        [{"start": start, "state": total, "sum": total}],
    )


def _db_snapshot(instance: Recorder) -> dict[str, Any]:
    with session_scope(session=instance.get_session(), read_only=True) as session:
        states_meta = {
            row.entity_id: row.metadata_id
            for row in session.execute(select(StatesMeta)).scalars()
        }
        stats_meta = {
            row.statistic_id: row.id
            for row in session.execute(select(StatisticsMeta)).scalars()
        }
        target_states = [
            row.state
            for row in session.execute(
                select(States)
                .join(StatesMeta, States.metadata_id == StatesMeta.metadata_id)
                .where(StatesMeta.entity_id == _NEW)
                .order_by(States.last_updated_ts)
            ).scalars()
        ]
    return {
        "states_meta": states_meta,
        "stats_meta": stats_meta,
        "target_states": target_states,
    }


async def test_copy_history_then_new_target_state_and_statistic_is_recorded(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """After a merge, the live target keeps recording states and statistics."""
    hour = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)

    hass.states.async_set(_OLD, "1")
    hass.states.async_set(_NEW, "10")
    _import_hour(hass, _OLD, hour - timedelta(hours=3), 1.0)
    _import_hour(hass, _NEW, hour - timedelta(hours=2), 10.0)
    await async_wait_recording_done(hass)

    before = await recorder_mock.async_add_executor_job(_db_snapshot, recorder_mock)
    assert before["target_states"] == ["10"]
    old_states_meta_id = before["states_meta"][_OLD]
    old_stats_meta_id = before["stats_meta"][_OLD]

    summary = await _copy_legacy_history(hass, mappings=[(_OLD, _NEW)])
    assert summary.applied_mappings == 1
    assert summary.states_rows_moved == 1
    assert summary.statistics_rows_moved == 1

    caplog.clear()
    with caplog.at_level(logging.ERROR):
        hass.states.async_set(_NEW, "11")
        _import_hour(hass, _NEW, hour - timedelta(hours=1), 11.0)
        await async_wait_recording_done(hass)

    errors = [rec for rec in caplog.records if rec.levelno >= logging.ERROR]
    assert errors == []

    after = await recorder_mock.async_add_executor_job(_db_snapshot, recorder_mock)
    assert _OLD not in after["states_meta"]
    assert _OLD not in after["stats_meta"]
    # The merged row keeps the legacy metadata_id and now carries the target id.
    assert after["states_meta"][_NEW] == old_states_meta_id
    assert after["stats_meta"][_NEW] == old_stats_meta_id
    assert after["target_states"] == ["1", "10", "11"]

    stats = await recorder_mock.async_add_executor_job(
        statistics_during_period,
        hass,
        hour - timedelta(hours=4),
        None,
        {_NEW},
        "hour",
        None,
        {"sum"},
    )
    assert [row["sum"] for row in stats[_NEW]] == [1.0, 10.0, 11.0]


async def test_copy_history_new_entity_cannot_inherit_freed_metadata_id(
    recorder_mock: Recorder,
    hass: HomeAssistant,
) -> None:
    """A brand-new entity that reuses a freed metadata_id gets its own states."""
    hass.states.async_set(_OLD, "1")
    hass.states.async_set(_NEW, "10")
    await async_wait_recording_done(hass)

    await _copy_legacy_history(hass, mappings=[(_OLD, _NEW)])

    hass.states.async_set("sensor.brand_new", "42")
    hass.states.async_set(_NEW, "11")
    await async_wait_recording_done(hass)

    after = await recorder_mock.async_add_executor_job(_db_snapshot, recorder_mock)
    assert after["target_states"] == ["1", "10", "11"]
    assert after["states_meta"]["sensor.brand_new"] != after["states_meta"][_NEW]
