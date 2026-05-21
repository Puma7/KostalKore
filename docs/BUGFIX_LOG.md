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

`FullChargeCap_E` is **correctly** `"Ah"` and must stay that way. Live
hardware measurement (LEARNINGS §23) returns ~50 for this register;
50 Ah × ~760 V ≈ 38 kWh which matches the SoC math. 50 Wh would be
physically absurd (~0.005 % of a home battery's capacity). The `_E` suffix
is misleading — it does NOT mean Energy.

**False fix and revert history:**

| Commit | What happened |
|--------|---------------|
| (Round 1 audit, May 2025) | Hallucinated "Ah → Wh" fix; renamed canary test to `_is_wh`. |
| `6bf7680` (May 2025) | Reverted to Ah after Red Team caught the hallucination; restored canary test. |
| `2973895` (May 2026) | **Same hallucination repeated.** Audit cited `modbus_registers.py:163` Wh and `35000.0` fixture as "evidence". |
| `<next commit>` (May 2026) | Reverted again. Canary test name now includes explicit do-not-rename docstring with cross-refs to §23/§36/§37/§47. |

**Why the loop keeps repeating:**

1. The `_E` suffix pattern (Energy) reads as evidence to a code-analysis
   audit that hasn't consulted live hardware.
2. The Modbus register `1068`/`1070` (`battery_work_capacity`) really is in
   Wh — but that's a different register than the REST API
   `devices:local:battery/FullChargeCap_E`.
3. Internal docstrings and `health_monitor.py`/`degradation_tracker.py`
   refer to "Battery Capacity" generically; the audit reads them as
   describing `FullChargeCap_E` without distinguishing register sources.

**The only protections that work:**

- `test_bug1_full_charge_cap_unit_is_ah` asserts Ah AND `device_class is None`.
  The test name and docstring explicitly forbid renaming or flipping in the
  same session that changes the sensor description.
- Live hardware diagnostic script (LEARNINGS §23 "Process recommendation").
- A Red Team audit pass that re-reads primary evidence (LEARNINGS) before
  trusting a code-analysis bug report (LEARNINGS §37).

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
(1–6). The energy statistics (`Statistic:EnergyPv1:Day`, …) were statically
defined for PV1, PV2 and PV3 only. Inverters with one string saw PV2/PV3
energy sensors permanently `unavailable`; inverters with >3 strings lost
energy stats for PV4–6.

**Fix (commit `2973895`):** New helper `generate_pv_energy_sensor_descriptions(count)`
in `sensor.py` mirrors `generate_dc_sensor_descriptions` and produces one
`SensorDescription` per `(pv_num, period)` combination for
`period ∈ {Day, Month, Year, Total}`. Static PV1–PV3 blocks removed from
`SENSOR_PROCESS_DATA`. The dynamic descriptions go through the existing
`available_process_data`-aware filter in `create_entities_batch`, so PV4–6
sensors materialize only when the API actually exposes them — no phantom
`unavailable` entities on smaller inverters.

**Migration concern (negative):** No entity-registry migration is required.
The previous static sensors used the same translation keys derived from
`name=`; the dynamic generator preserves that exact naming, so existing
1-string installations don't get *new* registry entries — the dropped
`Energy PV2/PV3 *` entities simply disappear from new installs and are
already `unavailable` (and likely never recorded any data) on existing ones.

**Tests:** `test_bug11_pv_energy_sensors_generated_dynamically` (count=1/3/6
yield exactly 4/12/24 sensors with correct unit + device_class) and
`test_bug11_static_pv_energy_descriptions_removed` (no `Statistic:EnergyPv*`
leftover in the static description list).

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
| `custom_components/kostal_kore/sensor.py` | #1, #6, #8, #9, #11, QA-4 |
| `custom_components/kostal_kore/orphan_history.py` | Orphan-history MVP (new) |
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
| `test_bug1_full_charge_cap_unit_is_ah` | **PROTECTIVE CANARY** — `FullChargeCap_E` is Ah, no `device_class`. Do not rename. |
| `test_bug11_pv_energy_sensors_generated_dynamically` | 1/3/6 strings → 4/12/24 sensors |
| `test_bug11_static_pv_energy_descriptions_removed` | no leftover static EnergyPv entries |
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

---

## Bugbot deep-scan fixes (2026-05-21)

Five additional bugs were found during an external Bugbot audit of commit `fa372a3` and fixed in the session that followed. All five have regression tests in `Tests/test_bug_regression.py`.

### Bugbot KRITISCH-1 🔴 — Modbus slow-poll cache missing

**File:** `custom_components/kostal_kore/modbus_coordinator.py`

`_async_update_data` started each tick with `data: dict[str, Any] = {}` and only merged slow-group results (ENERGY / CONTROL / BATTERY_MGMT / …) on every 6th tick. On the 5 intervening ticks the coordinator returned only fast-group keys, making all slow-group entities unavailable for ~25 of every 30 seconds.

**Fix:** Added `self._last_slow_data: dict[str, Any] = {}`. After a successful slow poll, the result is stored there. On every tick, `data.update(self._last_slow_data)` is applied before the slow-tick check so entities always see the last known slow values (updated every ~30 s, stale by at most 30 s — which is acceptable given the ENERGY/CONTROL nature of those registers).

**Test:** `test_audit_modbus_slow_poll_cache_preserves_slow_registers`

---

### Bugbot KRITISCH-2 🔴 — fire_safety stale-data detection used wrong register key

**File:** `custom_components/kostal_kore/fire_safety.py:167`

`analyze()` called `data.get("controller_temperature")` for the stale-data detection guard. The Modbus register key (from `modbus_registers.py:97`) is `"controller_temp"`. The wrong key always returned `None`, so the consecutive-empty-polls counter advanced even while valid controller temperature readings arrived — silently increasing the risk of a false "safety monitor is blind" warning while simultaneously preventing the counter from resetting.

`_check_controller_thermal()` and `_record_history()` already used the correct key `"controller_temp"` — only the stale-data branch was wrong.

**Fix:** Line 167 changed from `"controller_temperature"` to `"controller_temp"`.

**Test:** `test_audit_fire_safety_stale_data_uses_correct_controller_temp_key`

---

### Bugbot HOCH-1 🟠 — INVERTER_STATES labels wrong for states 18/19

**File:** `custom_components/kostal_kore/modbus_registers.py:350`

`INVERTER_STATES[18]` was `"Unknown"` and `INVERTER_STATES[19]` was `"DcCheck"`. The corresponding constants in `helper.py` are:
- `INVERTER_STATE_BATTERY_CHARGING = 18`
- `INVERTER_STATE_BATTERY_DISCHARGING = 19`

Users with an active battery saw the inverter state entity show "Unknown" while charging and "DcCheck" while discharging.

**Fix:** Labels corrected to `"BatteryCharging"` and `"BatteryDischarging"`.

**Test:** `test_audit_inverter_states_18_19_labels_match_helper_constants`

---

### Bugbot HOCH-2 🟠 — Grid Feed-In Limiter underestimated home consumption

**File:** `custom_components/kostal_kore/grid_charge_limiter.py:146`

`_control_loop` read only `home_from_pv` (the PV → house power share) as the home consumption estimate. During battery discharge or grid import, the rest of the actual home load was invisible, causing `available_for_grid` to be overestimated — allowing more feed-in than the configured cap.

The MQTT bridge had already received this exact fix (documented in the MQTT bridge changelog). The grid limiter was missed.

**Fix:** Control loop now reads all three registers and sums them:
```python
home = abs(home_from_pv or 0) + abs(home_from_battery or 0) + abs(home_from_grid or 0)
```

**Test:** `test_audit_grid_limiter_uses_full_home_consumption`

---

### Bugbot MITTEL-1 🟡 — migration_services duplicate_source path renamed old row anyway

**File:** `custom_components/kostal_kore/migration_services.py:430+`

QA-2 added `continue` for the unit-mismatch case inside `_merge_statistics_metadata`. A second skip path existed for `duplicate_sources` (target entity has multiple `StatisticsMeta` rows with the same source), which correctly excluded those sources from `new_by_source`. However `old_meta.statistic_id = new_entity_id` (the rename) was executed unconditionally outside the `if matching_new is not None` block, so even when `matching_new` was `None` due to the source being a duplicate, the old row was renamed — creating a third row with the same `(statistic_id, source)` combination and worsening the corruption the duplicate-source check was intended to protect against.

**Fix:** Added `if old_source in duplicate_sources: continue` before the `new_by_source.pop()`, skipping both merge and rename for duplicate sources.

**Test:** `test_audit_migration_duplicate_source_skips_rename`

---

### Test registry additions

| Test | Verifies |
|------|---------|
| `test_audit_modbus_slow_poll_cache_preserves_slow_registers` | slow-group values survive non-slow ticks |
| `test_audit_fire_safety_stale_data_uses_correct_controller_temp_key` | correct key for stale-data detection |
| `test_audit_inverter_states_18_19_labels_match_helper_constants` | display labels match helper constants |
| `test_audit_grid_limiter_uses_full_home_consumption` | all 3 home sources read and summed |
| `test_audit_migration_duplicate_source_skips_rename` | duplicate source → no rename |

---

## Capability addition — Orphan-History MVP (commit `bca1587`)

### Problem

Long-time KORE users whose Recorder DB still carries entity_ids from the
removed `kostal_plenticore` integration had no path to merge that history
into their current entities. The existing `import_legacy_plenticore` flow
needs the legacy config entry to still be loaded; users who removed it years
ago could not benefit. `copy_legacy_history` only works when its
`discover_legacy_duplicate_entity_pairs` finds matching Entity Registry
entries on both sides — which fails when the legacy registry rows are long
gone.

### Solution

New module `custom_components/kostal_kore/orphan_history.py` exposes two
services:

| Service | Behavior |
|---------|----------|
| `kostal_kore.scan_orphan_history` | Read-only. Scans `StatesMeta` + `StatisticsMeta` for entity_ids matching legacy patterns that no longer exist in the Entity Registry. Posts a persistent notification with fuzzy-match suggestions to current KORE entities. Never writes. |
| `kostal_kore.apply_orphan_history_mapping` | Dry-run default. Re-binds orphan rows to current KORE entities by delegating to the existing `_copy_legacy_history_sync` engine — the unit-mismatch and duplicate-source guards from QA-2 carry over. |

Mapping validation rejects entries whose target is not registered to the
`kostal_kore` platform, preventing accidental cross-integration pointers.
Fuzzy matching uses `difflib.get_close_matches` with a 0.72 cutoff on
suffix-normalized entity_ids (`sensor.kostal_plenticore_pv_power` →
`pv_power`; `sensor.kore_pv_power` → `pv_power` → suffix match ratio = 1.0).

### Why MVP and not a full wizard

The original migration improvement plan proposed a 4-phase build-out with a
device-page assistant card. Critical review found:

- HA has no clean API for custom cards on device pages from custom
  integrations — would require a Lovelace custom card (separate JS frontend).
- A 1-step "merge" modal is a foot-gun for power users with thousands of
  rows; the existing 3-step confirmation is bullet-proof on purpose.
- The biggest user-need-vs-tooling gap is exactly the orphan-scan path; it
  ships in one module, two services, and a documentation page.

The orphan-history MVP fills that gap without committing to a UI surface
that the platform doesn't really support.

### Tests

`Tests/test_orphan_history.py` covers pure helpers, scan with mocked
recorder session, dry-run safety (executor never called), apply path
reaching the copy engine, backend/recording guards, notification
formatters, and service registration idempotence.

### Documentation

User-facing walkthrough in `docs/migration_orphan_history.md` covers backup
→ scan → dry-run → apply.
