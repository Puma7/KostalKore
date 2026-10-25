# Legacy Migration Architecture — Known Limitations & Remediation Plan

Status: **Open** | Created: 2026-03-28 | Target: before HA Core 2026.8

## Context

KOSTAL KORE provides a migration path from the legacy `kostal_plenticore`
integration via `legacy_migration.py`. The migration moves entity-registry
bindings, device-registry associations, and config-entry data from the old
entry to the new `kostal_kore` entry.

As of March 2026, the migration **works** on current HA versions. However,
four architectural issues have been identified that must be resolved before
HA Core 2026.8 (expected August 2026).

---

## Issue 1: Cross-domain device linking (CRITICAL)

**File:** `legacy_migration.py:407-423`

### Problem
The migration calls `device_registry.async_update_device(add_config_entry_id=target)`
to attach the new `kostal_kore` config entry to a device whose identifiers are
still `("kostal_plenticore", serial_no)`. Meanwhile, the KORE coordinator
(`coordinator.py:330`) creates devices with `identifiers={(DOMAIN, serial_no)}`
where `DOMAIN = "kostal_kore"`.

This means after migration + reload:
- The legacy device keeps identifiers `("kostal_plenticore", serial_no)`.
- A **new** device is created with identifiers `("kostal_kore", serial_no)`.
- Entities may split across two devices.

Home Assistant announced on 2025-07-18 that cross-integration device linking
will stop working in Core 2026.8 when device identifiers become domain-scoped.

### Fix required
During migration, rewrite device identifiers:

```python
device_registry.async_update_device(
    source_device.id,
    add_config_entry_id=target_entry.entry_id,
    remove_config_entry_id=source_entry.entry_id,
    new_identifiers={(DOMAIN, serial_no)},  # <-- rewrite to kostal_kore
)
```

### Prerequisite
- Determine the correct `serial_no` from device identifiers reliably.
- Handle devices with multiple identifier tuples (unlikely but possible).
- Handle the case where the target device already exists (merge or skip).

---

## Issue 2: Migration is not transactional

**File:** `legacy_migration.py:353-431`

### Problem
`migrate_legacy_plenticore_entry()` performs these steps sequentially:
1. **Overwrite target entry** data/options/title (line 355-360)
2. **Move entities** one by one (line 375-405)
3. **Move devices** one by one (line 411-424)
4. **Optionally remove source entry** (line 426-430)
5. **Reload target entry** (line 431)

If step 2 or 3 fails mid-way, the target entry already has legacy
data/options, but only some entities/devices were moved. There is no rollback.

### Fix required
Implement snapshot-restore pattern:

```python
# Before migration
snapshot = {
    "title": target_entry.title,
    "data": dict(target_entry.data),
    "options": dict(target_entry.options),
}
try:
    # ... perform all migration steps ...
except Exception:
    # Rollback target entry to original state
    hass.config_entries.async_update_entry(
        target_entry, title=snapshot["title"],
        data=snapshot["data"], options=snapshot["options"],
    )
    # Entity/device moves are harder to rollback — log partial state
    raise
```

### Design consideration
Entity/device registry operations are synchronous and local (no I/O), so
failures are rare. The primary risk is a programming error or HA internal
state inconsistency. A pragmatic approach: snapshot entry data, accept that
registry ops are best-effort, and log a clear error if partial migration
occurs.

---

## Issue 3: Device identifiers never rewritten

**File:** `legacy_migration.py:407-423`

This is the technical twin of Issue 1. The `adopt_legacy_entity_ids()` function
(line 309-312) also uses `add_config_entry_id` without `new_identifiers`.

### Both functions need the identifier rewrite:
- `migrate_legacy_plenticore_entry()` — full migration path
- `adopt_legacy_entity_ids()` — lightweight entity-ID adoption path

### Implementation sketch
```python
def _rewrite_device_identifiers(
    device: dr.DeviceEntry,
    legacy_domain: str,
    target_domain: str,
) -> set[tuple[str, str]] | None:
    """Rewrite device identifiers from legacy to target domain."""
    new_ids: set[tuple[str, str]] = set()
    changed = False
    for domain, identifier in device.identifiers:
        if domain == legacy_domain:
            new_ids.add((target_domain, identifier))
            changed = True
        else:
            new_ids.add((domain, identifier))
    return new_ids if changed else None
```

---

## Issue 4: Unique-ID rewriting covers only one pattern

**File:** `legacy_migration.py:78-89`

### Problem
`_rewrite_unique_id()` handles two patterns:
1. `unique_id == source_entry_id` → replace with `target_entry_id`
2. `unique_id.startswith(f"{source_entry_id}_")` → prefix swap

Entities with other unique-ID structures (serial-based, platform-prefixed,
or composite keys) pass through unchanged. This can leave:
- **Duplicate entities**: if KORE creates an entity with a different unique-ID
  for the same logical sensor.
- **Grey entities**: if the rebound entity's unique-ID doesn't match what KORE
  expects, the entity becomes orphaned after reload.

### Fix required
Audit all unique-ID patterns used by `kostal_plenticore` and `kostal_kore`:

```
# Known patterns to investigate:
# 1. entry_id                        (handled)
# 2. entry_id_suffix                 (handled)
# 3. serial_suffix                   (NOT handled)
# 4. hostname_suffix                 (NOT handled)
# 5. platform_entry_id_suffix        (NOT handled)
```

Build a mapping table or chain of rewrite rules. Consider a fallback that
logs unhandled patterns for manual review.

---

## Remediation Timeline

| Phase | Target | Scope |
|-------|--------|-------|
| Phase 1 | Before 2026.6 | Device identifier rewrite (Issues 1+3) |
| Phase 2 | Before 2026.7 | Transaction safety (Issue 2) |
| Phase 3 | Before 2026.8 | Unique-ID pattern audit (Issue 4) |
| Validation | 2026.8-rc | Test against HA 2026.8 release candidate |

### Testing strategy
1. Unit tests with mock registries for all rewrite patterns.
2. Integration test: full migration cycle on a test HA instance with real
   `kostal_plenticore` entities.
3. Regression test: verify no duplicate devices after migration + reload.
4. Forward-compatibility test: run against HA 2026.8-rc to confirm
   domain-scoped identifiers work.

---

## References

- HA Blog 2025-07-18: [Updated guidelines for helper integrations linking to devices](https://developers.home-assistant.io/blog/2025/07/18/updated-pattern-for-helpers-linking-to-devices/)
- HA Config Entry Migration docs: [developers.home-assistant.io](https://developers.home-assistant.io/docs/config_entries_config_flow_handler/#config-entry-migration)
- Current migration code: `custom_components/kostal_kore/legacy_migration.py`
- Current coordinator device setup: `custom_components/kostal_kore/coordinator.py:328-337`
- Migration guide for users: `migration.md`

---

## Addendum: Profile B (orphan-history) — added 2026-05

The original document above covers Profile A: both old and new config entries
loaded simultaneously, registry-driven discovery. A second profile surfaced
in user reports:

**Profile B — long-time KORE installation that never ran the legacy import.**
The Recorder still holds `sensor.kostal_plenticore_*` rows, but the Entity
Registry has no legacy entries; `discover_legacy_duplicate_entity_pairs`
returns empty.

### Architecture decision

Rather than extend the registry-driven flow with a "phantom registry" mode,
Profile B is a separate thin module (`orphan_history.py`) that:

1. Queries `StatesMeta.entity_id` and `StatisticsMeta.statistic_id` directly
   to enumerate Recorder content.
2. Cross-references against the live Entity Registry to identify orphans.
3. Fuzzy-matches orphans to current KORE entities (suffix-normalised
   entity_id, `difflib` ratio ≥ 0.72).
4. **Delegates the actual row movement to `_copy_legacy_history_sync`** —
   the QA-2 unit-mismatch guard, the duplicate-source guard, and the
   transaction/rollback machinery all carry over without duplication.

### Why not a wizard UI

Custom integrations cannot cleanly inject UI on the device page in HA. The
implementation cost of a Lovelace custom card or a custom panel exceeds the
implementation cost of the entire scan+apply machinery, and would lock the
integration to specific HA frontend versions. The MVP ships as two services
+ persistent-notification reports + a documentation page.

### Status

Profile B is implemented in `custom_components/kostal_kore/orphan_history.py`
(commit `bca1587`). User-facing walkthrough in
`docs/migration_orphan_history.md`. Phase 1/2/3 from the original
remediation timeline above remain valid for Profile A.

---

## Addendum: status update (2026-06) — Issues 1 & 3 implemented + forward-compat test

The device-identifier rewrite described in Issues 1 and 3 **is implemented**, via
the two-layer flow:

- `migrate_legacy_plenticore_entry()` merges `(kostal_kore, serial)` onto the legacy
  device (`merge_identifiers`) — leaving an **interim dual-identifier state** while the
  legacy entry is kept for safe rollback.
- `finalize_legacy_cleanup()` rewrites the device domain-clean (`new_identifiers`,
  dropping `kostal_plenticore`) and removes the legacy entry.

Regression coverage in `Tests/test_legacy_migration.py`:

- `test_finalize_cleanup_strips_legacy_domain_from_device_identifiers` — cleanup in
  isolation removes the legacy-domain identifier.
- `test_full_migration_then_cleanup_is_forward_compatible_2026_8` — the **end-to-end**
  migrate→cleanup pipeline ends with only the `(kostal_kore, serial)` identifier (the
  domain-scoped state HA Core 2026.8 requires), and asserts the interim dual state.

**Residual exposure:** between `migrate` and `finalize_legacy_cleanup` the device carries
both domain identifiers (the two-layer safety window). Users should complete cleanup
before upgrading to HA 2026.8.

**Still open:** Issue 2 (transactional rollback in the migrate step) and Issue 4
(unique-ID pattern coverage beyond `{entry_id}_*`). A forward-compatibility run against an
HA 2026.8 release candidate is recommended once one is available.
