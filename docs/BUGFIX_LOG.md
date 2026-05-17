# Bug Fix Log — May 2026

This document records the 11 verified bugs found during the analysis sweep of the
KostalKore Home Assistant integration, the 7 QA findings that surfaced during
the resolution phase, and the architectural learnings collected along the way.

The branch `claude/fix-home-assistant-integration-mVGgC` carries the fixes.
Tests for every fix live in `Tests/test_bug_regression.py`.

---

## Severity legend

| Symbol | Severity | Meaning |
|--------|----------|---------|
| 🔴 | CRITICAL | data corruption or wrong unit shown to user |
| 🟠 | HIGH | feature broken or silent failure mode |
| 🟡 | MEDIUM | resilience / UX degradation |
| 🟢 | LOW | code quality, boundary edge cases |

---

## Original bugs (1–11)

### Bug #1 🔴 — Battery capacity sensor unit

**File:** `custom_components/kostal_kore/sensor.py` (descriptions for `WorkCapacity`
and `FullChargeCap_E`).

`WorkCapacity` was declared with `native_unit_of_measurement="Ah"`, but the
underlying Modbus register `battery_work_capacity` (register 1068) reports in
**Wh**. Long-term statistics interpreted the reading as ampere-hours,
mis-scaling battery storage history by a factor of ~5 (cell-voltage dependent).

**Fix:** `WorkCapacity` → `UnitOfEnergy.WATT_HOUR`, added
`device_class=SensorDeviceClass.ENERGY_STORAGE`, and
`suggested_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR` so the UI shows
`35.7 kWh` rather than `35700 Wh`.

`FullChargeCap_E` was left as `"Ah"`: the REST-API register actually reports
charge capacity in ampere-hours. The regression test documents this as the
expected baseline so a future "fix" doesn't silently flip it.

A `repairs.create_battery_capacity_unit_migration_issue` notice was added so
existing installations are prompted to resolve the Recorder unit-change warning.

### Bug #2 / #5 🟠 — Missing KSEM translation keys in `en.json`

`strings.json` carries 12 keys in `options.step.init.data` (and again in
`config.step.setup_options.data`); `translations/en.json` only had 8. The four
KSEM-related fields (`ksem_enabled`, `ksem_host`, `ksem_port`, `ksem_unit_id`)
were missing, so the KSEM section of the setup/options dialog rendered with
empty labels.

**Fix:** Added the four missing keys to both steps in `en.json`.

### Bug #3 🟡 — Missing `reauth_confirm` description

`strings.json` provided a one-line description for the re-authentication
form, but `en.json` only carried the `data` block. The form rendered without
its explanatory text.

**Fix:** Added the `description` field to `config.step.reauth_confirm` in `en.json`.

### Bug #4 🟠 — Hardcoded button label

`ModbusResetButton` declared `_attr_has_entity_name = True` but set
`_attr_name = "Reset Modbus Registers"` — that combination bypasses
HA's translation system. The button was unlocalizable, and the entity-id
referenced by `dashboards/degradation_dashboard.yaml` could render wrong
in non-English installations.

**Fix:** Replaced `_attr_name` with `_attr_translation_key = "reset_modbus_registers"`
and added the corresponding `entity.button.reset_modbus_registers` block in
`strings.json` + `en.json`.

### Bug #6 🟠 — Modbus DC-string count not clamped

`diagnostics.py:168` and `switch.py:794` correctly clamp the discovered DC
string count to `MAX_SANE_STRING_COUNT` (6). `sensor.py:1519` did not. A
corrupted Modbus response of e.g. `99` would have caused
`generate_dc_sensor_descriptions(99)` to spawn **297** phantom sensors.

**Fix:** Clamp the value before use; log a warning if the raw value exceeds
the sane maximum.

### Bug #7 🟡 — Module-level error wipes good fields

The coordinator originally parsed each module via dict-comprehension:

```python
result[module_id] = {pid: str(module_data[pid].value) for pid in module_data}
```

A single failing `.value` access (broken `ProcessData` instance) raised inside
the comprehension and dropped the entire module to `{}` — every sensor on
that module went `unavailable`.

**Fix:** Per-field try/except; failed fields are collected and (where possible)
back-filled from `_last_result`, the rest of the module is preserved. See
QA-5 for the back-fill caching nuance.

### Bug #8 / #9 🟡 — `CalculatedPvSumSensor` ignores `dc_string_count` and uses a magic string

The class stored `dc_string_count` but iterated `self.coordinator.data` keys
matching the hardcoded string `"devices:local:pv"`. Effects:

1. If any unrelated entity registered an extra PV module key, the calculated
   sum would include it.
2. The magic string duplicated `MODULE_ID_PREFIX` (already exported from the
   same module).

**Fix:** Iterate `range(1, dc_string_count + 1)` and build the module-id with
`f"{MODULE_ID_PREFIX}{dc_num}"`.

### Bug #10 🟢 — Isolation heuristic boundary off-by-one

`helper.py:89`:
```python
if 0 < abs(numeric) < ISOLATION_KOHM_HEURISTIC_MAX:
    return numeric * 1000.0
```

A firmware reading of exactly `1000` (i.e. **1 MΩ — a perfectly healthy
isolation value**) failed the strict `<` check, stayed un-multiplied, and
was therefore stored as **1000 Ω**. With the health monitor's thresholds
(`critical_low = 100_000 Ω`, `warning_low = 500_000 Ω`), that triggered a
false critical alarm.

**Fix:** `<= ISOLATION_KOHM_HEURISTIC_MAX`.

### Bug #11 🟢 — PV energy statistics hardcoded for PV1–PV3

DC power sensors are generated dynamically for the actual string count
(1–6). The energy statistics (`Statistic:EnergyPv1:Day`, …) are statically
defined for PV1, PV2 and PV3 only. Inverters with one string see PV2/PV3
energy sensors permanently `unavailable`; inverters with >3 strings lose
energy stats for PV4–6.

**Status:** Documented in source. Migrating these to a dynamic generator
mirroring `generate_dc_sensor_descriptions()` is left for a follow-up since
it requires HA entity-registry migration logic for upgrades.

---

## QA findings (1–7)

These were uncovered during the adversarial self-review and resolution
phase after the first round of fixes.

### QA-1 🟠 — Isolation restore called before health monitor exists

`_restore_isolation_sample()` was called from `modbus_coordinator.async_setup()`.
The function writes into `self._health_monitor.isolation`, but
`_health_monitor` is injected from `__init__.py` **after** `async_setup()`
returns. The restore was therefore a no-op for every restart.

**Fix:** Removed the call from `async_setup()`. `__init__.py` now schedules
`hass.async_create_task(modbus_coordinator._restore_isolation_sample())`
immediately after the health monitor is attached.

### QA-2 🔴 — Migration `continue` missing on unit mismatch

In `_merge_statistics_metadata`, the unit-mismatch warning was logged but
execution fell through to `old_meta.statistic_id = new_entity_id` — a
silent Recorder corruption: two rows now share the same `statistic_id` with
different units.

**Fix:** `continue` skips both the rename and the row-delete.

### QA-3 🟡 — TOCTOU-style read of suppressed registers via private method

The modbus coordinator computed two `sum()` aggregates using
`client._is_suppressed()`. That method has a **side effect** (drops expired
entries from the strike map). Between the two `sum()` calls the set of
suppressed addresses could change.

**Fix:** Take a snapshot via the public `client.unavailable_registers`
property (a `frozenset`, side-effect free) and use it for both counts.

### QA-4 🟡 — `TOTAL_INCREASING` sensors held stale cache on bad parse

`PlenticoreDataSensor` cached the last good value to survive an occasional
NaN/Inf from the formatter. For `TOTAL_INCREASING` (cumulative-energy)
sensors this is wrong: a real zero-reset (e.g. day rollover, counter reset)
would be hidden behind the cache and HA's long-term statistics would never
see the drop to 0.

**Fix:** When the formatter returns `None`, return `None` directly for
`TOTAL_INCREASING`; only `MEASUREMENT` sensors use the last-valid cache.

### QA-5 🟡 — Stale cascade in field-level back-fill

The Bug #7 fix back-filled failed fields from `_last_result`. After the
update, `_last_result = result` was written — which **included** the
back-filled (stale) values. Next cycle they'd be back-filled again, then
re-cached, forever.

**Fix:** Maintain two dicts in `_async_update_data`:

* `result` — what HA receives; may contain back-filled values.
* `fresh_result` — only freshly parsed values; written to `_last_result`.

A field that fails for two consecutive cycles now goes `unavailable`
(after one grace cycle) rather than perpetuating a stale value forever.

### QA-6 🟡 — `_record_failure` ran before retry, no `clear_issue` on retry success

In the 503-on-first-fetch branch, `_record_failure()` was invoked
immediately, then the retry happened. If the retry succeeded,
`_record_success()` reset the multiplier, but the counter mutation already
happened. The "inverter_busy" repair issue was also never cleared on a
successful retry.

**Fix:** `_record_failure()` moved into the retry-failure branch.
`clear_issue(hass, "inverter_busy", …)` is now called on retry success.

### QA-7 🟢 — Suppressed exception chain (`from None`)

`raise ConfigEntryNotReady("Login timed out") from None` and
`raise UpdateFailed("Timeout fetching process data") from None` discarded the
original traceback, making debugging harder.

**Fix:** Both timeout blocks now bind the original exception
(`except asyncio.TimeoutError as timeout_err`) and chain it via
`from timeout_err`.

---

## Architectural learnings

These bullet points capture the recurring patterns the bug sweep exposed.
They are not "rules" — they are observations worth keeping in mind for
future contributions.

### 1. Per-field error handling beats per-module

Dict comprehensions over external API data are tempting, but a single
broken element raises and drops the whole structure. Always parse
field-by-field when the source is untrusted (REST/Modbus/MQTT). The cost
of an extra `for` loop is invisible next to a network round-trip.

### 2. Cache only what you produced this cycle

If a fallback layer back-fills missing data from a cache, the next cycle
must **not** consume that back-filled value as if it were fresh. Otherwise
a single bad cycle locks the value in forever. Keep a separate
"fresh-only" snapshot for caching (see QA-5).

### 3. Properties with side effects are foot-guns

`_is_suppressed()` looked like a query but mutated state on expiry. Two
calls in the same function gave different answers depending on time of
day. Public, side-effect-free snapshots (`unavailable_registers` returning
a `frozenset`) are easier to reason about.

### 4. `TOTAL_INCREASING` ≠ `MEASUREMENT`

Cumulative counters have semantics that cached-last-value patterns
violate. Always check the `state_class` before applying generic
"last good value" recovery logic — the legal "0" of a daily counter
must be allowed to surface.

### 5. Inclusive boundaries on physical thresholds

Heuristics that scale physical quantities ("if it looks small, multiply
it") should always use inclusive comparisons at the boundary. Real
hardware reports the boundary value, and an off-by-one excludes a single
firmware reading from the conversion path.

### 6. `from None` is a debugging anti-pattern

Suppress chains only when re-raising a domain exception that genuinely
replaces (rather than wraps) the original. For "timeout → UpdateFailed",
keep the chain via `from <original_err>` — the traceback is the only
breadcrumb when this fires in a user log six weeks later.

### 7. Async timing: inject before scheduling

A coroutine that depends on an injected dependency must be scheduled
**after** the injection point. `async_create_task` keeps that schedule
non-blocking while still respecting the dependency order. Calling the
coroutine inside `async_setup()` (before the injecting caller has run)
silently produces a no-op.

### 8. Centralize clamping & magic values

`MAX_SANE_STRING_COUNT` and `MODULE_ID_PREFIX` already existed —
re-implementing them locally created drift between files. When you see
the same constant inline in two files, that's a clamping bug waiting to
happen.

### 9. Translation keys must be paired

`strings.json` is the source of truth; every key referenced by the
config-flow / options-flow / entity description must also appear in
each `translations/<lang>.json`. Adding a new option without adding the
matching translation entry produces an empty label that is impossible to
spot without exercising the dialog manually.

### 10. Migration code: `continue` is mandatory in skip paths

Database-touching migration code has a single read pass per row, and the
"rename/delete" step typically happens unconditionally after the per-row
checks. If a skip condition fires inside the loop, `continue` is the
only way to bypass the unconditional tail. A bare `pass` or a missing
guard cascades into silent data corruption.

---

## File-by-file change summary

| File | Bugs / QAs addressed |
|------|----------------------|
| `custom_components/kostal_kore/sensor.py` | #1, #6, #8, #9, #11 (doc), QA-4 |
| `custom_components/kostal_kore/translations/en.json` | #2, #3, #4, #5 |
| `custom_components/kostal_kore/strings.json` | #4 |
| `custom_components/kostal_kore/modbus_button.py` | #4 |
| `custom_components/kostal_kore/coordinator.py` | #7, QA-5, QA-6, QA-7 |
| `custom_components/kostal_kore/modbus_coordinator.py` | QA-3 |
| `custom_components/kostal_kore/modbus_client.py` | (batching support, MAX_RETRIES) |
| `custom_components/kostal_kore/helper.py` | #10 |
| `custom_components/kostal_kore/migration_services.py` | QA-2 |
| `custom_components/kostal_kore/__init__.py` | QA-1 (isolation restore timing) |
| `Tests/test_bug_regression.py` | regression coverage for all 18 items |

---

## Regression test coverage

`Tests/test_bug_regression.py` carries one or more focused tests per item:

| Test name | Verifies |
|-----------|----------|
| `test_bug1_work_capacity_uses_watt_hour` | `WorkCapacity` is Wh |
| `test_bug1_full_charge_cap_unit_is_ah` | `FullChargeCap_E` baseline (Ah) |
| `test_bug2_ksem_keys_in_options_step` | KSEM keys in options dialog |
| `test_bug5_ksem_keys_in_config_setup_options_step` | KSEM keys in setup dialog |
| `test_bug3_reauth_confirm_has_description` | reauth dialog description present |
| `test_bug4_modbus_reset_button_uses_translation_key` | button uses translation key |
| `test_bug4_entity_translation_key_in_en_json` | translation entry exists |
| `test_bug6_dc_string_count_clamped_to_max` | `99` clamped to `MAX_SANE_STRING_COUNT` |
| `test_bug7_one_bad_field_does_not_wipe_module` | sibling fields survive |
| `test_bug10_isolation_heuristic_inclusive_boundary` | `1000 kΩ → 1_000_000 Ω` |
| `test_qa1_isolation_restore_not_called_in_async_setup` | timing correct |
| `test_qa1_isolation_restore_called_after_health_monitor_injection` | task scheduled |
| `test_qa2_migration_unit_mismatch_skips_rename` | corruption avoided |
| `test_qa2_migration_unit_match_performs_rename` | happy path unaffected |
| `test_qa4_total_increasing_returns_none_not_stale` | cumulative counters reset cleanly |
| `test_qa4_non_total_increasing_returns_stale_on_none` | measurement cache works |
| `test_qa5_last_result_contains_only_fresh_values` | no stale cascade |
| `test_qa6_record_failure_not_called_before_retry` | retry-success keeps interval |
| `test_qa6_record_failure_called_when_retry_also_fails` | total failure recorded |
| `test_qa6_clear_issue_called_on_retry_success` | repair notice cleared |
| `test_qa7_login_timeout_preserves_exception_chain` | login chain intact |
| `test_qa7_process_data_timeout_preserves_exception_chain` | poll chain intact |

Run with:

```bash
TZ=UTC python -m pytest tests/test_bug_regression.py --no-cov -v
```

All 22 tests pass on HA core `2024.3.3` against the pinned test environment.
