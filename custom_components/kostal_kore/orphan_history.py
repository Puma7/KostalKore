"""Detect and merge orphaned Recorder history from removed legacy entries.

Use case: a user installed kostal_kore months or years ago without ever
running the legacy migration. The Entity Registry now contains only the new
kostal_kore entities, but the Recorder DB still carries old entity_ids from
the removed kostal_plenticore integration (sensor.kostal_plenticore_*). These
are "orphans": rows exist, but no Entity Registry binding points at them, so
they are invisible in dashboards and history views.

This module:

1. Scans `StatesMeta` + `StatisticsMeta` for entity_ids that match legacy
   Plenticore patterns AND have no corresponding Entity Registry entry.
2. Suggests mappings to current kostal_kore entities via fuzzy string matching
   on the entity_id suffix (the part after the integration prefix).
3. Applies mappings by reusing the existing `_copy_legacy_history_sync` engine
   from `migration_services`, which already enforces unit-mismatch protection
   and duplicate-source guards.

The MVP exposes two services (`scan_orphan_history`, `apply_orphan_history_mapping`)
and does NOT add any UI or wizard surface. Power users invoke the services
directly via Developer Tools or YAML automations; documentation guides them.
"""

from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass, field
from typing import Any, Final

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .const import (
    DOMAIN,
    SERVICE_APPLY_ORPHAN_HISTORY_MAPPING,
    SERVICE_SCAN_ORPHAN_HISTORY,
)

_LOGGER: Final = logging.getLogger(__name__)

# Legacy entity_id substrings that mark a row as Plenticore-derived. The
# kostal_plenticore prefix covers the vast majority; the additional tokens
# catch entries the user manually renamed (e.g. "pv_power") that still belong
# to the Plenticore device family. Empty fragments are ignored. `.wr_` is
# anchored to the slug start to cover entries the user renamed via a device
# named "WR" / "WR2" (common shorthand for Wechselrichter) — without the
# leading dot it would over-match generic substrings like "warning".
_LEGACY_ENTITY_ID_FRAGMENTS: Final[tuple[str, ...]] = (
    "kostal_plenticore",
    "plenticore",
    ".wr_",
    ".wr2_",
)

_CONF_MAPPING: Final[str] = "mapping"
_CONF_DRY_RUN: Final[str] = "dry_run"

_FUZZY_CUTOFF: Final[float] = 0.72

_NOTIFICATION_ID_SCAN: Final[str] = "kostal_kore_orphan_scan"
_NOTIFICATION_ID_APPLY: Final[str] = "kostal_kore_orphan_apply"


@dataclass(slots=True, frozen=True)
class OrphanCandidate:
    """A Recorder entity_id with no current Entity Registry binding.

    `has_states` / `has_statistics` reflect which Recorder tables hold rows
    for this orphan. `suggested_target` is the best-guess current KORE entity
    to merge into; None when no plausible target was found.
    """

    old_entity_id: str
    has_states: bool
    has_statistics: bool
    suggested_target: str | None
    similarity: float


@dataclass(slots=True)
class OrphanScanReport:
    backend: str
    total_orphans: int = 0
    candidates: list[OrphanCandidate] = field(default_factory=list)


@dataclass(slots=True)
class OrphanMergeReport:
    backend: str
    total_mappings: int
    dry_run: bool
    applied_mappings: int = 0
    states_rows_moved: int = 0
    statistics_rows_moved: int = 0
    short_term_rows_moved: int = 0
    skipped: list[tuple[str, str, str]] = field(default_factory=list)


def _is_legacy_entity_id(entity_id: str) -> bool:
    """Return True if `entity_id` looks like it came from the legacy integration."""
    lowered = entity_id.lower()
    return any(fragment in lowered for fragment in _LEGACY_ENTITY_ID_FRAGMENTS)


def _entity_id_suffix(entity_id: str) -> str:
    """Return the descriptive tail of an entity_id with integration tokens stripped.

    `sensor.kostal_plenticore_pv_power` → `pv_power`
    `sensor.kore_pv_power`              → `pv_power`
    Used as the comparison basis for fuzzy mapping. If the input lacks a dot
    we treat it as already local (defensive against malformed input).
    """
    domain, sep, local = entity_id.partition(".")
    if not sep:
        local = domain
    for token in (
        "kostal_plenticore_", "kostal_kore_", "kore_", "plenticore_",
        "wr2_", "wr_",
    ):
        if local.startswith(token):
            local = local[len(token):]
            break
    return local


def _suggest_target(
    old_entity_id: str,
    kore_entity_ids: list[str],
) -> tuple[str | None, float]:
    """Return the best fuzzy-matched current KORE entity_id and similarity ratio."""
    if not kore_entity_ids:
        return None, 0.0
    old_suffix = _entity_id_suffix(old_entity_id)
    if not old_suffix:
        return None, 0.0

    kore_by_suffix = {_entity_id_suffix(eid): eid for eid in kore_entity_ids}
    # Exact suffix match wins outright — same metric on a renamed integration.
    if old_suffix in kore_by_suffix:
        return kore_by_suffix[old_suffix], 1.0

    matches = difflib.get_close_matches(
        old_suffix,
        list(kore_by_suffix.keys()),
        n=1,
        cutoff=_FUZZY_CUTOFF,
    )
    if not matches:
        return None, 0.0
    best_suffix = matches[0]
    ratio = difflib.SequenceMatcher(None, old_suffix, best_suffix).ratio()
    return kore_by_suffix[best_suffix], ratio


def _scan_orphans_sync(
    recorder_instance: Any,
    registry_entity_ids: set[str],
    kore_entity_ids: list[str],
) -> OrphanScanReport:
    """Synchronous Recorder scan; runs inside the recorder executor."""
    from homeassistant.components.recorder.db_schema import StatesMeta, StatisticsMeta
    from sqlalchemy import select

    # Imported lazily here as well so the migration_services helper stays the
    # canonical owner of backend detection (DRY).
    from .migration_services import _detect_recorder_backend

    report = OrphanScanReport(backend=_detect_recorder_backend(recorder_instance))

    states_ids: set[str] = set()
    statistics_ids: set[str] = set()

    session = recorder_instance.get_session()
    try:
        for (entity_id,) in session.execute(select(StatesMeta.entity_id)).all():
            if entity_id and _is_legacy_entity_id(entity_id) and entity_id not in registry_entity_ids:
                states_ids.add(entity_id)
        for (statistic_id,) in session.execute(select(StatisticsMeta.statistic_id)).all():
            if (
                statistic_id
                and _is_legacy_entity_id(statistic_id)
                and statistic_id not in registry_entity_ids
            ):
                statistics_ids.add(statistic_id)
    finally:
        session.close()

    all_orphans = sorted(states_ids | statistics_ids)
    for orphan in all_orphans:
        target, ratio = _suggest_target(orphan, kore_entity_ids)
        report.candidates.append(
            OrphanCandidate(
                old_entity_id=orphan,
                has_states=orphan in states_ids,
                has_statistics=orphan in statistics_ids,
                suggested_target=target,
                similarity=ratio,
            )
        )
    report.total_orphans = len(report.candidates)
    return report


async def scan_orphan_history(hass: HomeAssistant) -> OrphanScanReport:
    """Find Recorder rows from removed legacy Plenticore entities.

    Read-only: never writes to the database. Always callable.
    """
    # Import here to keep the module load cheap when no migration is needed.
    from .migration_services import _get_recorder_instance

    recorder_instance = _get_recorder_instance(hass)
    if not bool(getattr(recorder_instance, "recording", True)):
        raise HomeAssistantError("Recorder is not active.")

    registry = er.async_get(hass)
    registry_entity_ids = {entry.entity_id for entry in registry.entities.values()}
    kore_entity_ids = [
        entry.entity_id
        for entry in registry.entities.values()
        if entry.platform == DOMAIN
    ]

    return await recorder_instance.async_add_executor_job(
        _scan_orphans_sync,
        recorder_instance,
        registry_entity_ids,
        kore_entity_ids,
    )


async def apply_orphan_mapping(
    hass: HomeAssistant,
    mapping: dict[str, str],
    *,
    dry_run: bool = True,
) -> OrphanMergeReport:
    """Re-bind orphan Recorder rows to current KORE entities.

    The actual row movement reuses `_copy_legacy_history_sync` from
    `migration_services`, which enforces unit-mismatch protection and
    duplicate-source guards. We add a precondition check that every target
    entity is registered to the kostal_kore platform — that prevents
    pointing orphans at unrelated integrations.
    """
    from .migration_services import (
        _copy_legacy_history_sync,
        _detect_recorder_backend,
        _get_recorder_instance,
    )

    if not mapping:
        raise HomeAssistantError("Mapping is empty — nothing to apply.")

    registry = er.async_get(hass)
    kore_entity_ids = {
        entry.entity_id
        for entry in registry.entities.values()
        if entry.platform == DOMAIN
    }

    skipped: list[tuple[str, str, str]] = []
    accepted: list[tuple[str, str]] = []
    for old_id, new_id in mapping.items():
        if not isinstance(old_id, str) or not isinstance(new_id, str):
            skipped.append((str(old_id), str(new_id), "invalid_type"))
            continue
        if not _is_legacy_entity_id(old_id):
            skipped.append((old_id, new_id, "not_legacy_pattern"))
            continue
        if new_id not in kore_entity_ids:
            skipped.append((old_id, new_id, "target_not_a_kore_entity"))
            continue
        accepted.append((old_id, new_id))

    recorder_instance = _get_recorder_instance(hass)
    backend = _detect_recorder_backend(recorder_instance)
    if backend not in {"sqlite", "mariadb", "postgresql"}:
        raise HomeAssistantError(f"Unsupported recorder backend '{backend}'.")
    if not bool(getattr(recorder_instance, "recording", True)):
        raise HomeAssistantError("Recorder is not active.")

    report = OrphanMergeReport(
        backend=backend,
        total_mappings=len(mapping),
        dry_run=dry_run,
        skipped=skipped,
    )

    if not accepted or dry_run:
        return report

    summary = await recorder_instance.async_add_executor_job(
        _copy_legacy_history_sync,
        recorder_instance,
        accepted,
    )
    report.applied_mappings = summary.applied_mappings
    report.states_rows_moved = summary.states_rows_moved
    report.statistics_rows_moved = summary.statistics_rows_moved
    report.short_term_rows_moved = summary.short_term_rows_moved
    return report


def _format_scan_message(report: OrphanScanReport) -> str:
    """Build the persistent-notification body for a scan report."""
    if report.total_orphans == 0:
        return (
            "No orphaned legacy Plenticore history found in the Recorder. "
            f"(Backend: {report.backend})"
        )
    lines = [
        f"Found **{report.total_orphans}** orphaned legacy entity ID(s) in the Recorder.",
        f"Backend: {report.backend}",
        "",
        "| Legacy entity_id | States | Stats | Suggested target | Similarity |",
        "| --- | :---: | :---: | --- | :---: |",
    ]
    for cand in report.candidates:
        target = cand.suggested_target or "—"
        sim = f"{cand.similarity:.2f}" if cand.suggested_target else "—"
        lines.append(
            f"| `{cand.old_entity_id}` | "
            f"{'✓' if cand.has_states else '·'} | "
            f"{'✓' if cand.has_statistics else '·'} | "
            f"`{target}` | {sim} |"
        )
    lines.extend(
        [
            "",
            "Next steps: review suggestions, then call "
            f"`{DOMAIN}.{SERVICE_APPLY_ORPHAN_HISTORY_MAPPING}` with a YAML "
            "mapping. Keep `dry_run: true` for the first call.",
        ]
    )
    return "\n".join(lines)


def _format_apply_message(report: OrphanMergeReport) -> str:
    """Build the persistent-notification body for an apply report."""
    header = "DRY-RUN preview" if report.dry_run else "Applied"
    lines = [
        f"**{header}** — {report.applied_mappings}/{report.total_mappings} mapping(s)",
        f"Backend: {report.backend}",
        f"States rows moved: **{report.states_rows_moved}**",
        f"Statistics rows moved: **{report.statistics_rows_moved}**",
        f"Short-term stats rows moved: **{report.short_term_rows_moved}**",
    ]
    if report.skipped:
        lines.extend(["", "**Skipped:**"])
        for old_id, new_id, reason in report.skipped:
            lines.append(f"- `{old_id}` → `{new_id}` — {reason}")
    return "\n".join(lines)


async def _notify(
    hass: HomeAssistant,
    notification_id: str,
    title: str,
    message: str,
) -> None:
    await hass.services.async_call(
        "persistent_notification",
        "create",
        {"notification_id": notification_id, "title": title, "message": message},
        blocking=False,
    )


_APPLY_SCHEMA: Final[vol.Schema] = vol.Schema(
    {
        vol.Required(_CONF_MAPPING): vol.All(
            {cv.entity_id: cv.entity_id},
            vol.Length(min=1),
        ),
        vol.Optional(_CONF_DRY_RUN, default=True): bool,
    }
)


async def _handle_scan_service(hass: HomeAssistant, _call: ServiceCall) -> None:
    report = await scan_orphan_history(hass)
    await _notify(
        hass,
        _NOTIFICATION_ID_SCAN,
        "Kostal Kore — Orphan History Scan",
        _format_scan_message(report),
    )


async def _handle_apply_service(hass: HomeAssistant, call: ServiceCall) -> None:
    mapping = dict(call.data[_CONF_MAPPING])
    dry_run = bool(call.data.get(_CONF_DRY_RUN, True))
    report = await apply_orphan_mapping(hass, mapping, dry_run=dry_run)
    await _notify(
        hass,
        _NOTIFICATION_ID_APPLY,
        "Kostal Kore — Orphan History "
        + ("Dry-Run" if report.dry_run else "Apply"),
        _format_apply_message(report),
    )


def async_register_orphan_history_services(hass: HomeAssistant) -> None:
    """Register orphan-history services once per Home Assistant instance."""

    async def _scan_wrapper(call: ServiceCall) -> None:
        await _handle_scan_service(hass, call)

    async def _apply_wrapper(call: ServiceCall) -> None:
        await _handle_apply_service(hass, call)

    if not hass.services.has_service(DOMAIN, SERVICE_SCAN_ORPHAN_HISTORY):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SCAN_ORPHAN_HISTORY,
            _scan_wrapper,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_APPLY_ORPHAN_HISTORY_MAPPING):
        hass.services.async_register(
            DOMAIN,
            SERVICE_APPLY_ORPHAN_HISTORY_MAPPING,
            _apply_wrapper,
            schema=_APPLY_SCHEMA,
        )


def async_unregister_orphan_history_services_if_unused(
    hass: HomeAssistant,
    *,
    unloading_entry_id: str | None = None,
) -> None:
    """Unregister orphan-history services when no loaded KORE entries remain."""
    domain_data = hass.data.get(DOMAIN, {})
    if not isinstance(domain_data, dict):
        domain_data = {}
    active_entry_ids = {
        str(entry_id)
        for entry_id in domain_data
        if unloading_entry_id is None or str(entry_id) != unloading_entry_id
    }
    if active_entry_ids:
        return
    if hass.services.has_service(DOMAIN, SERVICE_SCAN_ORPHAN_HISTORY):
        hass.services.async_remove(DOMAIN, SERVICE_SCAN_ORPHAN_HISTORY)
    if hass.services.has_service(DOMAIN, SERVICE_APPLY_ORPHAN_HISTORY_MAPPING):
        hass.services.async_remove(DOMAIN, SERVICE_APPLY_ORPHAN_HISTORY_MAPPING)
