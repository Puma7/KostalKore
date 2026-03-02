"""Service handlers for legacy entity/history migration workflows."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import secrets
import time
from typing import Any, Final, cast

import voluptuous as vol
from sqlalchemy import select

from homeassistant.components.recorder.db_schema import (
    States,
    StatesMeta,
    Statistics,
    StatisticsMeta,
    StatisticsShortTerm,
)
from homeassistant.components.recorder.core import Recorder
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import (
    DATA_KEY_HISTORY_MIGRATION_GUARDS,
    DOMAIN,
    SERVICE_ADOPT_LEGACY_ENTITY_IDS,
    SERVICE_COPY_LEGACY_HISTORY,
)
from .legacy_migration import (
    LegacyEntityPair,
    adopt_legacy_entity_ids,
    discover_legacy_duplicate_entity_pairs,
)

_LOGGER: Final = logging.getLogger(__name__)

_CONF_TARGET_ENTRY_ID: Final[str] = "target_entry_id"
_CONF_SOURCE_ENTRY_ID: Final[str] = "source_entry_id"
_CONF_DRY_RUN: Final[str] = "dry_run"
_CONF_CONFIRMATION_CODE: Final[str] = "confirmation_code"
_CONF_FINAL_CONFIRM: Final[str] = "final_confirm"
_CONF_INCLUDE_AUTO_MAP: Final[str] = "include_auto_map"
_CONF_ENTITY_MAP: Final[str] = "entity_map"
_CONF_OLD_ENTITY_ID: Final[str] = "old_entity_id"
_CONF_NEW_ENTITY_ID: Final[str] = "new_entity_id"

_GUARD_PHASE_IDLE: Final[int] = 0
_GUARD_PHASE_AWAITING_CODE: Final[int] = 1
_GUARD_PHASE_AWAITING_FINAL: Final[int] = 2
_GUARD_CHALLENGE_TTL_SECONDS: Final[int] = 300
_GUARD_FINAL_TTL_SECONDS: Final[int] = 60
_GUARD_CODE_LEN: Final[int] = 6
_GUARD_CODE_ALPHABET: Final[str] = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

_ACTION_ADOPT: Final[str] = "adopt_legacy_entity_ids"
_ACTION_COPY: Final[str] = "copy_legacy_history"
_DATA_RECORDER_INSTANCE: Final[str] = "recorder_instance"


_MAPPING_ITEM_SCHEMA: Final[vol.Schema] = vol.Schema(
    {
        vol.Required(_CONF_OLD_ENTITY_ID): cv.entity_id,
        vol.Required(_CONF_NEW_ENTITY_ID): cv.entity_id,
    }
)

_COMMON_SCHEMA: Final[dict[Any, Any]] = {
    vol.Optional(_CONF_TARGET_ENTRY_ID): str,
    vol.Optional(_CONF_SOURCE_ENTRY_ID): str,
    vol.Optional(_CONF_DRY_RUN, default=True): bool,
    vol.Optional(_CONF_CONFIRMATION_CODE): str,
    vol.Optional(_CONF_FINAL_CONFIRM, default=False): bool,
}

_ADOPT_SERVICE_SCHEMA: Final[vol.Schema] = vol.Schema(dict(_COMMON_SCHEMA))
_COPY_SERVICE_SCHEMA: Final[vol.Schema] = vol.Schema(
    {
        **_COMMON_SCHEMA,
        vol.Optional(_CONF_INCLUDE_AUTO_MAP, default=True): bool,
        vol.Optional(_CONF_ENTITY_MAP): vol.All(
            cv.ensure_list,
            [_MAPPING_ITEM_SCHEMA],
        ),
    }
)


@dataclass(slots=True)
class _HistoryCopySummary:
    backend: str
    total_mappings: int
    applied_mappings: int = 0
    states_rows_moved: int = 0
    statistics_rows_moved: int = 0
    short_term_rows_moved: int = 0
    meta_pairs_rebound: int = 0


def _entry_store(hass: HomeAssistant, entry_id: str) -> dict[str, Any]:
    return cast(dict[str, Any], hass.data.setdefault(DOMAIN, {}).setdefault(entry_id, {}))


def _guard_store(hass: HomeAssistant, entry_id: str) -> dict[str, dict[str, Any]]:
    store = _entry_store(hass, entry_id)
    return cast(
        dict[str, dict[str, Any]],
        store.setdefault(DATA_KEY_HISTORY_MIGRATION_GUARDS, {}),
    )


def _reset_guard(hass: HomeAssistant, entry_id: str, action: str) -> None:
    guard_by_action = _guard_store(hass, entry_id)
    guard_by_action[action] = {
        "phase": _GUARD_PHASE_IDLE,
        "code": None,
        "expires_at": 0.0,
    }


def _get_or_init_guard(hass: HomeAssistant, entry_id: str, action: str) -> dict[str, Any]:
    guard_by_action = _guard_store(hass, entry_id)
    return guard_by_action.setdefault(
        action,
        {
            "phase": _GUARD_PHASE_IDLE,
            "code": None,
            "expires_at": 0.0,
        },
    )


def _generate_confirmation_code() -> str:
    return "".join(
        secrets.choice(_GUARD_CODE_ALPHABET) for _ in range(_GUARD_CODE_LEN)
    )


async def _notify(hass: HomeAssistant, notification_id: str, title: str, message: str) -> None:
    await hass.services.async_call(
        "persistent_notification",
        "create",
        {
            "title": title,
            "message": message,
            "notification_id": notification_id,
        },
        blocking=True,
    )


def _resolve_target_entry_id(hass: HomeAssistant, call: ServiceCall) -> str:
    requested_entry_id = str(call.data.get(_CONF_TARGET_ENTRY_ID, "")).strip()
    if requested_entry_id:
        return requested_entry_id

    entries = list(hass.config_entries.async_entries(DOMAIN))
    if len(entries) == 1:
        return entries[0].entry_id

    raise vol.Invalid(
        "target_entry_id is required when multiple kostal_kore entries exist."
    )


def _normalise_mapping_rows(rows: list[dict[str, str]]) -> list[tuple[str, str]]:
    mapping: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        old_entity_id = str(row.get(_CONF_OLD_ENTITY_ID, "")).strip()
        new_entity_id = str(row.get(_CONF_NEW_ENTITY_ID, "")).strip()
        if not old_entity_id or not new_entity_id or old_entity_id == new_entity_id:
            continue
        pair = (old_entity_id, new_entity_id)
        if pair in seen:
            continue
        seen.add(pair)
        mapping.append(pair)
    return mapping


def _build_auto_mapping(pairs: list[LegacyEntityPair]) -> list[tuple[str, str]]:
    return _normalise_mapping_rows(
        [
            {
                _CONF_OLD_ENTITY_ID: pair.old_entity_id,
                _CONF_NEW_ENTITY_ID: pair.new_entity_id,
            }
            for pair in pairs
        ]
    )


async def _ensure_guard_confirmed(
    hass: HomeAssistant,
    *,
    entry_id: str,
    action: str,
    dry_run: bool,
    confirmation_code: str | None,
    final_confirm: bool,
) -> bool:
    """Enforce multi-step confirmation with code challenge for destructive actions."""
    if dry_run:
        _reset_guard(hass, entry_id, action)
        return True

    notification_id = f"kostal_kore_{action}_{entry_id}"
    now = time.monotonic()
    guard = _get_or_init_guard(hass, entry_id, action)
    phase = int(guard.get("phase", _GUARD_PHASE_IDLE))
    expires_at = float(guard.get("expires_at", 0.0))
    expected_code = str(guard.get("code") or "")
    entered_code = (confirmation_code or "").strip().upper()

    if phase in (_GUARD_PHASE_AWAITING_CODE, _GUARD_PHASE_AWAITING_FINAL) and now > expires_at:
        _reset_guard(hass, entry_id, action)
        await _notify(
            hass,
            notification_id,
            "KOSTAL KORE migration confirmation expired",
            "Confirmation expired for safety. Re-run the service to start a new challenge.",
        )
        return False

    if phase == _GUARD_PHASE_IDLE:
        code = _generate_confirmation_code()
        guard["phase"] = _GUARD_PHASE_AWAITING_CODE
        guard["code"] = code
        guard["expires_at"] = now + _GUARD_CHALLENGE_TTL_SECONDS
        await _notify(
            hass,
            notification_id,
            "KOSTAL KORE critical migration confirmation (Step 1/2)",
            (
                "⚠️ This operation may permanently alter registry/recorder history.\n\n"
                f"Copy this confirmation code: `{code}`\n\n"
                "Call the same service again with:\n"
                f"- `{_CONF_CONFIRMATION_CODE}: {code}`\n"
                f"- `{_CONF_DRY_RUN}: false`"
            ),
        )
        return False

    if phase == _GUARD_PHASE_AWAITING_CODE:
        if not entered_code or entered_code != expected_code:
            await _notify(
                hass,
                notification_id,
                "KOSTAL KORE confirmation code mismatch",
                (
                    "Entered confirmation code is invalid.\n\n"
                    "Please use the code from Step 1 and call the service again."
                ),
            )
            return False
        guard["phase"] = _GUARD_PHASE_AWAITING_FINAL
        guard["expires_at"] = now + _GUARD_FINAL_TTL_SECONDS
        await _notify(
            hass,
            notification_id,
            "KOSTAL KORE critical migration confirmation (Step 2/2)",
            (
                "Final confirmation armed.\n\n"
                "Call the same service one more time within "
                f"{_GUARD_FINAL_TTL_SECONDS} seconds with:\n"
                f"- `{_CONF_CONFIRMATION_CODE}: {entered_code}`\n"
                f"- `{_CONF_FINAL_CONFIRM}: true`\n"
                f"- `{_CONF_DRY_RUN}: false`"
            ),
        )
        return False

    # phase == awaiting final
    if not entered_code or entered_code != expected_code or not final_confirm:
        await _notify(
            hass,
            notification_id,
            "KOSTAL KORE final confirmation missing",
            "Final confirmation requires matching code and `final_confirm: true`.",
        )
        return False

    _reset_guard(hass, entry_id, action)
    return True


def _detect_recorder_backend(recorder_instance: Any) -> str:
    db_url = str(getattr(recorder_instance, "db_url", "")).lower()
    if db_url.startswith("sqlite"):
        return "sqlite"
    if db_url.startswith("mysql") or db_url.startswith("mariadb"):
        return "mariadb"
    if db_url.startswith("postgresql"):
        return "postgresql"
    return "unknown"


def _get_recorder_instance(hass: HomeAssistant) -> Recorder:
    recorder_instance = hass.data.get(_DATA_RECORDER_INSTANCE)
    if recorder_instance is None:
        raise HomeAssistantError("Recorder integration is not loaded.")
    if not isinstance(recorder_instance, Recorder):
        raise HomeAssistantError("Recorder instance is not available yet.")
    return recorder_instance


def _merge_states_metadata(
    session: Any,
    *,
    old_entity_id: str,
    new_entity_id: str,
) -> tuple[int, bool]:
    old_meta = session.execute(
        select(StatesMeta).where(StatesMeta.entity_id == old_entity_id)
    ).scalar_one_or_none()
    new_meta = session.execute(
        select(StatesMeta).where(StatesMeta.entity_id == new_entity_id)
    ).scalar_one_or_none()

    if old_meta is None:
        return 0, False

    old_meta_id = int(old_meta.metadata_id)

    if new_meta is not None:
        new_meta_id = int(new_meta.metadata_id)
        moved_rows = int(
            session.query(States)
            .filter(States.metadata_id == new_meta_id)
            .update(
                {"metadata_id": old_meta_id, "entity_id": new_entity_id},
                synchronize_session=False,
            )
        )
        session.delete(new_meta)
    else:
        moved_rows = 0

    # Ensure all retained rows point to the final entity_id.
    session.query(States).filter(States.metadata_id == old_meta_id).update(
        {"entity_id": new_entity_id},
        synchronize_session=False,
    )
    old_meta.entity_id = new_entity_id
    return moved_rows, True


def _merge_statistics_table(
    session: Any,
    table: Any,
    *,
    old_metadata_id: int,
    new_metadata_id: int,
) -> int:
    old_starts = list(
        session.execute(
            select(table.start_ts).where(table.metadata_id == old_metadata_id)
        ).scalars()
    )
    if old_starts:
        session.query(table).filter(
            table.metadata_id == new_metadata_id,
            table.start_ts.in_(old_starts),
        ).delete(synchronize_session=False)

    return int(
        session.query(table)
        .filter(table.metadata_id == new_metadata_id)
        .update({"metadata_id": old_metadata_id}, synchronize_session=False)
    )


def _merge_statistics_metadata(
    session: Any,
    *,
    old_entity_id: str,
    new_entity_id: str,
) -> tuple[int, int, bool]:
    old_meta_rows = list(
        session.execute(
            select(StatisticsMeta).where(StatisticsMeta.statistic_id == old_entity_id)
        ).scalars()
    )
    if not old_meta_rows:
        return 0, 0, False

    new_meta_rows = list(
        session.execute(
            select(StatisticsMeta).where(StatisticsMeta.statistic_id == new_entity_id)
        ).scalars()
    )
    new_by_source = {str(meta.source): meta for meta in new_meta_rows}

    stats_rows_moved = 0
    short_term_rows_moved = 0
    rebound_done = False

    for old_meta in old_meta_rows:
        old_meta_id = int(old_meta.id)
        matching_new = new_by_source.pop(str(old_meta.source), None)
        if matching_new is not None:
            new_meta_id = int(matching_new.id)
            stats_rows_moved += _merge_statistics_table(
                session,
                Statistics,
                old_metadata_id=old_meta_id,
                new_metadata_id=new_meta_id,
            )
            short_term_rows_moved += _merge_statistics_table(
                session,
                StatisticsShortTerm,
                old_metadata_id=old_meta_id,
                new_metadata_id=new_meta_id,
            )
            session.delete(matching_new)
        old_meta.statistic_id = new_entity_id
        rebound_done = True

    return stats_rows_moved, short_term_rows_moved, rebound_done


def _copy_legacy_history_sync(
    recorder_instance: Any,
    mappings: list[tuple[str, str]],
) -> _HistoryCopySummary:
    summary = _HistoryCopySummary(
        backend=_detect_recorder_backend(recorder_instance),
        total_mappings=len(mappings),
    )

    session = recorder_instance.get_session()
    try:
        for old_entity_id, new_entity_id in mappings:
            states_rows_moved, state_rebound = _merge_states_metadata(
                session,
                old_entity_id=old_entity_id,
                new_entity_id=new_entity_id,
            )
            stats_rows_moved, short_term_rows_moved, stats_rebound = (
                _merge_statistics_metadata(
                    session,
                    old_entity_id=old_entity_id,
                    new_entity_id=new_entity_id,
                )
            )

            touched = (
                states_rows_moved > 0
                or stats_rows_moved > 0
                or short_term_rows_moved > 0
                or state_rebound
                or stats_rebound
            )
            if touched:
                summary.applied_mappings += 1
                summary.states_rows_moved += states_rows_moved
                summary.statistics_rows_moved += stats_rows_moved
                summary.short_term_rows_moved += short_term_rows_moved
                summary.meta_pairs_rebound += 1

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    return summary


async def _copy_legacy_history(
    hass: HomeAssistant,
    *,
    mappings: list[tuple[str, str]],
) -> _HistoryCopySummary:
    recorder_instance = _get_recorder_instance(hass)
    backend = _detect_recorder_backend(recorder_instance)
    if backend not in {"sqlite", "mariadb", "postgresql"}:
        raise HomeAssistantError(
            f"Unsupported recorder backend '{backend}'."
        )
    if not bool(getattr(recorder_instance, "recording", True)):
        raise HomeAssistantError("Recorder is not active.")

    summary = await recorder_instance.async_add_executor_job(
        _copy_legacy_history_sync,
        recorder_instance,
        mappings,
    )
    return summary


async def _handle_adopt_service(hass: HomeAssistant, call: ServiceCall) -> None:
    target_entry_id = _resolve_target_entry_id(hass, call)
    source_entry_id = cast(str | None, call.data.get(_CONF_SOURCE_ENTRY_ID))
    dry_run = bool(call.data[_CONF_DRY_RUN])
    confirmation_code = cast(str | None, call.data.get(_CONF_CONFIRMATION_CODE))
    final_confirm = bool(call.data[_CONF_FINAL_CONFIRM])

    if not await _ensure_guard_confirmed(
        hass,
        entry_id=target_entry_id,
        action=_ACTION_ADOPT,
        dry_run=dry_run,
        confirmation_code=confirmation_code,
        final_confirm=final_confirm,
    ):
        return

    result = await adopt_legacy_entity_ids(
        hass,
        target_entry_id=target_entry_id,
        source_entry_id=source_entry_id,
        dry_run=dry_run,
    )

    await _notify(
        hass,
        f"kostal_kore_{_ACTION_ADOPT}_{target_entry_id}",
        "KOSTAL KORE legacy entity-ID adoption",
        (
            f"Mode: {'dry-run' if dry_run else 'applied'}\n\n"
            f"Source entry: `{result.source_entry_id}`\n"
            f"Target entry: `{result.target_entry_id}`\n"
            f"Migrated entities: **{result.migrated_entities}**\n"
            f"Migrated devices: **{result.migrated_devices}**\n"
            f"Removed duplicate target entities: **{result.removed_target_duplicates}**"
        ),
    )


async def _handle_copy_history_service(hass: HomeAssistant, call: ServiceCall) -> None:
    target_entry_id = _resolve_target_entry_id(hass, call)
    source_entry_id = cast(str | None, call.data.get(_CONF_SOURCE_ENTRY_ID))
    dry_run = bool(call.data[_CONF_DRY_RUN])
    confirmation_code = cast(str | None, call.data.get(_CONF_CONFIRMATION_CODE))
    final_confirm = bool(call.data[_CONF_FINAL_CONFIRM])
    include_auto_map = bool(call.data[_CONF_INCLUDE_AUTO_MAP])

    manual_rows = cast(list[dict[str, str]], call.data.get(_CONF_ENTITY_MAP, []))
    manual_mapping = _normalise_mapping_rows(manual_rows)

    auto_mapping: list[tuple[str, str]] = []
    if include_auto_map:
        try:
            auto_pairs = discover_legacy_duplicate_entity_pairs(
                hass,
                target_entry_id=target_entry_id,
                source_entry_id=source_entry_id,
            )
            auto_mapping = _build_auto_mapping(auto_pairs)
        except HomeAssistantError as err:
            if not manual_mapping:
                raise
            _LOGGER.debug("Auto mapping skipped, using manual mapping only: %s", err)

    mapping = _normalise_mapping_rows(
        [
            {_CONF_OLD_ENTITY_ID: old, _CONF_NEW_ENTITY_ID: new}
            for old, new in (manual_mapping + auto_mapping)
        ]
    )
    if not mapping:
        await _notify(
            hass,
            f"kostal_kore_{_ACTION_COPY}_{target_entry_id}",
            "KOSTAL KORE history copy",
            "No entity mapping found. Provide `entity_map` or enable `include_auto_map`.",
        )
        return

    if not await _ensure_guard_confirmed(
        hass,
        entry_id=target_entry_id,
        action=_ACTION_COPY,
        dry_run=dry_run,
        confirmation_code=confirmation_code,
        final_confirm=final_confirm,
    ):
        return

    if dry_run:
        recorder_instance = _get_recorder_instance(hass)
        backend = _detect_recorder_backend(recorder_instance)
        await _notify(
            hass,
            f"kostal_kore_{_ACTION_COPY}_{target_entry_id}",
            "KOSTAL KORE history copy preview",
            (
                "Dry-run preview (no database changes made).\n\n"
                f"Recorder backend: **{backend}**\n"
                f"Mappings detected: **{len(mapping)}**\n"
                f"Auto-mapped pairs: **{len(auto_mapping)}**\n"
                f"Manual pairs: **{len(manual_mapping)}**"
            ),
        )
        return

    summary = await _copy_legacy_history(hass, mappings=mapping)
    await _notify(
        hass,
        f"kostal_kore_{_ACTION_COPY}_{target_entry_id}",
        "KOSTAL KORE history copy completed",
        (
            f"Recorder backend: **{summary.backend}**\n"
            f"Mappings requested: **{summary.total_mappings}**\n"
            f"Mappings applied: **{summary.applied_mappings}**\n"
            f"State rows moved: **{summary.states_rows_moved}**\n"
            f"Statistics rows moved: **{summary.statistics_rows_moved}**\n"
            f"Short-term stats rows moved: **{summary.short_term_rows_moved}**\n"
            f"Metadata pairs rebound: **{summary.meta_pairs_rebound}**"
        ),
    )


def async_register_migration_services(hass: HomeAssistant) -> None:
    """Register migration services once per Home Assistant instance."""
    async def _adopt_service_wrapper(call: ServiceCall) -> None:
        await _handle_adopt_service(hass, call)

    async def _copy_service_wrapper(call: ServiceCall) -> None:
        await _handle_copy_history_service(hass, call)

    if not hass.services.has_service(DOMAIN, SERVICE_ADOPT_LEGACY_ENTITY_IDS):
        hass.services.async_register(
            DOMAIN,
            SERVICE_ADOPT_LEGACY_ENTITY_IDS,
            _adopt_service_wrapper,
            schema=_ADOPT_SERVICE_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_COPY_LEGACY_HISTORY):
        hass.services.async_register(
            DOMAIN,
            SERVICE_COPY_LEGACY_HISTORY,
            _copy_service_wrapper,
            schema=_COPY_SERVICE_SCHEMA,
        )


def async_unregister_migration_services_if_unused(hass: HomeAssistant) -> None:
    """Unregister services when no KORE config entries remain."""
    remaining_entries = list(hass.config_entries.async_entries(DOMAIN))
    if remaining_entries:
        return
    if hass.services.has_service(DOMAIN, SERVICE_ADOPT_LEGACY_ENTITY_IDS):
        hass.services.async_remove(DOMAIN, SERVICE_ADOPT_LEGACY_ENTITY_IDS)
    if hass.services.has_service(DOMAIN, SERVICE_COPY_LEGACY_HISTORY):
        hass.services.async_remove(DOMAIN, SERVICE_COPY_LEGACY_HISTORY)
