# Complete migration manual (English): `kostal_plenticore` → `kostal_kore`

This guide documents **every** migration path in KOSTAL KORE — from simple device-page buttons to advanced **Developer Tools → Services**. It is written for beginners, advanced users, and professionals.

**Quick links**

| Document | Language | Content |
|----------|----------|---------|
| [MIGRATION_LEITFADEN.md](MIGRATION_LEITFADEN.md) | Deutsch | Vollständiger Leitfaden |
| This guide | English | Full guide |
| [migration_orphan_history.md](migration_orphan_history.md) | Deutsch | Profile “long-time KORE, never migrated” |
| [../migration.md](../migration.md) | English | Compact step-by-step |
| [../MIGRATION_ARCHITECTURE.md](../MIGRATION_ARCHITECTURE.md) | English | Technical limits / architecture |

---

## 1. Which path do I need?

| Profile | Situation | Recommended path |
|---------|-----------|------------------|
| **A – Standard** | `kostal_plenticore` is still installed; you are switching to KORE now | **Buttons** (§3) or equivalent **Services** (§4) |
| **A – Follow-up** | Import succeeded but some entities/history are wrong | `adopt_legacy_entity_ids` + optionally `copy_legacy_history` (§4) |
| **B – Orphan** | KORE has run for months/years; legacy entry is gone; graphs have gaps | **Orphan services** (§6) |
| **Pros / automation** | Scripts, repeatable dry-runs | **Always services** with `dry_run: true` first (§4) |

---

## 2. Overview of all tools

### 2.1 Device page (Settings → Devices → KOSTAL KORE device → Entities)

| Entity | Type | Internal function | What it does |
|--------|------|-------------------|--------------|
| **Import Legacy Plenticore Data** | Button | `migrate_legacy_plenticore_entry` | Merge config/options, rebind registry, **unload** legacy entry (not delete) |
| **Finalize Legacy Cleanup** | Button | `finalize_legacy_cleanup` | Remove leftover legacy entities/devices, **delete** legacy config entry |
| **Legacy Cleanup Confirmation Code** | Text | (input for cleanup) | Paste code from notification (cleanup step 1 of 3) |

Migration buttons are always available on the KORE device (diagnostic category), independent of Modbus.

### 2.2 Developer Tools → Services

| Service | Default `dry_run` | Apply confirmation | Internal function |
|---------|-------------------|--------------------|-------------------|
| `kostal_kore.adopt_legacy_entity_ids` | `true` | 3-step (code + `final_confirm`) | Entity/device registry only |
| `kostal_kore.copy_legacy_history` | `true` | 3-step | Recorder metadata merge |
| `kostal_kore.scan_orphan_history` | — (read-only) | none | Scan DB for orphan legacy IDs |
| `kostal_kore.apply_orphan_history_mapping` | `true` | none (use dry-run first) | Bind orphan history to KORE |

UI field help comes from `custom_components/kostal_kore/services.yaml`.

### 2.3 Critical: Import button ≠ `adopt_legacy_entity_ids` service

| | **Button: Import Legacy Plenticore Data** | **Service: adopt_legacy_entity_ids** |
|---|------------------------------------------|--------------------------------------|
| **Audience** | Beginners, double-press confirm | Pros, preview, repair |
| **Merges config `data`/`options`** | **Yes** | **No** |
| **Entity registry rebind** | Yes | Yes |
| **Legacy entry** | Unloaded, kept in UI list | Unloaded on apply |
| **Dry-run** | No (60 s double-press only) | Yes |
| **Typical use** | First migration | Re-run registry fix only |

**Rule of thumb:** First migration → **Import button**. Use `adopt` only when you must **not** overwrite KORE config/options again.

---

## 3. Path A: Device-page buttons (beginners)

See [MIGRATION_LEITFADEN.md](MIGRATION_LEITFADEN.md) §3 for the same flow in German (screenshots/navigation labels may match your HA language).

**Summary**

1. Full HA backup.
2. Add KORE integration; keep legacy entry until import finishes.
3. **Import Legacy Plenticore Data:** first press arms 60 s window → second press runs `migrate_legacy_plenticore_entry`.
4. Test dashboards/automations for days or weeks.
5. **Finalize Legacy Cleanup:** 3-step flow (notification code → text entity → final press within 60 s) runs `finalize_legacy_cleanup`.

---

## 4. Path B: Services (advanced / pros)

### 4.1 `kostal_kore.adopt_legacy_entity_ids`

Registry-only rebind. Preview:

```yaml
service: kostal_kore.adopt_legacy_entity_ids
data:
  dry_run: true
```

Apply: three calls — `dry_run: false` (get code) → same with `confirmation_code` → same with `final_confirm: true`.

### 4.2 `kostal_kore.copy_legacy_history`

For remaining history gaps after adopt/import. Merges recorder metadata, not blind row copies. Unit mismatches are skipped.

```yaml
service: kostal_kore.copy_legacy_history
data:
  dry_run: true
  include_auto_map: true
```

### 4.3 Recommended order (pros)

1. Backup → adopt dry-run → adopt apply (3 calls) → test → copy history if needed → finalize via **button** (no dedicated cleanup service).

---

## 5. Path C: Orphan history

For users who **never** had a legacy config entry but still have `kostal_plenticore_*` rows in the recorder DB.

1. `kostal_kore.scan_orphan_history`
2. `kostal_kore.apply_orphan_history_mapping` with `dry_run: true`
3. Apply with `dry_run: false`

Details (German): [migration_orphan_history.md](migration_orphan_history.md).

---

## 6. Troubleshooting & FAQ

See [MIGRATION_LEITFADEN.md](MIGRATION_LEITFADEN.md) §8–9 for the full tables (German). Common items:

- **No legacy entry** → restore backup or use orphan path.
- **0 entities migrated** → enable legacy integration; check unique_id format in logs.
- **Automation entity IDs** → update manually after migration.

Technical architecture notes: [MIGRATION_ARCHITECTURE.md](../MIGRATION_ARCHITECTURE.md).

---

## 7. Checklists

**Beginner (buttons only):** backup → KORE setup → import (2 presses) → validate → finalize (3-step) → remove old repo.

**Pro (services):** backup → adopt dry-run/apply → optional copy history → finalize button.

**Orphan:** backup → scan → dry-run apply → apply.

---

*Version: KOSTAL KORE 2.16.x — verify button names and service fields after upgrades.*
