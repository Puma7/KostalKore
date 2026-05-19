# KOSTAL KORE — Consolidated Learnings (2026-03)

This file captures the most important implementation learnings from the recent
backlog execution wave and follow-up validation.

## 1) Network auto-discovery is possible, but best-effort

### What is true now
- Auto-discovery **can work** when the setup host field is empty.
- The setup flow probes likely local IPv4 candidates and then validates
  credentials against discovered hosts before accepting one.
- Discovery is therefore a convenience path, not a hard requirement.

### Why it can fail
- VLAN / subnet isolation
- firewall rules (port 80 probes or API access blocked)
- unusual network topology / routing
- inverter not reachable during setup window

### Decision / guidance
- Keep auto-discovery enabled as best-effort UX.
- Always keep manual host entry as first-class fallback.

---

## 2) Battery charge/discharge writes are Modbus-only by policy

### What we learned
In practice, REST battery charge/discharge controls are not consistently
reliable across firmware/device combinations. Accepting those writes in UI can
look successful while not producing deterministic inverter behavior.

### Implemented decision
- REST write paths for battery charge/discharge setpoints are intentionally
  blocked.
- Equivalent controls remain available via Modbus entities where behavior is
  deterministic and already safety-gated.

### Operational consequence
- If a user needs active charge/discharge power control, they must enable
  Modbus and use Modbus-backed entities.

---

## 3) High-impact write safety needs multiple guardrails

Single checks were not enough. Reliable safe behavior now requires:
- explicit write allowlist
- installer access validation
- temporary arming window for high-impact controls
- cross-field validation (e.g., off-threshold < on-threshold)
- read-after-write verification where readback API is available

### Important nuance
- Some test/legacy clients do not implement readback APIs.
- For those, strict verification paths must fail safely for high-impact writes
  and degrade compatibly for low-impact writes.

---

## 4) Event intelligence should be isolated from core polling

### Implemented pattern
- Events run in their own coordinator.
- Bounded ring buffer + dedup/cooldown avoids noise and memory growth.
- Rich event detail is diagnostics-only, not dumped into entity attributes.

### Outcome
- Actionable event context added without destabilizing normal sensor polling.

---

## 5) Capability and polling behavior should adapt at runtime

### Learnings applied
- Cache capability probes (settings/process maps) to reduce startup duplication.
- Use adaptive polling backoff on repeated communication failures with jitter.
- Persist Modbus unavailable-register knowledge to reduce repeated log spam.

### Outcome
- Better resilience under busy/error periods.
- Lower repeated probe overhead.

---

## 6) KSEM should be optional and isolated

### Implemented pattern
- KSEM runs behind its own coordinator and config options.
- Failure domain is separate from inverter REST/Modbus.
- Source precedence is explicit:
  1. KSEM (if healthy)
  2. inverter powermeter (Modbus)
  3. REST fallback
- Source conflict/confidence is exposed as sensor attributes.

---

## 7) Documentation must track behavioral policy, not only features

Most valuable documentation updates were policy-level:
- what discovery can/cannot guarantee
- which write paths are intentionally disabled
- which controls are Modbus-only by design
- which safeguards are mandatory

This prevents future regressions where convenience reintroduces unsafe behavior.

---

## 8) Legacy migration is safest as a two-layer workflow

### Recommended operational order
1. **Adopt legacy entity IDs** (registry-only, safe/default path).
2. **Copy/merge recorder history** only for remaining unmatched pairs.

### Why this order matters
- Recorder history in Home Assistant is keyed through metadata/entity mappings.
- If canonical IDs can be preserved first, most historical continuity is retained
  without direct recorder DB changes.
- DB-level history merge should be a second step, not the default.

### Implemented safeguards
- Both migration services support `dry_run` previews.
- Apply mode requires multi-step confirmation:
  - challenge code generation,
  - code verification,
  - explicit final confirmation call.

---

## 9) Recorder history migration should update metadata, not raw row copies

### Implemented approach
- Merge at metadata layer (`states_meta`, `statistics_meta`) and move linked
  rows (`states`, `statistics`, `statistics_short_term`) with dedupe on
  conflicting timestamps/start buckets.

### Why this matters
- Avoids fragile row-by-row blind copies.
- Reduces duplicate-statistics artifacts and keeps recorder references coherent.
- Works across SQLite and MariaDB/MySQL (and PostgreSQL support path included).

---

## 10) Grid Feed-In Optimizer is a limiter, not PV curtailment

### Behavior
- Optimizer adjusts battery charge limit via Modbus register `1038`
  (`bat_max_charge_limit`) so PV surplus above configured grid feed-in cap is
  redirected to battery charging.
- When disabled, normal charge limits are restored.

### Important integration rule
- Register `1038` can be written by several control features.
- Running multiple concurrent writers (optimizer, external automations, manual
  scripts) can cause control contention. One owner per control period is safer.

---

## 11) `Isolation Resistance = unknown` is typically a data-source symptom

### What it means
- Isolation resistance is read from Modbus register `120`.
- `unknown` usually indicates:
  - no successful read after startup yet,
  - transient Modbus disconnects,
  - unsupported register on specific firmware/model (illegal address path).

### Operator guidance
- Verify Modbus connectivity first (host/port/unit-id and inverter setting).
- Check logs for `Connection lost reading isolation_resistance` or
  `Illegal data address`.

---

## 12) Modbus proxy error codes must match failure semantics

### What we learned
Returning Modbus exception **0x02** (Illegal Data Address) for transient
forwarding failures (backend disconnect, timeout) causes well-behaved clients
to permanently remove the register from their polling list. The register is
valid — the backend is temporarily unavailable.

### Implemented decision
- Forward-to-inverter failures now return **0x04** (Server Device Failure).
- 0x02 is reserved for truly unknown/unsupported register addresses.
- Unit-ID mismatches return **0x0B** (Gateway Target Device Failed to Respond).

### Consequence
External clients (evcc, SolarAssistant) will retry on 0x04 instead of
blacklisting valid registers.

---

## 13) FC16 frame validation must be strict at protocol boundary

### What we learned
The Modbus TCP proxy is a protocol boundary — external clients send raw frames.
Trusting `byte_count` from the frame without cross-checking `quantity * 2`
allows inconsistent payloads to reach decode/forward paths.

### Implemented decision
- Validate `quantity` range (1–123 per Modbus spec).
- Enforce `byte_count == quantity * 2` before processing.
- Reject early with 0x03 (Illegal Data Value) on mismatch.

---

## 14) Platform-forwarding failure needs explicit rollback

### What we learned
`async_forward_entry_setups()` can fail after the integration has already
started long-lived runtime objects (Modbus proxy, MQTT bridge, SoC controller,
authenticated API session). HA does **not** call `async_unload_entry()` on
setup failure — only on explicit unload/reload.

### Implemented decision
- Added `_rollback_setup()` that mirrors the cleanup pattern from
  `async_unload_entry()` but runs in the setup-failure path.
- Cleans up: SoC controller, Modbus proxy, MQTT bridge, plenticore session,
  and `hass.data` entry.

---

## 15) Config flow discovery must not spray credentials

### What we learned
The original `_probe_tcp_port()` in the discovery flow attempted a raw TCP
connect to every candidate IP. Replacing it with `_probe_kostal_api()` using
the unauthenticated `/api/v1/info/version` endpoint confirms the target is
actually a Kostal inverter without sending credentials to arbitrary hosts.

### Security consequence
- Credentials are only sent to confirmed Kostal devices.
- Discovery still works on non-standard ports if the API endpoint responds.

---

## 16) NaN/Inf from inverter firmware must be filtered at format boundary

### What we learned
Some firmware versions or transient Modbus read errors can produce `NaN` or
`Inf` float values. Propagating these to HA entity state causes downstream
issues (statistics, graphs, automations with numeric comparisons).

### Implemented decision
- `format_float()` and `format_energy()` return `None` for `NaN`/`Inf`.
- This makes the entity state `unknown` in HA, which is the correct semantic.

---

## 17) Legacy migration device identifiers are not future-proof

### What we learned
The current migration uses `add_config_entry_id` to link the new `kostal_kore`
entry to a device whose identifiers are still `("kostal_plenticore", serial)`.
Home Assistant announced (July 2025) that cross-domain device linking will
stop working in Core **2026.8** when device identifiers become domain-scoped.

### Current status
- Migration works today (HA 2025.x / early 2026.x).
- After HA 2026.8, the device side of the migration will silently fail.
- Unique-ID rewriting only covers `entry_id`-prefixed patterns.

### Planned remediation
See `MIGRATION_ARCHITECTURE.md` for the full plan:
1. Rewrite device identifiers during migration.
2. Make migration transactional (snapshot → migrate → rollback on failure).
3. Handle all known unique-ID naming patterns.

---

## 18) Codex static analysis: validation workflow

### Process that worked well
1. Receive Codex findings as a batch (typically 4 per file group).
2. Read the actual code at the cited lines.
3. Validate each finding against real code — reject false positives immediately.
4. Implement only confirmed-valid fixes.
5. Run tests, commit, push.

### False positive patterns observed
- Findings about missing error handling where try/except already existed.
- Findings about missing cleanup where the cleanup was added in a prior session.
- Findings about race conditions that are impossible in asyncio single-threaded context.
- Duplicate findings that describe the same root cause from different angles.

### Rejection rate
~30% of findings were invalid (already fixed, false positive, or not applicable).
Always validate before implementing.

---

## 19) Ghost store resurrection after async_unload_entry

### What we learned
`integration_entry_store()` used `hass.data.setdefault(DOMAIN, {}).setdefault(entry_id, {})`
which re-creates the per-entry store if a lingering callback fires after
`async_unload_entry` has already popped the entry from `hass.data`.

### Implemented decision
- Return a detached empty `{}` when the entry is not (or no longer) in
  `hass.data[DOMAIN]`. Writes go into a throwaway dict — harmless no-op.
- The real store is created in `__init__.py:async_setup_entry()` via
  `hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {...}`, not by the
  helper function.

### Lesson
Convenience wrappers that silently create state on first access are dangerous
in lifecycle-aware systems. Prefer explicit creation during setup and
read-only access everywhere else.

---

## 20) Multi-string fire safety learning needs a stable metric

### What we learned
The original DC string ratio learning used `min(powers) / max(powers)` which
works well for exactly 2 strings but becomes unstable with 3+ strings. Any
change to the weakest or strongest string shifts the ratio dramatically,
even during normal operation (e.g. cloud passing over one orientation).

### Implemented decision
- Replaced with deviation-from-equal-share: `max(|p/total - 1/N|)` for all
  active strings. This metric is symmetric and stable regardless of string
  count.
- `_is_stable_ratio()` now checks the jitter (max absolute deviation from
  mean) of this metric over 30 minutes, with a 0.10 threshold.

### Design principle
When generalizing a 2-element algorithm to N elements, verify that the
mathematical properties still hold. Pairwise metrics don't always scale.

---

## 21) Notification IDs must be entry-scoped for multi-device setups

### What we learned
Global notification IDs like `kostal_charge_block` cause silent overwrites
when two inverters exist. Inverter B's notification replaces Inverter A's,
and dismissing one dismisses both.

### Implemented decision
- All persistent notification IDs now include `{entry_id}` suffix.
- Dismiss calls are added to turn-off / entity-removal paths to prevent
  orphaned notifications.
- This is a minor breaking change for users with automations matching on
  the old notification IDs.

### Lesson
Any identifier that HA uses to deduplicate or address user-facing state
must be scoped to the config entry, not global to the integration domain.

---

## 22) Self-review catches regressions that forward-only review misses

### What we learned
After implementing ~10 fixes in one session, switching to a "Senior QA"
adversarial review of our own diffs caught:
- A no-op fix (helper.py `setdefault` → `get` that still fell through to
  `setdefault`).
- A metric that was mathematically wrong for 3-string systems.
- A missing dismiss in an entity-removal path.
- Inconsistent constants across two files.

### Process recommendation
After a batch of fixes, explicitly diff all changes and review them
assuming they are wrong. This second pass is cheap and consistently
finds issues that the implementation mindset overlooks.

---

## 23) Verify unit assumptions against live hardware before "fixing" them

### What we learned
The static bug analysis identified both `FullChargeCap_E` and `WorkCapacity`
on `devices:local:battery` as wrongly labeled `Ah` (should be `Wh`). The
analysis relied on:
- the `_E` suffix suggesting "Energy",
- the Modbus register `1068` documented as `Wh`,
- internal test fixtures using values around 35000.

Verification against a real inverter (INSTALLER role, bulk REST query)
showed the two keys behave differently:

| Key              | Live value | Unit | Reasoning                                     |
|------------------|-----------:|------|-----------------------------------------------|
| `WorkCapacity`   | 35700      | Wh   | 35.7 kWh fits a 7-module BYD pack at 94 % SoC |
| `FullChargeCap_E`| 50         | Ah   | 50 Ah × ~760 V ≈ 38 kWh matches the SoC math  |

35700 Ah at 743 V would be 26.5 MWh — physically impossible. Conversely
50 Wh would be nonsensically small.

### Lesson
- Plenticore key names are not a reliable source of units. The `_E` suffix
  is misleading on `FullChargeCap_E`.
- For any unit change that affects HA long-term statistics, require a live
  measurement plus a physics plausibility check (P = U × I, SoC × capacity,
  module count × per-module Wh) before committing.
- Internal test fixtures can reinforce a wrong assumption — they are not
  evidence about the real device.

### Process recommendation
When in doubt, write a small diagnostic script that authenticates against
a live inverter and dumps the raw values. A 10-line script costs less than
shipping a wrong unit fix to user installations.

---

## 24) Plenticore REST: individual process-value queries return HTTP 500

### What we observed
On a real Plenticore device (INSTALLER role), POSTs to `/api/v1/processdata`
with a single `processdataIds` entry return **500 Internal Server Error** for
many keys that exist in the module's key listing — even harmless string
keys like `BatManufacturer`. The bulk query (no `processdataIds` filter)
returns all values successfully in the same session.

```
Test 1: Massenabfrage ohne Key-Filter
  ✅ 13 Werte erhalten (BatManufacturer, FullChargeCap_E, WorkCapacity, ...)

Test 2: Einzelabfrage harmloser String-Keys
  BatManufacturer: ❌ API Error: Unknown API response [500]
```

### Implication
- This is a Plenticore firmware behavior, not a client bug.
- `pykoplenti`'s use of bulk queries for the polling path is the right
  pattern — single-key fetches must not be assumed reliable.
- Any future code that wants "just one value" should still bulk-fetch and
  index in the client.

### Lesson
Don't infer a key is unsupported because the single-key endpoint returns
500. Confirm via the bulk endpoint, which is the only reliable path on
this firmware.

---

## 25) Translation parity: `strings.json` and `en.json` drift silently

### What we learned
`strings.json` (the canonical translation source) contained 4 KSEM option
keys plus a `reauth_confirm.description` that were never copied into
`translations/en.json`. HA silently renders empty labels for missing
English translations — there is no build-time check.

### Implemented decision
- `en.json` is now kept in lockstep with `strings.json` (KSEM options in
  both `options.step.init.data` and `config.step.setup_options.data`,
  reauth description, and the `entity.button.reset_modbus_registers`
  block).
- The `ModbusResetButton` was switched from `_attr_name = "..."` to
  `_attr_translation_key = "reset_modbus_registers"` so the translation
  is actually used.

### Process recommendation
Whenever `strings.json` gains a key, do a diff against `translations/en.json`
in the same commit. Treat the parity check as part of "is this PR ready",
not as a follow-up.

---

## 26) Field-level error handling beats module-level for partial responses

### What we learned
The coordinator's REST response processing used a dict comprehension
inside a single try/except per module:

```python
result[module_id] = {
    pid: str(module_data[pid].value) for pid in module_data.keys()
}
```

If a single field lacked `.value` (or raised on access), the entire
comprehension failed and the `except` block set `result[module_id] = {}`.
A single bad field made 10–15 sensors of that module go `unavailable`.

### Implemented decision
- Iterate fields explicitly, catching per-field.
- Collect failed field names into a list and emit **one** aggregated warning
  per module per cycle instead of one warning per failing field (which
  would otherwise produce dozens of log lines per polling cycle).

### Lesson
Fault isolation granularity should match the smallest unit of data the
user cares about. For sensor data, that unit is the field, not the module.
And log aggregation matters — per-field warnings under load are worse
than no warning at all because they hide everything else.

---

## 27) Be conservative when tightening or loosening safety heuristics

### What we learned
The isolation resistance normalizer used `0 < |x| < 1000` to decide whether
a value should be interpreted as kΩ (and multiplied by 1000 to get Ω).
Bug analysis flagged the strict `<` as a "boundary bug": a value of exactly
1000 would skip the kΩ-to-Ω conversion and trip the critical alarm.

### Why we did NOT change it
- A reading of exactly `1000` is genuinely ambiguous: it could be 1000 kΩ
  (= 1 MΩ, healthy) or 1000 Ω (= 1 kΩ, critical fault).
- Changing `<` to `<=` would silently re-interpret a real 1000 Ω fault
  as a healthy reading.
- The "false alarm at exactly 1000" failure mode (user sees a critical
  notification, investigates, finds nothing) is recoverable. The
  "missed fault at exactly 1000" failure mode is not.

### Lesson
On safety-critical heuristics, prefer the failure mode that is loud and
recoverable over the failure mode that is silent and dangerous. Don't
"fix" a boundary case without checking which side of the boundary is
actually safer.

---

## 28) Range-based loops over coordinator data lose self-healing

### What we learned
A first attempt to fix `CalculatedPvSumSensor` replaced the
`for module_id in self.coordinator.data` scan with
`for i in range(1, dc_string_count + 1)`. This passed unit tests but
broke a subtle self-healing property: if a PV string came online *after*
init (e.g. firmware re-detected it, or the discovery race finished late),
the coordinator-data scan would pick it up automatically while the
range loop never would.

### Implemented decision
- Revert to scanning `coordinator.data` for module IDs starting with
  `MODULE_ID_PREFIX`.
- Use the `MODULE_ID_PREFIX` constant everywhere (no more hardcoded
  `"devices:local:pv"` literals).
- Keep `dc_string_count` only for explicit fetch registration in
  `async_added_to_hass`, where being exact matters.

### Lesson
Coordinator-data scans are dynamic by nature. Replacing them with
range loops feels cleaner but trades adaptive behavior for a static
view of the world. Choose which property you actually want before
"tidying up" the loop.

---

## 29) Cap upper bounds but let invalid lower values fall through

### What we learned
A first attempt to clamp the Modbus-discovered DC string count used
`max(1, min(raw, MAX_SANE_STRING_COUNT))`. This was over-aggressive:
when Modbus returned `0` (register not yet populated, or genuinely
unknown), the clamp mapped it to `1`, which then satisfied the
`if dc_string_count >= 1:` guard and **bypassed the REST API fallback**.
The integration silently started with a wrong, default string count
instead of consulting the better source.

### Implemented decision
- Cap the upper bound only: `if raw > MAX_SANE_STRING_COUNT: ...` warn
  and clamp to the max.
- Let `raw <= 0` flow through, so the REST API fallback path runs.

### Lesson
Clamps are not the right tool when the lower bound represents
"missing information". Map "missing" to a state that triggers the
fallback chain, not to a valid-looking default.

---

## 30) Three-stage review (Developer → QA → Red Team) catches what self-review misses

### What we learned
A single self-review pass after a fix tends to confirm what the
implementer already believes. Layering three explicit roles caught
real defects at each stage:

| Stage | Role | Caught |
|-------|------|--------|
| 1 | Developer (implement) | the original 11 bugs |
| 2 | QA (adversarial self-review) | a too-aggressive clamp, an over-loud log path, a missing diagnostic notification |
| 3 | Red Team (independent skeptic) | a wrong entity-registry field name that would have made the QA-added fix silently no-op for the entire target cohort; an unverifiable claim baked into a code comment |

The Red Team stage is the most valuable when it explicitly assumes
the prior two stages may have hallucinated, instead of building on
their conclusions.

### Process recommendation
For non-trivial fixes:
1. Implement.
2. Switch role — review your own diff with a "this is broken until
   proven otherwise" stance.
3. Switch role again — challenge the QA findings themselves. Ask:
   "is the bug even real, or did the QA invent a failure mode that
   the original code never actually had?"

Each stage must explicitly distrust the previous one. Without that,
later stages just rubber-stamp earlier ones.

---

## 31) HA entity_registry has two unit fields — pick the right one

### What we learned
`RegistryEntry` exposes two unit-of-measurement attributes that look
interchangeable but are not:

| Field | Meaning | Default for users without UI override |
|-------|---------|---------------------------------------|
| `unit_of_measurement` | the user's UI override | often `None` |
| `original_unit_of_measurement` | the entity-reported unit at last registration | the actual current unit |

A migration check written as `entry.unit_of_measurement == "Ah"`
silently misses every user who never opened the UI cog and adjusted
the unit — exactly the population the migration notice is meant for.

### Implemented decision
- Read the effective unit as
  `entry.unit_of_measurement or entry.original_unit_of_measurement`.
- Treat `unit_of_measurement` as override-only.

### Lesson
When an HA registry attribute has both an `xxx` and an
`original_xxx` variant, assume they hold different things and verify
which one represents the "current effective" value before relying
on it.

---

## 32) `suggested_unit_of_measurement` does not retroactively re-format existing entities

### What we learned
Setting `suggested_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR`
in the entity description applies the kWh display only when the
entity is **registered for the first time**. For entities already
present in the registry (every existing install), the registry's
stored `unit_of_measurement` keeps the previously persisted value
and the suggestion has no effect.

### Implemented decision
- Keep `suggested_unit_of_measurement` in the entity description for
  new installs.
- Document in the migration Repairs issue that existing users must
  manually change the unit through the entity settings cog if they
  want the kWh display.

### Lesson
HA's `suggested_*` attributes are first-registration hints, not
runtime preferences. Any breaking unit change must surface a
manual-action notice for existing entities, because the registry
will hold the old value indefinitely otherwise.

---

## 33) Don't bake unverified architectural claims into code comments

### What we learned
A QA finding claimed `EntityCategory.DIAGNOSTIC` filters entities
out of the Home Assistant Energy Dashboard's auto-suggestion picker
and therefore neutralizes `device_class=ENERGY_STORAGE`. The Red
Team pass could not confirm that claim from HA's published behavior
— the actual Energy Dashboard filter is on `device_class`,
`state_class`, and `hidden_by`, not on `entity_category`.

The fix (removing DIAGNOSTIC) was still correct, but for a different
reason: it was a **consistency** violation against the sibling
battery sensors `SoC`, `P`, `U` in the same definition, which are
user-facing and carry no DIAGNOSTIC.

### Implemented decision
- Removed the unverifiable claim from the inline comment.
- Replaced with the verifiable consistency argument.

### Lesson
Code comments that justify a change with an unverified third-party
behavior claim are technical debt. If the claim later turns out to
be wrong, the comment misleads future maintainers about why the
code is the way it is. Prefer comments that cite facts visible in
the same repository (sibling code patterns, in-repo conventions).

---

## 34) Custom Repairs issues can be redundant with HA's built-in validators

### What we learned
Home Assistant's recorder includes a `validate_statistics` pass that
automatically surfaces unit-of-measurement mismatches between the
current entity and stored statistics. The user already gets a
"Fix Issue" button in *Developer Tools → Statistics* — without the
integration doing anything.

A custom Repairs issue in the Repairs panel adds value only if it
provides instructions or context that HA's built-in validator does
not — for example, telling the user that an additional manual step
(display-unit override on the entity) is needed, or explaining
*why* the unit changed.

### Implemented decision
- Keep our custom Repairs issue, but use it to **augment** HA's
  built-in mechanism, not duplicate it: the description lists both
  the Developer-Tools-Statistics step and the entity-settings-cog
  step, and explains the root cause (Ah/Wh confusion).
- Auto-clear the issue once the registry reflects the new unit.

### Lesson
Before adding a Repairs issue for a known HA-handled scenario,
check what HA already exposes natively. If our addition does not
carry new actionable information, drop it. If it does, keep it but
make the marginal value explicit in the description.

---

## 35) Diagnostic / one-off scripts shouldn't take credentials as positional CLI args

### What we learned
The diagnostic script `check_inverter_api.py` originally took the
inverter password (or installer master-key + service-code) as
positional `sys.argv` entries. That puts the secret into
`~/.bash_history`, PowerShell transcript logs, `ps aux` output,
and any container or CI log that captures process invocations.

The script was committed to the repository root, so the unsafe
pattern was visible to anyone reading the repo and could be
copy-pasted into other operational scripts.

### Implemented decision
- Default to `getpass.getpass()` for credential entry, falling back
  to positional args only for backwards compatibility.
- Document the trade-off in the script's docstring.
- Use `--installer` as a mode flag instead of relying on argument
  count alone, so the master-mode is explicit.

### Lesson
Even short-lived diagnostic scripts deserve secure-by-default input
handling once they're committed to a repository. Anything that
lives in the repo gets imitated; make the safe path the obvious
one.

---

## 36) Multi-pass AI bug audits can hallucinate bugs and then hallucinate fixes

### What happened
A structured omniscient audit (Round 1) identified `FullChargeCap_E` as having
the wrong unit: "Ah should be Wh because _E = Energy". This reasoning sounded
plausible. The same audit also found `modbus_registers.py` reporting Wh for the
equivalent Modbus register (1070) — and used that as corroborating evidence.

Round 2 wrote the fix (changed `"Ah"` → `UnitOfEnergy.WATT_HOUR`, added
`device_class=ENERGY_STORAGE`, `suggested_unit_of_measurement=kWh`). Round 2
also renamed the protective regression test from `test_bug1_full_charge_cap_unit_is_ah`
to `test_bug1_full_charge_cap_unit_is_wh` — destroying the guard that existed
precisely to prevent this mistake.

A subsequent independent Red Team audit (Round 3) cross-checked `LEARNINGS.md`
section 23, which documents real hardware measurement: the register returns `50`,
and 50 Ah × ~760 V ≈ 38 kWh (physically correct). 50 Wh would be absurdly
small for a home battery. `docs/BUGFIX_LOG.md` also explicitly warned:
"regression test documents this as the expected baseline so a future 'fix'
doesn't silently flip it."

### Why the hallucination was convincing
1. The `_E` suffix pattern (Energy) was applied mechanically without checking
   actual firmware output.
2. The Modbus register (`WorkCapacity` Modbus, 1070) genuinely is in Wh — but
   the REST-API register `FullChargeCap_E` is a different register reporting a
   different physical quantity (charge, not energy).
3. Internal test fixtures and docstrings used "Wh" language for the battery
   system generically, which was read as evidence without distinguishing which
   register was being described.

### The protective test was destroyed by the same session that introduced the bug
The most dangerous moment: Round 2 renamed `test_bug1_full_charge_cap_unit_is_ah`
(a red-flag canary) to match the new (wrong) assertion. After that rename, the
test suite stayed green while the code was wrong. A canary test that is renamed
in the same commit as the behavior change provides no protection.

### Lessons
- **Naming conventions are not units.** Always cross-reference against live
  hardware data or confirmed API documentation, not naming patterns.
- **Regression tests that document "current known-good state" must never be
  renamed by the same session that changes the behavior.** They exist exactly to
  survive well-intentioned but wrong fixes. If a test name contradicts your new
  code, treat that as a warning signal, not a test to rename.
- **Multi-pass AI audits compound errors.** An auditor that wrote the original
  fix and then reviews the fix will rationalise it. Independent review (Red Team
  mode) that re-reads primary evidence (real hardware logs, `LEARNINGS.md`,
  `BUGFIX_LOG.md`) is the only reliable check.
- **Physics plausibility check is mandatory for any unit change** that affects
  HA long-term statistics: P = U × I, E = Q × U, SoC × capacity, module count
  × per-module spec. A 10-second sanity check costs far less than shipping a
  wrong unit to user installations.

---

## 37) Omniscient audits must be followed by an independent Red Team pass

### What happened
After a 12-bug audit and fix session, a structured 3-stage Red Team audit
(Reality Check → Blast Radius → Edge-Case Simulation) was conducted. It caught
the Fix #1 hallucination (see section 36) that had survived two full review
passes.

### The 3-stage Red Team process that worked
**Stage 1 — Reality Check:** For each fix, locate the *primary source* (real
hardware logs, official docs, API responses) and verify the assumption. Do not
trust the audit's own reasoning as evidence.

**Stage 2 — Blast Radius:** For each fix, ask: "What is the worst case if this
fix is wrong?" For a unit change on a `TOTAL_INCREASING` sensor, the answer is
"all historical data corrupted in HA long-term statistics" — a high-severity
blast. That severity justifies extra scrutiny.

**Stage 3 — Edge-Case Simulation:** Trace what happens with boundary inputs
(zero, negative, very large, NaN, None). Confirm the fix handles them correctly
and that the test suite actually exercises those paths.

### Why this order matters
Stage 1 failing (wrong assumption) makes Stages 2 and 3 irrelevant — no amount
of edge-case correctness saves a fix built on a false premise. Do Stage 1 first.

### Lesson
Schedule a Red Team pass as a mandatory step after any AI-generated batch of
fixes, especially for fixes that:
- Change units of measurement
- Rename or delete existing tests
- Touch long-term statistics storage
- Involve physical quantities (voltage, current, energy, charge)

The Red Team auditor must have read-only access to primary evidence (hardware
logs, official firmware docs) and must be instructed to *challenge* the fixes,
not validate them.

---

## 38) `RegistryEntry.unit_of_measurement` is the correct attribute for persisted unit checks

### What happened
`__init__.py` migration check used `_entry_reg.unit_of_measurement or _entry_reg.original_unit_of_measurement` to read the effective unit from the entity registry. mypy (strict mode) reported `"RegistryEntry" has no attribute "original_unit_of_measurement"` — and it was correct. The `original_unit_of_measurement` attribute does not exist on the HA version range this integration targets.

### What is true
`RegistryEntry.unit_of_measurement` holds the effective persisted unit: it is the user override if one was set, or the unit provided at first entity registration otherwise. There is no separate `original_unit_of_measurement` field to fall back to.

### Lesson
When checking what unit an entity currently reports in the entity registry, `entry.unit_of_measurement` is sufficient and correct. Do not add a fallback to `original_unit_of_measurement` — it does not exist and the fallback logic would silently be dead code at best, an `AttributeError` at worst.

---

## 39) Isolate entity registry pre-fill from HA platform setup when testing migration checks

### What happened
A test pre-filled the entity registry with `unit_of_measurement="Wh"` and then ran a full `config_entries.async_setup`. The migration check (which runs before `async_forward_entry_setups`) was expected to see "Wh" and call `clear_issue`. Instead it saw a different unit, causing the wrong branch to execute.

### Root cause
During the full setup path, HA's platform machinery calls `async_get_or_create` for entities as it discovers them. This updates internal registry fields. Even though the migration check runs *before* `async_forward_entry_setups`, something in `Plenticore.async_setup()` (the real client) touched the registry between pre-fill and the migration check.

### Fix
Use a `_DummyPlenticore` stub + call `async_setup_entry` directly instead of going through `hass.config_entries.async_setup`. Patch `async_forward_entry_setups` to `AsyncMock(return_value=True)` to skip platform setup entirely. Nothing touches the entity registry during `DummyPlenticore.async_setup()`, so the pre-fill state is stable at migration-check time.

### Pattern
```python
with patch("custom_components.kostal_kore.__init__.Plenticore", _DummyPlenticore), \
     patch("homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
           AsyncMock(return_value=True)):
    assert await kp_init.async_setup_entry(hass, mock_config_entry) is True
```

---

## 40) Use a list (not a set) when iterating identifiers in branch-coverage tests

### What happened
`__init__.py` iterates `device_info["identifiers"]` to find the first tuple where `domain == DOMAIN`. To cover the "loop continues past a non-matching identifier" branch, the test needs to guarantee that a non-matching tuple comes *first*. Sets have no guaranteed iteration order, so a `set` of two tuples might be tested in either order depending on CPython's hash randomisation.

### Fix
Pass `identifiers` as a `list` in tests where iteration order matters:
```python
identifiers = [("other_domain", "ignored"), (DOMAIN, "SN-99999")]
```
`DeviceInfo` stores identifiers in a `TypedDict` with no runtime type enforcement, so a list is accepted. The for loop in `__init__.py` iterates any iterable, making this safe at runtime.

### Lesson
When writing branch-coverage tests for code that loops over a set, replace the set with an ordered iterable (list or tuple) so the test deterministically exercises the intended path. This is intentional duck-typing — keep a comment explaining why.

---

## 41) `BatterySocController` runs even inside `# pragma: no cover` blocks

### What happened
`__init__.py` contains:
```python
if modbus_coordinator is not None:  # pragma: no cover
    ...
    soc_controller = BatterySocController(...)
```
The `# pragma: no cover` tells the *coverage tool* to skip this block. It does not prevent the block from *executing* at runtime. Tests using `_DummyPlenticore` that also mock `ModbusDataUpdateCoordinator` will trigger this block, causing the real `BatterySocController` constructor to receive a `MagicMock` coordinator.

### Fix
Always patch `BatterySocController` at the source module level in any test that uses `_DummyPlenticore` with a mocked Modbus coordinator:
```python
patch("custom_components.kostal_kore.battery_soc_controller.BatterySocController",
      return_value=mock_soc)
```

### Lesson
`# pragma: no cover` is a coverage reporting directive, not a runtime guard. Treat any code inside a `pragma: no cover` block as executable — it will run in tests unless the relevant conditions are also mocked out.

---

## 42) `_restore_isolation_sample` must be `AsyncMock` when mocking the Modbus coordinator

### What happened
`__init__.py` does `await modbus_coordinator._restore_isolation_sample()` during setup. A plain `MagicMock()` attribute is not awaitable; awaiting it raises `TypeError: object MagicMock can't be used in 'await' expression`.

### Fix
When constructing a mock Modbus coordinator for tests, set all awaited methods explicitly:
```python
mock_modbus_coord = MagicMock()
mock_modbus_coord.async_setup = AsyncMock()
mock_modbus_coord.async_shutdown = AsyncMock()
mock_modbus_coord._restore_isolation_sample = AsyncMock()
```

### Lesson
`MagicMock` auto-creates child attributes as `MagicMock` instances, which are not awaitable. When testing async code that `await`s a method on a mock, always set that method to `AsyncMock()` explicitly — or use `AsyncMock` as the top-level mock class if all methods are async.
