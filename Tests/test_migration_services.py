"""Tests for guarded migration service helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.kostal_kore.const import (
    DOMAIN,
    SERVICE_ADOPT_LEGACY_ENTITY_IDS,
    SERVICE_COPY_LEGACY_HISTORY,
)
from custom_components.kostal_kore.legacy_migration import LEGACY_DOMAIN, LegacyAdoptResult
from custom_components.kostal_kore import migration_services as migration_services_mod
from custom_components.kostal_kore.migration_services import (
    _HistoryCopySummary,
    _build_auto_mapping,
    _copy_legacy_history,
    _copy_legacy_history_sync,
    _detect_recorder_backend,
    _ensure_guard_confirmed,
    _get_or_init_guard,
    _get_recorder_instance,
    _handle_adopt_service,
    _handle_copy_history_service,
    _notify,
    _merge_states_metadata,
    _merge_statistics_table,
    _merge_statistics_metadata,
    _normalise_mapping_rows,
    _resolve_target_entry_id,
    async_register_migration_services,
    async_unregister_migration_services_if_unused,
)

from pytest_homeassistant_custom_component.common import MockConfigEntry


async def test_register_unregister_migration_services(hass: HomeAssistant) -> None:
    """Services are registered once and removable when no entries exist."""
    async_register_migration_services(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_ADOPT_LEGACY_ENTITY_IDS)
    assert hass.services.has_service(DOMAIN, SERVICE_COPY_LEGACY_HISTORY)

    async_unregister_migration_services_if_unused(hass)
    assert not hass.services.has_service(DOMAIN, SERVICE_ADOPT_LEGACY_ENTITY_IDS)
    assert not hass.services.has_service(DOMAIN, SERVICE_COPY_LEGACY_HISTORY)


async def test_unregister_migration_services_ignores_unloading_entry_id(
    hass: HomeAssistant,
) -> None:
    """Services should be removable even if unloading entry still exists in domain store."""
    async_register_migration_services(hass)
    hass.data.setdefault(DOMAIN, {})["entry-a"] = {"mock": True}

    async_unregister_migration_services_if_unused(hass, unloading_entry_id="entry-a")
    assert not hass.services.has_service(DOMAIN, SERVICE_ADOPT_LEGACY_ENTITY_IDS)
    assert not hass.services.has_service(DOMAIN, SERVICE_COPY_LEGACY_HISTORY)


async def test_unregister_migration_services_keeps_services_with_other_entries(
    hass: HomeAssistant,
) -> None:
    """Services must stay registered while other KORE runtime entries are active."""
    async_register_migration_services(hass)
    hass.data.setdefault(DOMAIN, {})["entry-a"] = {"mock": True}
    hass.data.setdefault(DOMAIN, {})["entry-b"] = {"mock": True}

    async_unregister_migration_services_if_unused(hass, unloading_entry_id="entry-a")
    assert hass.services.has_service(DOMAIN, SERVICE_ADOPT_LEGACY_ENTITY_IDS)
    assert hass.services.has_service(DOMAIN, SERVICE_COPY_LEGACY_HISTORY)


async def test_adopt_service_dry_run_calls_adopt(hass: HomeAssistant) -> None:
    """Dry-run adopt should execute immediately without confirmation challenge."""
    target_entry = MockConfigEntry(domain=DOMAIN, data={"host": "10.0.0.11"})
    source_entry = MockConfigEntry(domain=LEGACY_DOMAIN, data={"host": "10.0.0.11"})
    target_entry.add_to_hass(hass)
    source_entry.add_to_hass(hass)
    async_register_migration_services(hass)

    with (
        patch(
            "custom_components.kostal_kore.migration_services.adopt_legacy_entity_ids",
            AsyncMock(
                return_value=LegacyAdoptResult(
                    source_entry_id=source_entry.entry_id,
                    target_entry_id=target_entry.entry_id,
                    dry_run=True,
                    migrated_entities=3,
                    migrated_devices=1,
                    removed_target_duplicates=2,
                )
            ),
        ) as mock_adopt,
        patch(
            "custom_components.kostal_kore.migration_services._notify",
            AsyncMock(),
        ) as mock_notify,
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_ADOPT_LEGACY_ENTITY_IDS,
            {
                "target_entry_id": target_entry.entry_id,
                "source_entry_id": source_entry.entry_id,
                "dry_run": True,
            },
            blocking=True,
        )

    mock_adopt.assert_awaited_once()
    mock_notify.assert_awaited()


async def test_copy_service_apply_requires_guard_challenge(hass: HomeAssistant) -> None:
    """First non-dry-run copy call should only arm challenge and stop."""
    target_entry = MockConfigEntry(domain=DOMAIN, data={"host": "10.0.0.11"})
    source_entry = MockConfigEntry(domain=LEGACY_DOMAIN, data={"host": "10.0.0.11"})
    target_entry.add_to_hass(hass)
    source_entry.add_to_hass(hass)
    async_register_migration_services(hass)

    with (
        patch(
            "custom_components.kostal_kore.migration_services._copy_legacy_history",
            AsyncMock(),
        ) as mock_copy,
        patch(
            "custom_components.kostal_kore.migration_services._notify",
            AsyncMock(),
        ) as mock_notify,
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_COPY_LEGACY_HISTORY,
            {
                "target_entry_id": target_entry.entry_id,
                "source_entry_id": source_entry.entry_id,
                "dry_run": False,
                "include_auto_map": False,
                "entity_map": [
                    {
                        "old_entity_id": "sensor.kostal_old_grid_power",
                        "new_entity_id": "sensor.kostal_new_grid_power",
                    }
                ],
            },
            blocking=True,
        )

    mock_copy.assert_not_awaited()
    mock_notify.assert_awaited()


def test_mapping_and_target_resolution_helpers(hass: HomeAssistant) -> None:
    """Mapping normalization and target resolution handle edge cases."""
    only_entry = MockConfigEntry(domain=DOMAIN, data={"host": "10.0.0.10"})
    only_entry.add_to_hass(hass)

    assert _resolve_target_entry_id(hass, SimpleNamespace(data={})) == only_entry.entry_id
    assert _resolve_target_entry_id(
        hass,
        SimpleNamespace(data={"target_entry_id": f"  {only_entry.entry_id}  "}),
    ) == only_entry.entry_id

    second_entry = MockConfigEntry(domain=DOMAIN, data={"host": "10.0.0.11"})
    second_entry.add_to_hass(hass)
    with pytest.raises(vol.Invalid, match="target_entry_id is required"):
        _resolve_target_entry_id(hass, SimpleNamespace(data={}))

    rows = [
        {"old_entity_id": "sensor.a", "new_entity_id": "sensor.b"},
        {"old_entity_id": "sensor.a", "new_entity_id": "sensor.b"},
        {"old_entity_id": "", "new_entity_id": "sensor.c"},
        {"old_entity_id": "sensor.same", "new_entity_id": "sensor.same"},
    ]
    assert _normalise_mapping_rows(rows) == [("sensor.a", "sensor.b")]

    with pytest.raises(vol.Invalid, match="Conflicting mapping"):
        _normalise_mapping_rows(
            [
                {"old_entity_id": "sensor.a", "new_entity_id": "sensor.x"},
                {"old_entity_id": "sensor.b", "new_entity_id": "sensor.x"},
            ]
        )

    pairs = [
        SimpleNamespace(old_entity_id="sensor.old", new_entity_id="sensor.new"),
    ]
    assert _build_auto_mapping(pairs) == [("sensor.old", "sensor.new")]


async def test_guard_confirmation_state_machine_and_payload_binding(
    hass: HomeAssistant,
) -> None:
    """The destructive-action guard covers issue, expiry, mismatch and success paths."""
    entry_id = "entry-guard"
    action = "copy_legacy_history"
    hass.data.setdefault(DOMAIN, {})[entry_id] = {}

    with (
        patch.object(migration_services_mod, "_notify", AsyncMock()) as notify_mock,
        patch.object(
            migration_services_mod,
            "generate_confirmation_code",
            return_value="ABC123",
        ),
    ):
        assert await _ensure_guard_confirmed(
            hass,
            entry_id=entry_id,
            action=action,
            dry_run=True,
            confirmation_code=None,
            final_confirm=False,
            payload_fingerprint="dry",
        )
        assert _get_or_init_guard(hass, entry_id, action)["phase"] == 0

        with patch.object(migration_services_mod.time, "monotonic", return_value=10.0):
            assert not await _ensure_guard_confirmed(
                hass,
                entry_id=entry_id,
                action=action,
                dry_run=False,
                confirmation_code=None,
                final_confirm=False,
                payload_fingerprint="fp-1",
            )
            guard = _get_or_init_guard(hass, entry_id, action)
            assert guard["phase"] == 1
            assert guard["code"] == "ABC123"

            assert not await _ensure_guard_confirmed(
                hass,
                entry_id=entry_id,
                action=action,
                dry_run=False,
                confirmation_code="WRONG",
                final_confirm=False,
                payload_fingerprint="fp-1",
            )

            assert not await _ensure_guard_confirmed(
                hass,
                entry_id=entry_id,
                action=action,
                dry_run=False,
                confirmation_code="ABC123",
                final_confirm=False,
                payload_fingerprint="fp-1",
            )
            assert _get_or_init_guard(hass, entry_id, action)["phase"] == 2

            assert not await _ensure_guard_confirmed(
                hass,
                entry_id=entry_id,
                action=action,
                dry_run=False,
                confirmation_code="ABC123",
                final_confirm=False,
                payload_fingerprint="fp-1",
            )
            assert await _ensure_guard_confirmed(
                hass,
                entry_id=entry_id,
                action=action,
                dry_run=False,
                confirmation_code="ABC123",
                final_confirm=True,
                payload_fingerprint="fp-1",
            )
            assert _get_or_init_guard(hass, entry_id, action)["phase"] == 0

        with patch.object(migration_services_mod.time, "monotonic", return_value=20.0):
            assert not await _ensure_guard_confirmed(
                hass,
                entry_id=entry_id,
                action=action,
                dry_run=False,
                confirmation_code=None,
                final_confirm=False,
                payload_fingerprint="fp-2",
            )
            assert not await _ensure_guard_confirmed(
                hass,
                entry_id=entry_id,
                action=action,
                dry_run=False,
                confirmation_code="ABC123",
                final_confirm=False,
                payload_fingerprint="fp-changed",
            )
            assert _get_or_init_guard(hass, entry_id, action)["phase"] == 0

        guard = _get_or_init_guard(hass, entry_id, action)
        guard.update({"phase": 1, "code": "ABC123", "expires_at": 1.0})
        with patch.object(migration_services_mod.time, "monotonic", return_value=99.0):
            assert not await _ensure_guard_confirmed(
                hass,
                entry_id=entry_id,
                action=action,
                dry_run=False,
                confirmation_code="ABC123",
                final_confirm=False,
                payload_fingerprint="fp-3",
            )
            assert _get_or_init_guard(hass, entry_id, action)["phase"] == 0

    assert notify_mock.await_count >= 1


def test_recorder_backend_and_instance_helpers(hass: HomeAssistant) -> None:
    """Recorder helper functions distinguish backend types and instance states."""
    assert _detect_recorder_backend(SimpleNamespace(db_url="sqlite:///db.sqlite")) == "sqlite"
    assert _detect_recorder_backend(SimpleNamespace(db_url="mysql://db")) == "mariadb"
    assert _detect_recorder_backend(SimpleNamespace(db_url="postgresql://db")) == "postgresql"
    assert _detect_recorder_backend(SimpleNamespace(db_url="custom://db")) == "unknown"

    hass.data.pop("recorder_instance", None)
    with pytest.raises(HomeAssistantError, match="Recorder integration is not loaded"):
        _get_recorder_instance(hass)

    hass.data["recorder_instance"] = object()
    with pytest.raises(HomeAssistantError, match="Recorder instance is not available yet"):
        _get_recorder_instance(hass)

    class DummyRecorder:
        pass

    recorder = DummyRecorder()
    hass.data["recorder_instance"] = recorder
    with patch.object(migration_services_mod, "Recorder", DummyRecorder):
        assert _get_recorder_instance(hass) is recorder


def test_history_merge_helpers_and_sync_copy_paths() -> None:
    """History merge helpers cover no-op, merge and rollback scenarios."""

    class ScalarOne:
        def __init__(self, value):
            self._value = value

        def scalar_one_or_none(self):
            return self._value

    class Scalars:
        def __init__(self, values):
            self._values = values

        def scalars(self):
            return self._values

    session = MagicMock()
    session.execute.return_value = ScalarOne(None)
    assert _merge_states_metadata(session, old_entity_id="sensor.old", new_entity_id="sensor.new") == (0, False)

    old_meta = SimpleNamespace(metadata_id=7, entity_id="sensor.old")
    new_meta = SimpleNamespace(metadata_id=9, entity_id="sensor.new")
    query_new = MagicMock()
    query_new.filter.return_value.update.return_value = 4
    query_old = MagicMock()
    query_old.filter.return_value.update.return_value = 0
    session = MagicMock()
    session.execute.side_effect = [ScalarOne(old_meta), ScalarOne(new_meta)]
    session.query.side_effect = [query_new, query_old]
    assert _merge_states_metadata(session, old_entity_id="sensor.old", new_entity_id="sensor.new") == (4, True)
    assert old_meta.entity_id == "sensor.new"
    session.delete.assert_called_once_with(new_meta)

    session = MagicMock()
    session.execute.return_value = Scalars([])
    assert _merge_statistics_metadata(session, old_entity_id="sensor.old", new_entity_id="sensor.new") == (0, 0, False)

    old_stat = SimpleNamespace(id=1, source="rec", statistic_id="sensor.old")
    new_stat = SimpleNamespace(id=2, source="rec", statistic_id="sensor.new")
    session = MagicMock()
    session.execute.side_effect = [Scalars([old_stat]), Scalars([new_stat])]
    with patch.object(migration_services_mod, "_merge_statistics_table", side_effect=[2, 3]):
        assert _merge_statistics_metadata(
            session,
            old_entity_id="sensor.old",
            new_entity_id="sensor.new",
        ) == (2, 3, True)
    assert old_stat.statistic_id == "sensor.new"
    session.delete.assert_called_once_with(new_stat)

    recorder = MagicMock()
    session = MagicMock()
    recorder.get_session.return_value = session
    with patch.object(
        migration_services_mod,
        "_merge_states_metadata",
        side_effect=[(0, False), (1, True)],
    ), patch.object(
        migration_services_mod,
        "_merge_statistics_metadata",
        side_effect=[(0, 0, False), (2, 3, True)],
    ):
        summary = _copy_legacy_history_sync(
            recorder,
            [("sensor.a", "sensor.b"), ("sensor.c", "sensor.d")],
        )
    assert summary.applied_mappings == 1
    assert summary.statistics_rows_moved == 2
    assert summary.short_term_rows_moved == 3
    session.commit.assert_called_once()
    session.close.assert_called_once()

    recorder = MagicMock()
    session = MagicMock()
    recorder.get_session.return_value = session
    with patch.object(
        migration_services_mod,
        "_merge_states_metadata",
        side_effect=RuntimeError("boom"),
    ):
        with pytest.raises(RuntimeError, match="boom"):
            _copy_legacy_history_sync(recorder, [("sensor.a", "sensor.b")])
    session.rollback.assert_called_once()
    session.close.assert_called_once()


async def test_copy_history_async_and_service_handlers_cover_remaining_paths(
    hass: HomeAssistant,
) -> None:
    """Copy/adopt service helpers cover preview, apply and fallback branches."""
    target_entry = MockConfigEntry(domain=DOMAIN, data={"host": "10.0.0.11"})
    source_entry = MockConfigEntry(domain=LEGACY_DOMAIN, data={"host": "10.0.0.11"})
    target_entry.add_to_hass(hass)
    source_entry.add_to_hass(hass)

    summary = _HistoryCopySummary(
        backend="sqlite",
        total_mappings=1,
        applied_mappings=1,
        states_rows_moved=2,
        statistics_rows_moved=3,
        short_term_rows_moved=4,
        meta_pairs_rebound=1,
    )

    class DummyRecorder:
        def __init__(self, db_url: str, recording: bool = True) -> None:
            self.db_url = db_url
            self.recording = recording
            self.async_add_executor_job = AsyncMock(return_value=summary)

    with patch.object(migration_services_mod, "Recorder", DummyRecorder):
        hass.data["recorder_instance"] = DummyRecorder("sqlite:///db.sqlite")
        assert await _copy_legacy_history(hass, mappings=[("sensor.a", "sensor.b")]) == summary
        hass.data["recorder_instance"].async_add_executor_job.assert_awaited_once()

        hass.data["recorder_instance"] = DummyRecorder("custom://db.sqlite")
        with pytest.raises(HomeAssistantError, match="Unsupported recorder backend"):
            await _copy_legacy_history(hass, mappings=[("sensor.a", "sensor.b")])

        hass.data["recorder_instance"] = DummyRecorder("sqlite:///db.sqlite", recording=False)
        with pytest.raises(HomeAssistantError, match="Recorder is not active"):
            await _copy_legacy_history(hass, mappings=[("sensor.a", "sensor.b")])

    with (
        patch.object(migration_services_mod, "_ensure_guard_confirmed", AsyncMock(return_value=True)),
        patch.object(
            migration_services_mod,
            "adopt_legacy_entity_ids",
            AsyncMock(
                return_value=LegacyAdoptResult(
                    source_entry_id=source_entry.entry_id,
                    target_entry_id=target_entry.entry_id,
                    dry_run=False,
                    migrated_entities=2,
                    migrated_devices=1,
                    removed_target_duplicates=1,
                )
            ),
        ) as adopt_mock,
        patch.object(migration_services_mod, "_notify", AsyncMock()) as notify_mock,
    ):
        await _handle_adopt_service(
            hass,
            SimpleNamespace(
                data={
                    "target_entry_id": target_entry.entry_id,
                    "source_entry_id": source_entry.entry_id,
                    "dry_run": False,
                    "confirmation_code": "ABC123",
                    "final_confirm": True,
                }
            ),
        )
    adopt_mock.assert_awaited_once()
    notify_mock.assert_awaited()

    with patch.object(migration_services_mod, "_notify", AsyncMock()) as notify_mock:
        await _handle_copy_history_service(
            hass,
            SimpleNamespace(
                data={
                    "target_entry_id": target_entry.entry_id,
                    "source_entry_id": source_entry.entry_id,
                    "dry_run": True,
                    "confirmation_code": None,
                    "final_confirm": False,
                    "include_auto_map": False,
                    "entity_map": [],
                }
            ),
        )
    notify_mock.assert_awaited_once()

    with (
        patch.object(migration_services_mod, "_ensure_guard_confirmed", AsyncMock(return_value=True)),
        patch.object(
            migration_services_mod,
            "discover_legacy_duplicate_entity_pairs",
            side_effect=HomeAssistantError("auto-map-failed"),
        ),
        patch.object(migration_services_mod, "Recorder", DummyRecorder),
        patch.object(migration_services_mod, "_notify", AsyncMock()) as notify_mock,
    ):
        hass.data["recorder_instance"] = DummyRecorder("sqlite:///db.sqlite")
        await _handle_copy_history_service(
            hass,
            SimpleNamespace(
                data={
                    "target_entry_id": target_entry.entry_id,
                    "source_entry_id": source_entry.entry_id,
                    "dry_run": True,
                    "confirmation_code": None,
                    "final_confirm": False,
                    "include_auto_map": True,
                    "entity_map": [
                        {
                            "old_entity_id": "sensor.old_a",
                            "new_entity_id": "sensor.new_a",
                        }
                    ],
                }
            ),
        )
    notify_mock.assert_awaited_once()

    with (
        patch.object(migration_services_mod, "_ensure_guard_confirmed", AsyncMock(return_value=True)),
        patch.object(
            migration_services_mod,
            "_copy_legacy_history",
            AsyncMock(return_value=summary),
        ) as copy_mock,
        patch.object(migration_services_mod, "_notify", AsyncMock()) as notify_mock,
    ):
        await _handle_copy_history_service(
            hass,
            SimpleNamespace(
                data={
                    "target_entry_id": target_entry.entry_id,
                    "source_entry_id": source_entry.entry_id,
                    "dry_run": False,
                    "confirmation_code": None,
                    "final_confirm": False,
                    "include_auto_map": False,
                    "entity_map": [
                        {
                            "old_entity_id": "sensor.old_a",
                            "new_entity_id": "sensor.new_a",
                        }
                    ],
                }
            ),
        )
    copy_mock.assert_awaited_once()
    notify_mock.assert_awaited()


def test_unregister_services_handles_non_dict_domain_store(hass: HomeAssistant) -> None:
    """Service cleanup tolerates unexpected domain-store shapes."""
    async_register_migration_services(hass)
    hass.data[DOMAIN] = "not-a-dict"
    async_unregister_migration_services_if_unused(hass)
    assert not hass.services.has_service(DOMAIN, SERVICE_ADOPT_LEGACY_ENTITY_IDS)
    assert not hass.services.has_service(DOMAIN, SERVICE_COPY_LEGACY_HISTORY)


def test_unregister_services_handles_missing_registrations(hass: HomeAssistant) -> None:
    """Unregister should also tolerate the case where no migration services are registered."""
    hass.data[DOMAIN] = {}
    async_unregister_migration_services_if_unused(hass)
    assert not hass.services.has_service(DOMAIN, SERVICE_ADOPT_LEGACY_ENTITY_IDS)
    assert not hass.services.has_service(DOMAIN, SERVICE_COPY_LEGACY_HISTORY)


async def test_remaining_helper_and_service_branches(hass: HomeAssistant) -> None:
    """Smaller helper branches cover remaining notify/merge/service registration paths."""
    with patch("homeassistant.core.ServiceRegistry.async_call", AsyncMock()) as async_call_mock:
        await _notify(hass, "notif", "Title", "Body")
    async_call_mock.assert_awaited_once()

    class ScalarOne:
        def __init__(self, value):
            self._value = value

        def scalar_one_or_none(self):
            return self._value

    class Scalars:
        def __init__(self, values):
            self._values = values

        def scalars(self):
            return self._values

    old_meta = SimpleNamespace(metadata_id=7, entity_id="sensor.old")
    session = MagicMock()
    query_old = MagicMock()
    query_old.filter.return_value.update.return_value = 0
    session.execute.side_effect = [ScalarOne(old_meta), ScalarOne(None)]
    session.query.side_effect = [query_old]
    assert _merge_states_metadata(session, old_entity_id="sensor.old", new_entity_id="sensor.new") == (0, True)

    session = MagicMock()
    delete_query = MagicMock()
    delete_query.filter.return_value.delete.return_value = 1
    update_query = MagicMock()
    update_query.filter.return_value.update.return_value = 2
    session.execute.return_value = Scalars([1, 2])
    session.query.side_effect = [delete_query, update_query]
    assert _merge_statistics_table(
        session,
        migration_services_mod.Statistics,
        old_metadata_id=1,
        new_metadata_id=2,
    ) == 2
    session = MagicMock()
    update_only_query = MagicMock()
    update_only_query.filter.return_value.update.return_value = 3
    session.execute.return_value = Scalars([])
    session.query.return_value = update_only_query
    assert _merge_statistics_table(
        session,
        migration_services_mod.StatisticsShortTerm,
        old_metadata_id=10,
        new_metadata_id=11,
    ) == 3

    old_stat = SimpleNamespace(id=1, source="old", statistic_id="sensor.old")
    session = MagicMock()
    session.execute.side_effect = [Scalars([old_stat]), Scalars([])]
    assert _merge_statistics_metadata(session, old_entity_id="sensor.old", new_entity_id="sensor.new") == (0, 0, True)
    assert old_stat.statistic_id == "sensor.new"

    target_entry = MockConfigEntry(domain=DOMAIN, data={"host": "10.0.0.21"})
    source_entry = MockConfigEntry(domain=LEGACY_DOMAIN, data={"host": "10.0.0.21"})
    target_entry.add_to_hass(hass)
    source_entry.add_to_hass(hass)

    with (
        patch.object(migration_services_mod, "_ensure_guard_confirmed", AsyncMock(return_value=False)),
        patch.object(migration_services_mod, "adopt_legacy_entity_ids", AsyncMock()) as adopt_mock,
    ):
        await _handle_adopt_service(
            hass,
            SimpleNamespace(
                data={
                    "target_entry_id": target_entry.entry_id,
                    "source_entry_id": source_entry.entry_id,
                    "dry_run": False,
                    "confirmation_code": "ABC123",
                    "final_confirm": True,
                }
            ),
        )
    adopt_mock.assert_not_awaited()

    with pytest.raises(HomeAssistantError, match="auto-map failed"):
        with patch.object(
            migration_services_mod,
            "discover_legacy_duplicate_entity_pairs",
            side_effect=HomeAssistantError("auto-map failed"),
        ):
            await _handle_copy_history_service(
                hass,
                SimpleNamespace(
                    data={
                        "target_entry_id": target_entry.entry_id,
                        "source_entry_id": source_entry.entry_id,
                        "dry_run": True,
                        "confirmation_code": None,
                        "final_confirm": False,
                        "include_auto_map": True,
                        "entity_map": [],
                    }
                ),
            )

    pairs = [SimpleNamespace(old_entity_id="sensor.old", new_entity_id="sensor.new")]
    with (
        patch.object(migration_services_mod, "_ensure_guard_confirmed", AsyncMock(return_value=False)),
        patch.object(
            migration_services_mod,
            "discover_legacy_duplicate_entity_pairs",
            return_value=pairs,
        ),
    ):
        await _handle_copy_history_service(
            hass,
            SimpleNamespace(
                data={
                    "target_entry_id": target_entry.entry_id,
                    "source_entry_id": source_entry.entry_id,
                    "dry_run": False,
                    "confirmation_code": "ABC123",
                    "final_confirm": False,
                    "include_auto_map": True,
                    "entity_map": [],
                }
            ),
        )

    async_register_migration_services(hass)
    hass.services.async_remove(DOMAIN, SERVICE_COPY_LEGACY_HISTORY)
    async_register_migration_services(hass)
    async_register_migration_services(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_ADOPT_LEGACY_ENTITY_IDS)
    assert hass.services.has_service(DOMAIN, SERVICE_COPY_LEGACY_HISTORY)
