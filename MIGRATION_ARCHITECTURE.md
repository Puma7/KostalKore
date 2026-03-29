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
