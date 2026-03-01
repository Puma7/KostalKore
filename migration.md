# KOSTAL KORE Migration Guide

This guide explains how to migrate from the old `kostal_plenticore` integration to `kostal_kore` without losing entity history.

## Short answer first

Yes, you can move to KOSTAL KORE now.

Recommended order:
1. Keep the old integration installed for now.
2. Install and set up KOSTAL KORE.
3. Run **Import Legacy Plenticore Data** (step 1).
4. Test for a few days/weeks.
5. Run **Finalize Legacy Cleanup** (step 2), then remove old artifacts.

Do **not** delete the old integration before step 1 import.

## Before you start

1. Create a full Home Assistant backup/snapshot.
2. Note your inverter host/IP, password, and service code (if used).
3. Make sure the old `kostal_plenticore` config entry still exists.

## Step-by-step migration

### 1) Install KOSTAL KORE

1. Install `KOSTAL KORE` (HACS or manual).
2. Restart Home Assistant if required.
3. Add the `KOSTAL KORE` integration in **Settings -> Devices & Services**.
4. Complete setup wizard (host or auto-discovery, password, optional service code).

At this point, both old and new integration entries can coexist temporarily.

### 2) Run migration import (safe step)

1. Open your KOSTAL KORE device page.
2. Press button: **Import Legacy Plenticore Data**.

What this does:
- Imports legacy config/options into the KORE entry.
- Rebinds legacy entities/devices to KORE.
- Keeps the old legacy entry for safety.

What to expect:
- A persistent notification with migrated entity/device counts.
- No immediate destructive cleanup.

### 3) Validate during test phase

Use the system normally and verify:
- Entities update correctly.
- Dashboards and automations still work.
- Recorder history is intact for migrated entities.

Keep this phase as long as you want.

### 4) Finalize cleanup (when you are confident)

1. Open the KOSTAL KORE device page again.
2. Press button: **Finalize Legacy Cleanup**.

What this does:
- Removes remaining legacy entities.
- Detaches/removes legacy devices.
- Removes the old `kostal_plenticore` config entry.

After that, you can remove old repository remnants from HACS if still present.

## Troubleshooting

### Error: "No legacy 'kostal_plenticore' config entry found"

The old entry is missing. Restore backup/snapshot or recreate old entry first, then retry import.

### Error: "Multiple legacy entries found"

More than one legacy entry exists. Keep only the intended source entry (or migrate one-by-one).

### Migration button runs but something looks wrong

1. Do not run cleanup yet.
2. Check persistent notification details.
3. Restore backup if needed.
4. Open an issue and attach diagnostics/logs.

## FAQ

### Can I disable the old integration before migration?

Not recommended. Leave it in place until import step finishes successfully.

### Can I delete old integration immediately?

Only after you run **Finalize Legacy Cleanup** and verify KORE works as expected.

### Will history be preserved?

The migration is designed to preserve history by moving registry mappings to the new entry. Always keep a backup as safety net.
