# Orphan History Migration

**English** · [Deutsch](../de/orphan-history.md)

> Overview of all migration paths (buttons, services, profiles): [migration.md](migration.md)

## When do I need this?

You need this tool if:

- You have been using **kostal_kore for months or years** without ever running the legacy migration.
- Your dashboards show the new `sensor.*` entities of kostal_kore, but the **history graphs have gaps or an empty past**.
- Your recorder database contains old `sensor.kostal_plenticore_*` rows that nothing queries anymore.

If you have just migrated from `kostal_plenticore`, you do **not** need these services — use the standard migration path instead (button on the device page + `adopt_legacy_entity_ids` service).

## What happens (in short)

**No raw data is deleted.** The old `StatesMeta` and `StatisticsMeta` rows are re-pointed to the current kostal_kore entities. On conflicts (e.g. different units or duplicate statistics sources), the affected merge is skipped instead of overwritten.

## Step 1 — Backup

Back up your Home Assistant database before the apply. Settings → System → Backups → "Full backup".

## Step 2 — Scan (read-only, always safe)

Developer Tools → Services:

```yaml
service: kostal_kore.scan_orphan_history
```

A persistent notification with a table appears:

| Legacy entity_id | States | Stats | Suggested target | Similarity |
| --- | :---: | :---: | --- | :---: |
| `sensor.kostal_plenticore_pv_power` | ✓ | ✓ | `sensor.kore_pv_power` | 1.00 |
| `sensor.kostal_plenticore_battery_soc` | · | ✓ | `sensor.kore_battery_soc` | 1.00 |

Check the suggestions. `Similarity = 1.00` means an exact suffix match; you should verify values below `0.85` manually.

## Step 3 — Dry run

Put together your mapping and call the apply service with `dry_run: true`:

```yaml
service: kostal_kore.apply_orphan_history_mapping
data:
  dry_run: true
  mapping:
    sensor.kostal_plenticore_pv_power: sensor.kore_pv_power
    sensor.kostal_plenticore_battery_soc: sensor.kore_battery_soc
```

A second persistent notification shows how many rows would be moved and which mappings are skipped (e.g. because the target is not a kostal_kore entity or the units do not match).

## Step 4 — Apply

If the dry run looks plausible:

```yaml
service: kostal_kore.apply_orphan_history_mapping
data:
  dry_run: false
  mapping:
    sensor.kostal_plenticore_pv_power: sensor.kore_pv_power
```

The operation runs in a recorder transaction — on errors it is rolled back.

## What is rejected

- **Target is not a kostal_kore entity** — prevents you from merging Plenticore history into a foreign sensor.
- **Source has no legacy pattern** — protects against typos that happen to point to a foreign entity.
- **Old unit ≠ new unit** — the existing migration guard (QA-2) takes over; affected mappings are skipped with a warning in the log.
- **Recorder not active** or **unsupported backend** — the service aborts early.

## Backend compatibility

Works on SQLite, MariaDB and PostgreSQL. Other recorder backends are rejected.

## Known limitations

- The fuzzy-match heuristic (`difflib` suffix comparison) works well for the obvious cases (`pv_power`, `battery_soc`, `home_grid_p`), but not for entities that were renamed completely. If in doubt, map manually.
- Mapping validation requires the target to exist in the entity registry of the `kostal_kore` platform. If you have just disabled the target, enable it briefly for the merge.
- Running the apply service repeatedly is idempotent — on the second run there are no source rows left, so the merge is skipped.
