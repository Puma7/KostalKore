# Coverage Roadmap - From Reported 100% to Real 100%

Status: **Open**  
Created: **2026-03-28**  
Scope: `custom_components/kostal_kore/`

## Why this exists

The current test suite reports `100%` coverage, but that number is only true for
the subset of files currently measured by coverage.

As of 2026-03-28:
- `48` Python modules exist under `custom_components/kostal_kore/`
- `40` modules are excluded in [.coveragerc](./.coveragerc)
- only `8` modules are currently part of the enforced `--cov-fail-under=100` gate

This roadmap defines what "real 100%" means, what must change to get there, and
the order in which the work should be done.

---

## Definition of "real 100%"

For this repository, **real 100% coverage** means:

1. All production modules under `custom_components/kostal_kore/` are measured by
   coverage unless there is a documented, explicit exception.
2. Coverage remains `100%` with `branch = True`.
3. Critical negative paths are tested, not only happy paths:
   - timeouts
   - malformed payloads
   - unsupported capability responses
   - partial init / partial unload
   - permission denial / installer gating
   - concurrency and cleanup paths
4. The coverage number is not maintained by broad `omit` rules.

## Non-goals

- Chasing 100% for vendored third-party code under `pykoplenti-master/`
- Replacing integration tests with synthetic unit tests where behavior would no
  longer reflect Home Assistant runtime semantics
- Hitting 100% by adding meaningless assertions that do not verify behavior

---

## Current blockers

The main blockers are not just missing tests. Many excluded files are also hard
to test because they mix:

- runtime state from `hass.data`
- entity creation
- direct side effects
- notifications
- Modbus/REST error handling
- partial setup/unload logic

Some modules will need light refactoring before their hard-to-reach branches can
be tested cleanly and deterministically.

---

## Coverage strategy

The work should be done in four waves. Each wave removes a set of files from
coverage exclusions, adds tests, and then re-runs the suite under the stricter
coverage gate.

### Wave 1 - Low-risk infrastructure and entity glue

Goal: remove the easiest structural exclusions first and stabilize the test
pattern for later waves.

Target files:
- [custom_components/kostal_kore/binary_sensor.py](./custom_components/kostal_kore/binary_sensor.py)
- [custom_components/kostal_kore/button.py](./custom_components/kostal_kore/button.py)
- [custom_components/kostal_kore/text.py](./custom_components/kostal_kore/text.py)
- [custom_components/kostal_kore/notifications.py](./custom_components/kostal_kore/notifications.py)
- [custom_components/kostal_kore/power_limits.py](./custom_components/kostal_kore/power_limits.py)
- [custom_components/kostal_kore/diagnostics.py](./custom_components/kostal_kore/diagnostics.py)
- [custom_components/kostal_kore/request_scheduler.py](./custom_components/kostal_kore/request_scheduler.py)
- [custom_components/kostal_kore/scheduled_session.py](./custom_components/kostal_kore/scheduled_session.py)
- [custom_components/kostal_kore/repairs.py](./custom_components/kostal_kore/repairs.py)

Why first:
- smallest external blast radius
- mostly deterministic behavior
- good foundation for later platform and migration tests

Estimated effort:
- `2-4` days

Exit criteria:
- remove these files from `omit`
- add direct tests for setup, negative paths, and cleanup
- keep suite green with `--cov-fail-under=100`

### Wave 2 - Setup, diagnostics, and migration safety

Goal: cover the files with the highest user-facing setup and repair impact.

Target files:
- [custom_components/kostal_kore/config_flow.py](./custom_components/kostal_kore/config_flow.py)
- [custom_components/kostal_kore/legacy_migration.py](./custom_components/kostal_kore/legacy_migration.py)
- [custom_components/kostal_kore/migration_services.py](./custom_components/kostal_kore/migration_services.py)
- [custom_components/kostal_kore/system_health_check.py](./custom_components/kostal_kore/system_health_check.py)
- [custom_components/kostal_kore/diagnostics_engine.py](./custom_components/kostal_kore/diagnostics_engine.py)
- [custom_components/kostal_kore/diagnostic_entities.py](./custom_components/kostal_kore/diagnostic_entities.py)

Why second:
- many previously reviewed P1/P2 issues live here
- configuration and migration failures are high-cost regressions
- these modules benefit from the notification and scheduler test helpers built in Wave 1

Estimated effort:
- `4-6` days

Exit criteria:
- host resolution, timeout, and reauth paths covered
- migration confirm/apply/rollback paths covered
- diagnostics distinguish empty vs failed data reads

### Wave 3 - Modbus and control-path hardening

Goal: cover the most failure-prone transport and write-control logic.

Target files:
- [custom_components/kostal_kore/modbus_client.py](./custom_components/kostal_kore/modbus_client.py)
- [custom_components/kostal_kore/modbus_coordinator.py](./custom_components/kostal_kore/modbus_coordinator.py)
- [custom_components/kostal_kore/modbus_proxy.py](./custom_components/kostal_kore/modbus_proxy.py)
- [custom_components/kostal_kore/mqtt_bridge.py](./custom_components/kostal_kore/mqtt_bridge.py)
- [custom_components/kostal_kore/ksem_coordinator.py](./custom_components/kostal_kore/ksem_coordinator.py)
- [custom_components/kostal_kore/modbus_button.py](./custom_components/kostal_kore/modbus_button.py)
- [custom_components/kostal_kore/modbus_number.py](./custom_components/kostal_kore/modbus_number.py)
- [custom_components/kostal_kore/live_test.py](./custom_components/kostal_kore/live_test.py)
- [custom_components/kostal_kore/modbus_test.py](./custom_components/kostal_kore/modbus_test.py)

Why third:
- highest density of protocol edge cases
- strongest concentration of failure classification logic
- easiest place to fake complex hardware responses once helper fixtures exist

Estimated effort:
- `5-8` days

Exit criteria:
- truncated read responses covered
- byte-order invalidity covered
- write-path exception typing covered
- unsupported/suppressed register behavior covered
- proxy forwarding and FC16 validation covered

### Wave 4 - Entity platforms and advisory/control logic

Goal: finish real coverage on the large Home Assistant platform files and all
specialized monitoring logic.

Target files:
- [custom_components/kostal_kore/sensor.py](./custom_components/kostal_kore/sensor.py)
- [custom_components/kostal_kore/switch.py](./custom_components/kostal_kore/switch.py)
- [custom_components/kostal_kore/number.py](./custom_components/kostal_kore/number.py)
- [custom_components/kostal_kore/select.py](./custom_components/kostal_kore/select.py)
- [custom_components/kostal_kore/coordinator.py](./custom_components/kostal_kore/coordinator.py)
- [custom_components/kostal_kore/health_monitor.py](./custom_components/kostal_kore/health_monitor.py)
- [custom_components/kostal_kore/health_sensor.py](./custom_components/kostal_kore/health_sensor.py)
- [custom_components/kostal_kore/health_binary_sensor.py](./custom_components/kostal_kore/health_binary_sensor.py)
- [custom_components/kostal_kore/fire_safety.py](./custom_components/kostal_kore/fire_safety.py)
- [custom_components/kostal_kore/fire_safety_entities.py](./custom_components/kostal_kore/fire_safety_entities.py)
- [custom_components/kostal_kore/battery_chemistry.py](./custom_components/kostal_kore/battery_chemistry.py)
- [custom_components/kostal_kore/longevity_advisor.py](./custom_components/kostal_kore/longevity_advisor.py)
- [custom_components/kostal_kore/longevity_entities.py](./custom_components/kostal_kore/longevity_entities.py)
- [custom_components/kostal_kore/degradation_tracker.py](./custom_components/kostal_kore/degradation_tracker.py)
- [custom_components/kostal_kore/degradation_entities.py](./custom_components/kostal_kore/degradation_entities.py)
- [custom_components/kostal_kore/battery_soc_controller.py](./custom_components/kostal_kore/battery_soc_controller.py)
- [custom_components/kostal_kore/soc_controller_entities.py](./custom_components/kostal_kore/soc_controller_entities.py)
- [custom_components/kostal_kore/charge_block_switch.py](./custom_components/kostal_kore/charge_block_switch.py)
- [custom_components/kostal_kore/grid_charge_limiter.py](./custom_components/kostal_kore/grid_charge_limiter.py)
- [custom_components/kostal_kore/battery_test.py](./custom_components/kostal_kore/battery_test.py)

Why last:
- these files are the broadest and most stateful
- they benefit most from all earlier fixtures and transport fakes
- they contain many of the reviewed business-logic edge cases

Estimated effort:
- `8-12` days

Exit criteria:
- platform setup and unload paths covered
- false-green startup states covered
- control-loop stop/reset paths covered
- persistence, notifications, and restore-state behavior covered

---

## Starter pack: first 10 tests to write

These are the first concrete regression tests recommended for Wave 1 and early
Wave 2. They were selected because they unlock real files from `omit` quickly
and cover behavior already identified as risky.

1. `binary_sensor.async_setup_entry()` creates fire-safety binary sensors when
   `fire_safety` exists and `health_monitor` is missing.
2. `binary_sensor.async_setup_entry()` does not create entities when Modbus is
   disabled.
3. `text` entity does not recreate missing entry store on a pure state read.
4. `text` entity stores confirmation values without mutating unrelated entry data.
5. `notifications.notify_safety_alert()` uses entry-scoped IDs so same-severity
   alerts from different devices do not overwrite each other.
6. `diagnostics._get_diagnostics_data_safe()` marks configuration fetch failure
   distinctly from empty configuration data.
7. `request_scheduler.request()` enforces the intended priority semantics, or
   the module contract is updated and tests assert the simpler serialized model.
8. `scheduled_session` keeps serialization in place for the full logical request
   lifecycle, not only header acquisition.
9. `config_flow.resolve_connection_safe()` does not probe arbitrary hosts with
   credentials during reauth/reconfigure when host is blank.
10. `migration_services` rejects many-to-one `entity_map` collisions before any
   history copy starts.

---

## Per-wave test design rules

To avoid fake coverage, each new test batch should follow these rules:

1. At least one negative-path test per public async entry point.
2. At least one cleanup/unload test for every file that registers listeners,
   tasks, or notifications.
3. Every previously reviewed P1/P2 issue should gain either:
   - a failing regression test before the fix, or
   - an explicit TODO test entry if the fix is deferred.
4. Prefer narrow, behavior-based assertions over snapshotting large objects.
5. Prefer Home Assistant-style fixture tests over pure mock pyramids when entity
   state or platform wiring is involved.

---

## Required refactors before coverage expansion

The following refactors are likely required to make real 100% achievable without
brittle tests:

- extract notification ID generation into small pure helpers
- isolate `hass.data` store access behind helper functions
- split transport decoding from coordinator bookkeeping
- separate entity factory logic from side-effectful setup code
- expose lightweight state/decision helpers for diagnostics and advisory logic

These refactors should stay behavior-preserving and land with tests.

---

## What success looks like

The roadmap is complete when:

- `.coveragerc` no longer hides the main platform and control modules
- the test suite still passes cleanly
- branch coverage remains at `100%`
- the coverage number reflects the actual integration, not only a curated subset

At that point, saying "we have 100% coverage" becomes technically honest.
