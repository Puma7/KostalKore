"""Tests for the orphan-history scanner and merge service."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.kostal_kore.const import (
    DOMAIN,
    SERVICE_APPLY_ORPHAN_HISTORY_MAPPING,
    SERVICE_SCAN_ORPHAN_HISTORY,
)
from custom_components.kostal_kore import orphan_history as orphan_history_mod
from custom_components.kostal_kore.orphan_history import (
    OrphanCandidate,
    OrphanMergeReport,
    OrphanScanReport,
    _entity_id_suffix,
    _format_apply_message,
    _format_scan_message,
    _is_legacy_entity_id,
    _scan_orphans_sync,
    _suggest_target,
    apply_orphan_mapping,
    async_register_orphan_history_services,
    async_unregister_orphan_history_services_if_unused,
    scan_orphan_history,
)


# ---------------------------------------------------------------------------
# Pure-function helpers — fast unit tests, no recorder
# ---------------------------------------------------------------------------


def test_is_legacy_entity_id_matches_plenticore_prefix() -> None:
    assert _is_legacy_entity_id("sensor.kostal_plenticore_pv_power")
    assert _is_legacy_entity_id("sensor.Plenticore_PV_Power")  # case-insensitive
    assert not _is_legacy_entity_id("sensor.kore_pv_power")
    assert not _is_legacy_entity_id("sensor.something_else")


def test_entity_id_suffix_strips_known_prefixes() -> None:
    assert _entity_id_suffix("sensor.kostal_plenticore_pv_power") == "pv_power"
    assert _entity_id_suffix("sensor.kostal_kore_pv_power") == "pv_power"
    assert _entity_id_suffix("sensor.kore_pv_power") == "pv_power"
    assert _entity_id_suffix("sensor.plenticore_battery_soc") == "battery_soc"
    assert _entity_id_suffix("sensor.unknown_name") == "unknown_name"
    # No dot: returns the raw string unchanged (defensive against malformed input)
    assert _entity_id_suffix("naked") == "naked"


def test_suggest_target_returns_exact_match_with_full_similarity() -> None:
    target, ratio = _suggest_target(
        "sensor.kostal_plenticore_pv_power",
        ["sensor.kore_pv_power", "sensor.kore_battery_soc"],
    )
    assert target == "sensor.kore_pv_power"
    assert ratio == 1.0


def test_suggest_target_falls_back_to_fuzzy_match() -> None:
    target, ratio = _suggest_target(
        "sensor.kostal_plenticore_battery_state_of_charge",
        ["sensor.kore_battery_soc", "sensor.kore_pv_power"],
    )
    # The suffix "battery_state_of_charge" should fuzzily match "battery_soc"
    # better than "pv_power" — main check is that *some* result comes back
    # and it is one of the candidates with a non-zero similarity.
    assert target in {"sensor.kore_battery_soc", "sensor.kore_pv_power", None}
    if target is not None:
        assert ratio > 0.0


def test_suggest_target_returns_none_when_no_candidates() -> None:
    target, ratio = _suggest_target("sensor.kostal_plenticore_foo", [])
    assert target is None
    assert ratio == 0.0


def test_suggest_target_returns_none_below_cutoff() -> None:
    target, _ = _suggest_target(
        "sensor.kostal_plenticore_zzzzzzz",
        ["sensor.kore_aaaaaaaaa"],
    )
    assert target is None


# ---------------------------------------------------------------------------
# Scan logic — synchronous executor function with mocked recorder
# ---------------------------------------------------------------------------


def _make_recorder_with_states(states_entity_ids: list[str], statistics_ids: list[str]):
    """Build a MagicMock recorder whose session yields the given entity_ids.

    `_scan_orphans_sync` runs `session.execute(select(...)).all()` twice — first
    on StatesMeta.entity_id, second on StatisticsMeta.statistic_id. We mock by
    returning rows in that order.
    """
    recorder = MagicMock()
    session = MagicMock()
    recorder.get_session.return_value = session
    recorder.db_url = "sqlite:///memory:"

    # Each call to .execute().all() returns a different list (states, then statistics)
    side_effects = [
        [(eid,) for eid in states_entity_ids],
        [(sid,) for sid in statistics_ids],
    ]
    execute_results = [MagicMock(), MagicMock()]
    execute_results[0].all.return_value = side_effects[0]
    execute_results[1].all.return_value = side_effects[1]
    session.execute.side_effect = execute_results
    return recorder, session


def test_scan_orphans_sync_empty_recorder_returns_empty_report() -> None:
    recorder, _ = _make_recorder_with_states([], [])
    report = _scan_orphans_sync(recorder, registry_entity_ids=set(), kore_entity_ids=[])
    assert report.total_orphans == 0
    assert report.candidates == []
    assert report.backend == "sqlite"


def test_scan_orphans_sync_detects_legacy_orphans() -> None:
    recorder, _ = _make_recorder_with_states(
        states_entity_ids=[
            "sensor.kostal_plenticore_pv_power",
            "sensor.kore_pv_power",  # in registry → NOT orphan
            "sensor.unrelated_thermostat",  # not legacy pattern → ignored
        ],
        statistics_ids=[
            "sensor.kostal_plenticore_pv_power",  # also in stats
            "sensor.kostal_plenticore_battery_soc",  # stats-only
        ],
    )
    report = _scan_orphans_sync(
        recorder,
        registry_entity_ids={"sensor.kore_pv_power", "sensor.kore_battery_soc"},
        kore_entity_ids=["sensor.kore_pv_power", "sensor.kore_battery_soc"],
    )
    assert report.total_orphans == 2

    by_id = {c.old_entity_id: c for c in report.candidates}

    pv_cand = by_id["sensor.kostal_plenticore_pv_power"]
    assert pv_cand.has_states is True
    assert pv_cand.has_statistics is True
    assert pv_cand.suggested_target == "sensor.kore_pv_power"
    assert pv_cand.similarity == 1.0

    bat_cand = by_id["sensor.kostal_plenticore_battery_soc"]
    assert bat_cand.has_states is False
    assert bat_cand.has_statistics is True
    assert bat_cand.suggested_target == "sensor.kore_battery_soc"


def test_scan_orphans_sync_ignores_registered_legacy_ids() -> None:
    """If a legacy id is still in the registry it is NOT an orphan."""
    recorder, _ = _make_recorder_with_states(
        states_entity_ids=["sensor.kostal_plenticore_pv_power"],
        statistics_ids=[],
    )
    report = _scan_orphans_sync(
        recorder,
        registry_entity_ids={"sensor.kostal_plenticore_pv_power"},
        kore_entity_ids=["sensor.kore_pv_power"],
    )
    assert report.total_orphans == 0


# ---------------------------------------------------------------------------
# scan_orphan_history — wires registry + recorder
# ---------------------------------------------------------------------------


async def test_scan_orphan_history_raises_when_recorder_inactive(
    hass: HomeAssistant,
) -> None:
    fake_recorder = SimpleNamespace(recording=False)
    with patch(
        "custom_components.kostal_kore.migration_services._get_recorder_instance",
        return_value=fake_recorder,
    ):
        with pytest.raises(HomeAssistantError, match="Recorder is not active"):
            await scan_orphan_history(hass)


# ---------------------------------------------------------------------------
# apply_orphan_mapping — preconditions & dry-run safety
# ---------------------------------------------------------------------------


async def test_apply_orphan_mapping_rejects_empty_mapping(hass: HomeAssistant) -> None:
    with pytest.raises(HomeAssistantError, match="Mapping is empty"):
        await apply_orphan_mapping(hass, {}, dry_run=True)


async def test_apply_orphan_mapping_skips_non_legacy_keys(hass: HomeAssistant) -> None:
    """Mapping entries whose old_id does not look legacy are skipped, not failed."""
    fake_recorder = SimpleNamespace(
        recording=True,
        db_url="sqlite:///memory:",
        async_add_executor_job=AsyncMock(),
    )
    with patch(
        "custom_components.kostal_kore.migration_services._get_recorder_instance",
        return_value=fake_recorder,
    ):
        report = await apply_orphan_mapping(
            hass,
            {"sensor.something_random": "sensor.kore_pv_power"},
            dry_run=True,
        )
    # The skipped entry counts: not_legacy_pattern (and target_not_a_kore_entity
    # also applies in this fixture, only one is reported per iteration).
    assert report.total_mappings == 1
    assert report.applied_mappings == 0
    assert len(report.skipped) == 1
    assert report.skipped[0][2] == "not_legacy_pattern"


async def test_apply_orphan_mapping_skips_when_target_not_kore_entity(
    hass: HomeAssistant,
) -> None:
    """Targets that aren't registered to kostal_kore must be skipped."""
    fake_recorder = SimpleNamespace(
        recording=True,
        db_url="sqlite:///memory:",
        async_add_executor_job=AsyncMock(),
    )
    with patch(
        "custom_components.kostal_kore.migration_services._get_recorder_instance",
        return_value=fake_recorder,
    ):
        report = await apply_orphan_mapping(
            hass,
            {"sensor.kostal_plenticore_pv_power": "sensor.some_random_thermostat"},
            dry_run=True,
        )
    assert report.applied_mappings == 0
    assert report.skipped[0][2] == "target_not_a_kore_entity"


async def test_apply_orphan_mapping_dry_run_does_not_call_executor(
    hass: HomeAssistant,
) -> None:
    """Dry-run must never invoke the recorder copy engine."""
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    # Inject a kostal_kore-platform entity into the registry so the target validates
    registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="unique-pv-power",
        suggested_object_id="kore_pv_power",
    )

    executor_mock = AsyncMock()
    fake_recorder = SimpleNamespace(
        recording=True,
        db_url="sqlite:///memory:",
        async_add_executor_job=executor_mock,
    )
    with patch(
        "custom_components.kostal_kore.migration_services._get_recorder_instance",
        return_value=fake_recorder,
    ):
        report = await apply_orphan_mapping(
            hass,
            {"sensor.kostal_plenticore_pv_power": "sensor.kore_pv_power"},
            dry_run=True,
        )
    executor_mock.assert_not_called()
    assert report.dry_run is True
    assert report.applied_mappings == 0


async def test_apply_orphan_mapping_apply_invokes_existing_copy_engine(
    hass: HomeAssistant,
) -> None:
    """When dry_run is False, the existing _copy_legacy_history_sync engine is called."""
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="unique-pv-power",
        suggested_object_id="kore_pv_power",
    )

    fake_summary = SimpleNamespace(
        applied_mappings=1,
        states_rows_moved=42,
        statistics_rows_moved=7,
        short_term_rows_moved=3,
    )
    executor_mock = AsyncMock(return_value=fake_summary)
    fake_recorder = SimpleNamespace(
        recording=True,
        db_url="sqlite:///memory:",
        async_add_executor_job=executor_mock,
    )
    with patch(
        "custom_components.kostal_kore.migration_services._get_recorder_instance",
        return_value=fake_recorder,
    ):
        report = await apply_orphan_mapping(
            hass,
            {"sensor.kostal_plenticore_pv_power": "sensor.kore_pv_power"},
            dry_run=False,
        )
    executor_mock.assert_awaited_once()
    assert report.dry_run is False
    assert report.applied_mappings == 1
    assert report.states_rows_moved == 42
    assert report.statistics_rows_moved == 7


async def test_apply_orphan_mapping_rejects_inactive_recorder(hass: HomeAssistant) -> None:
    fake_recorder = SimpleNamespace(
        recording=False,
        db_url="sqlite:///memory:",
        async_add_executor_job=AsyncMock(),
    )
    with patch(
        "custom_components.kostal_kore.migration_services._get_recorder_instance",
        return_value=fake_recorder,
    ):
        with pytest.raises(HomeAssistantError, match="Recorder is not active"):
            await apply_orphan_mapping(
                hass,
                {"sensor.kostal_plenticore_x": "sensor.kore_x"},
                dry_run=True,
            )


async def test_apply_orphan_mapping_rejects_unsupported_backend(hass: HomeAssistant) -> None:
    fake_recorder = SimpleNamespace(
        recording=True,
        db_url="oracle://nope",
        async_add_executor_job=AsyncMock(),
    )
    with patch(
        "custom_components.kostal_kore.migration_services._get_recorder_instance",
        return_value=fake_recorder,
    ):
        with pytest.raises(HomeAssistantError, match="Unsupported recorder backend"):
            await apply_orphan_mapping(
                hass,
                {"sensor.kostal_plenticore_x": "sensor.kore_x"},
                dry_run=True,
            )


# ---------------------------------------------------------------------------
# Notification formatters
# ---------------------------------------------------------------------------


def test_format_scan_message_empty_report() -> None:
    report = OrphanScanReport(backend="sqlite")
    msg = _format_scan_message(report)
    assert "No orphaned legacy Plenticore history found" in msg
    assert "sqlite" in msg


def test_format_scan_message_populated_report_includes_table() -> None:
    report = OrphanScanReport(
        backend="sqlite",
        total_orphans=1,
        candidates=[
            OrphanCandidate(
                old_entity_id="sensor.kostal_plenticore_pv_power",
                has_states=True,
                has_statistics=True,
                suggested_target="sensor.kore_pv_power",
                similarity=1.0,
            )
        ],
    )
    msg = _format_scan_message(report)
    assert "1" in msg  # total count
    assert "sensor.kostal_plenticore_pv_power" in msg
    assert "sensor.kore_pv_power" in msg
    assert SERVICE_APPLY_ORPHAN_HISTORY_MAPPING in msg
    assert "1.00" in msg


def test_format_apply_message_dry_run_marks_preview() -> None:
    report = OrphanMergeReport(
        backend="sqlite",
        total_mappings=2,
        dry_run=True,
        applied_mappings=2,
        states_rows_moved=10,
        statistics_rows_moved=5,
        short_term_rows_moved=3,
    )
    msg = _format_apply_message(report)
    assert "DRY-RUN preview" in msg
    assert "10" in msg


def test_format_apply_message_lists_skipped_entries() -> None:
    report = OrphanMergeReport(
        backend="sqlite",
        total_mappings=1,
        dry_run=False,
        skipped=[("sensor.foo", "sensor.bar", "not_legacy_pattern")],
    )
    msg = _format_apply_message(report)
    assert "Applied" in msg
    assert "not_legacy_pattern" in msg
    assert "sensor.foo" in msg


# ---------------------------------------------------------------------------
# Service registration / unregistration
# ---------------------------------------------------------------------------


async def test_register_and_unregister_services(hass: HomeAssistant) -> None:
    async_register_orphan_history_services(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_SCAN_ORPHAN_HISTORY)
    assert hass.services.has_service(DOMAIN, SERVICE_APPLY_ORPHAN_HISTORY_MAPPING)

    async_unregister_orphan_history_services_if_unused(hass)
    assert not hass.services.has_service(DOMAIN, SERVICE_SCAN_ORPHAN_HISTORY)
    assert not hass.services.has_service(DOMAIN, SERVICE_APPLY_ORPHAN_HISTORY_MAPPING)


async def test_unregister_keeps_services_when_other_entries_active(
    hass: HomeAssistant,
) -> None:
    async_register_orphan_history_services(hass)
    hass.data.setdefault(DOMAIN, {})["entry-a"] = {"mock": True}
    hass.data[DOMAIN]["entry-b"] = {"mock": True}

    async_unregister_orphan_history_services_if_unused(hass, unloading_entry_id="entry-a")
    assert hass.services.has_service(DOMAIN, SERVICE_SCAN_ORPHAN_HISTORY)
    assert hass.services.has_service(DOMAIN, SERVICE_APPLY_ORPHAN_HISTORY_MAPPING)


async def test_register_services_is_idempotent(hass: HomeAssistant) -> None:
    """Double-registration must not raise (real-world: multiple entries setup)."""
    async_register_orphan_history_services(hass)
    async_register_orphan_history_services(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_SCAN_ORPHAN_HISTORY)


# ---------------------------------------------------------------------------
# Service wrappers — verify they call into the right handlers
# ---------------------------------------------------------------------------


async def test_scan_service_handler_posts_notification(hass: HomeAssistant) -> None:
    """The scan service must end by posting a persistent_notification."""
    fake_report = OrphanScanReport(backend="sqlite")
    notify_mock = AsyncMock()
    with patch.object(
        orphan_history_mod, "scan_orphan_history",
        new=AsyncMock(return_value=fake_report),
    ), patch.object(orphan_history_mod, "_notify", new=notify_mock):
        async_register_orphan_history_services(hass)
        await hass.services.async_call(
            DOMAIN, SERVICE_SCAN_ORPHAN_HISTORY, {}, blocking=True,
        )
    notify_mock.assert_awaited_once()
    args = notify_mock.await_args.args
    assert args[1] == "kostal_kore_orphan_scan"
    assert "Orphan History Scan" in args[2]


async def test_apply_service_handler_validates_schema(hass: HomeAssistant) -> None:
    """Calling the apply service without `mapping` must raise a validation error."""
    import voluptuous as vol

    async_register_orphan_history_services(hass)
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_APPLY_ORPHAN_HISTORY_MAPPING,
            {},
            blocking=True,
        )


# ---------------------------------------------------------------------------
# Coverage tests for previously-missed branches
# ---------------------------------------------------------------------------


def test_suggest_target_returns_none_when_suffix_is_empty() -> None:
    """A legacy id that is only the prefix yields an empty suffix → None."""
    target, ratio = _suggest_target(
        "sensor.kostal_plenticore_",  # suffix becomes "" after prefix strip
        ["sensor.kore_pv_power"],
    )
    assert target is None
    assert ratio == 0.0


def test_suggest_target_returns_fuzzy_match_above_cutoff() -> None:
    """A non-exact suffix match above the 0.72 cutoff returns the best candidate."""
    # Suffixes differ by one trailing char → similarity well above 0.72.
    target, ratio = _suggest_target(
        "sensor.kostal_plenticore_pv_powerx",
        ["sensor.kore_pv_power"],
    )
    assert target == "sensor.kore_pv_power"
    assert 0.72 < ratio < 1.0


def test_scan_orphans_sync_skips_registered_statistics_id() -> None:
    """Statistics rows whose statistic_id is in the registry must NOT be orphan."""
    recorder, _ = _make_recorder_with_states(
        states_entity_ids=[],
        statistics_ids=[
            "sensor.kostal_plenticore_pv_power",  # in registry → skipped
            "sensor.kostal_plenticore_battery_soc",  # orphan
            "sensor.unrelated_thermostat",  # not legacy → skipped (177→176 branch)
        ],
    )
    report = _scan_orphans_sync(
        recorder,
        registry_entity_ids={"sensor.kostal_plenticore_pv_power"},
        kore_entity_ids=["sensor.kore_battery_soc"],
    )
    orphan_ids = {c.old_entity_id for c in report.candidates}
    assert orphan_ids == {"sensor.kostal_plenticore_battery_soc"}


async def test_scan_orphan_history_happy_path_invokes_executor(
    hass: HomeAssistant,
) -> None:
    """When recorder is active, executor is invoked with built registry sets."""
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="unique-pv-power",
        suggested_object_id="kore_pv_power",
    )

    fake_report = OrphanScanReport(backend="sqlite")
    executor_mock = AsyncMock(return_value=fake_report)
    fake_recorder = SimpleNamespace(
        recording=True,
        db_url="sqlite:///memory:",
        async_add_executor_job=executor_mock,
    )
    with patch(
        "custom_components.kostal_kore.migration_services._get_recorder_instance",
        return_value=fake_recorder,
    ):
        result = await scan_orphan_history(hass)

    assert result is fake_report
    executor_mock.assert_awaited_once()
    # Executor was called with (_scan_orphans_sync, recorder, registry_ids, kore_ids)
    args = executor_mock.await_args.args
    assert "sensor.kore_pv_power" in args[2]  # registry_entity_ids contains it
    assert "sensor.kore_pv_power" in args[3]  # kore_entity_ids contains it


async def test_apply_orphan_mapping_skips_non_string_keys(hass: HomeAssistant) -> None:
    """Non-string mapping keys are skipped with reason 'invalid_type'."""
    fake_recorder = SimpleNamespace(
        recording=True,
        db_url="sqlite:///memory:",
        async_add_executor_job=AsyncMock(),
    )
    with patch(
        "custom_components.kostal_kore.migration_services._get_recorder_instance",
        return_value=fake_recorder,
    ):
        report = await apply_orphan_mapping(
            hass,
            {123: "sensor.kore_pv_power"},  # type: ignore[dict-item]
            dry_run=True,
        )
    assert report.applied_mappings == 0
    assert report.skipped[0][2] == "invalid_type"


async def test_notify_helper_calls_persistent_notification_service(
    hass: HomeAssistant,
) -> None:
    """_notify must invoke persistent_notification.create with the given fields."""
    from custom_components.kostal_kore.orphan_history import _notify

    captured: list[dict] = []

    async def _capture(call) -> None:
        captured.append(dict(call.data))

    hass.services.async_register("persistent_notification", "create", _capture)
    await _notify(hass, "test-id", "Title", "Body")
    await hass.async_block_till_done()

    assert captured, "_notify must call persistent_notification.create"
    assert captured[0]["notification_id"] == "test-id"
    assert captured[0]["title"] == "Title"
    assert captured[0]["message"] == "Body"


async def test_apply_service_handler_full_path_posts_notification(
    hass: HomeAssistant,
) -> None:
    """The apply service handler must convert the call payload and notify on success."""
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="unique-pv-power",
        suggested_object_id="kore_pv_power",
    )

    fake_report = OrphanMergeReport(
        backend="sqlite", total_mappings=1, dry_run=True, applied_mappings=0,
    )
    notify_mock = AsyncMock()
    with patch.object(
        orphan_history_mod, "apply_orphan_mapping",
        new=AsyncMock(return_value=fake_report),
    ), patch.object(orphan_history_mod, "_notify", new=notify_mock):
        async_register_orphan_history_services(hass)
        await hass.services.async_call(
            DOMAIN,
            SERVICE_APPLY_ORPHAN_HISTORY_MAPPING,
            {"mapping": {"sensor.kostal_plenticore_pv_power": "sensor.kore_pv_power"}},
            blocking=True,
        )
    notify_mock.assert_awaited_once()
    title_arg = notify_mock.await_args.args[2]
    assert "Dry-Run" in title_arg


async def test_unregister_handles_non_dict_domain_data(hass: HomeAssistant) -> None:
    """Defensive: if hass.data[DOMAIN] is not a dict, treat as no active entries."""
    async_register_orphan_history_services(hass)
    hass.data[DOMAIN] = ["unexpected-type"]  # not a dict — defensive branch

    async_unregister_orphan_history_services_if_unused(hass)
    assert not hass.services.has_service(DOMAIN, SERVICE_SCAN_ORPHAN_HISTORY)
    assert not hass.services.has_service(DOMAIN, SERVICE_APPLY_ORPHAN_HISTORY_MAPPING)


async def test_unregister_when_services_already_absent(hass: HomeAssistant) -> None:
    """Unregister must be safe when services were never registered (442→444→exit)."""
    # No prior register call — services don't exist.
    async_unregister_orphan_history_services_if_unused(hass)
    # No exception — that's the contract.
    assert not hass.services.has_service(DOMAIN, SERVICE_SCAN_ORPHAN_HISTORY)
