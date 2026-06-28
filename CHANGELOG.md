# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- **Options flow crash on current Home Assistant** — `KostalPlenticoreOptionsFlow`
  assigned `self.config_entry`, which Home Assistant turned into a read-only
  property (`AttributeError: property 'config_entry' … has no setter`, hard since
  HA 2025.12). The flow now relies on the framework-provided `config_entry`
  property and no longer assigns it, so opening the integration options works on
  current HA. Surfaced by the new Python 3.14 / current-HA CI leg.

### Changed
- **Minimum Home Assistant raised to 2024.11.0** (`manifest.json`, `hacs.json`,
  `README.md`) — required for the auto-provided `OptionsFlow.config_entry`
  property, and consistent with the `DataUpdateCoordinator` `config_entry`
  support the integration already assumes.
- **Honest quality self-assessment** — `quality_scale.yaml` previously claimed
  `platinum` with `test-coverage`/`test-coverage-full` marked done ("100% across all
  integration modules, 133 tests"), but `.coveragerc` omits 41 of ~57 modules from the
  enforced gate. The two coverage rules are now `todo` (pointing at
  `COVERAGE_ROADMAP.md`), the test count corrected to 913, and the tier set to the
  honest `bronze` (gated by the open coverage rule; most higher rules are still met
  individually).
- **Removed coverage-padding tests** — `Tests/test_platinum_features.py` now keeps only
  the two behaviour tests (Modbus exception hierarchy and parsing); the `assert True` /
  `assert hasattr(...)` "platinum feature" tests that verified no behaviour were dropped.
- **Migration forward-compat coverage** — added an end-to-end
  migrate → `finalize_legacy_cleanup` test asserting the device ends with only the
  `kostal_kore` identifier (the domain-scoped state HA 2026.8 requires);
  `MIGRATION_ARCHITECTURE.md` updated to record that the rewrite is implemented + tested.

### Security
- **Workflow token least-privilege** — `ci.yml` and `hacs-validation.yml` now declare
  `permissions: contents: read`, resolving the two CodeQL "workflow does not contain
  permissions" alerts.
- **Removed vendored `pykoplenti-master/uv.lock`** — the upstream library's dev
  lockfile was the sole source of all 25 Dependabot alerts (aiohttp, black, pytest,
  virtualenv, uv, filelock, idna). It is never installed or used by KORE (CI installs
  `pykoplenti==1.5.0` from PyPI; `aiohttp` comes from Home Assistant core), so
  removing it clears every alert without changing any shipped dependency.
- **Modbus proxy bind-address guardrail** — a malformed bind value is coerced to the
  loopback default in the options flow, and the proxy logs a clear warning when it binds
  to a non-loopback address (LAN exposure of the inverter control proxy).

### CI
- **Test against current Home Assistant Python** — the CI test/typecheck job now runs
  on a Python matrix: `3.14` (the version current Home Assistant requires, ADR-0020)
  and `3.12` (the manifest floor, HA 2024.11). Smoke tests now always cover the Python
  runtime current HA ships with; bump the upper version when HA raises its minimum
  Python. mypy runs on the floor leg only — current HA source uses Python 3.14-only
  syntax that mypy cannot parse under the 3.12 target.

### Docs
- **evcc proxy guide** — `PROXY_SETUP.md` now recommends the `kostal-plenticore-gen2`
  template for **all** inverter generations (G1/G2/G3); the legacy `kostal-plenticore`
  name is still accepted by evcc via `covers:` (evcc PR #30854) but no longer advised.
- **evcc `batteryMode` conflict note** — Documented that evcc's gen2 `batteryMode`
  cyclically writes registers 1034/1038/1040 — the same registers KORE controls — so
  evcc battery control and an internal KORE controller (SoC controller, GridGuard,
  Block Battery Charging) must not run simultaneously (`PROXY_SETUP.md`, `README.md`).
- **evcc `batteryMode` register mapping** — `PROXY_SETUP.md` now documents the per-mode
  register/value mapping (`charge`→1034 negative W, `hold`→1040=0, `holdcharge`→1038=0)
  and advises configuring `maxchargepower` (W) instead of the deprecated `maxchargerate`
  (%), plus a minimum-evcc-version caveat (evcc PRs #26169 / #26515 / #27161 / #30853).
- **evcc `endianness` note** — Documented that the evcc template's `endianness` parameter
  must match the byte order KORE auto-detects; since evcc PR #30862 a mismatch also
  corrupts the PV-energy reading (register 1056) (`PROXY_SETUP.md`).

## [3.0.0] — 2026-05-24 — Production readiness

### Added
- **REG 1038 owner arbitration** — Grid Feed-In Optimizer, SoC Controller, and
  Block Battery Charging mutually exclude writes to `bat_max_charge_limit`
  (register 1038). Turning on a second feature raises a clear `HomeAssistantError`.
- **`slow_poll_stale` coordinator flag** — Exposed in debug bundles and
  coordinator state when slow-register data is merged from a failed refresh.
- **Service translations** (`translations/en.json`) for migration, orphan-history,
  and debug-bundle services.
- **Startup setup trace logging** — Filter logs with `Kostal setup trace` to
  follow config-entry phases, platform timing, entity batches, reload triggers,
  and unload steps.
- **Orphan-history MVP** — `scan_orphan_history` / `apply_orphan_history_mapping`
  services and `docs/migration_orphan_history.md`.

### Changed
- **Integration name and version** — Dropped “Experimental Alpha”; release **3.0.0**.
- **`export_debug_bundle`** — Passwords, service codes, and other sensitive keys
  are redacted via `async_redact_data` before writing JSON to `/config/www/`.
- **Config flow / number audit labels** — `write_access` and write-audit `user_type`
  use `CONF_INSTALLER_ACCESS` only (no `CONF_SERVICE_CODE` fallback).
- **Modbus proxy REG 1038 arbitration** — External FC06/FC16 writes to register
  1038 are rejected while an integration feature holds the owner lock (evcc,
  iobroker, etc.).
- **Grid Feed-In Optimizer** — `modbus_read_degraded` attribute; control-loop
  `finally` always restores charge limit and releases REG 1038.

### Fixed
- **Debug bundle secret leak** — `modbus_snapshot` / `rest_snapshot` were written
  unredacted; now covered by the same `TO_REDACT` set as config diagnostics.
- **Isolation restore poisoning** — Persisted isolation sentinel values are no
  longer seeded into the health-monitor deque on startup or re-saved to disk.
- **REG 1038 contention** — Three features could overwrite each other's charge
  limit without coordination.
- **Dead lifecycle keys** — Removed unused `KEY_SETUP_IN_PROGRESS` /
  `KEY_UNLOAD_IN_PROGRESS` from `__init__.py` (options reload guard uses
  `ConfigEntryState.LOADED` since PR #51).
- **Shutdown poll / unload** — `ModbusShutdownAbort` during reload; Modbus
  shutdown ordering and `_closing` flag (PRs #35–#51 on `main`).
- **Bug #11** — Dynamic PV per-string energy statistics for 1–6 strings.
- **Bug #1 regression guard** — `FullChargeCap_E` remains **Ah**, not kWh.

### Documentation
- **LEARNINGS §59–§60** — PR #47 merge and installer-access fix; removed stale
  “PR #47 in draft” / service-code fallback wording.
- **`services.yaml`** — Debug bundle description matches redaction behaviour.

## [2.16.10-rc.6] — 2026-05-22 — Reload-Loop Hotfix (b8→b9)

### Fixed
- **Modbus shutdown race during reload** (Cursor PR #35). After upgrading to
  v2.16.12b8, some installations showed a slow/messy reload sequence that
  looked like an initialization loop, with `SoC controller stop timed out
  after 5.0s during unload`, `Connection lost reading … reconnecting` *during*
  the unload, and `pymodbus: transaction_id mismatch` errors. Root causes:
  1. `ModbusDataUpdateCoordinator.async_shutdown()` did not call
     `super().async_shutdown()`, so the DataUpdateCoordinator's scheduled
     polls kept firing while the TCP client was being disconnected.
  2. Unload order stopped the SoC controller **before** shutting down Modbus,
     so `_write_normal()` (the safe-default restore) held the connection busy
     while background reads tried to use the same socket — 5s timeout.
  3. `read_register()` attempted a reconnect after `disconnect()` had already
     started, racing with the teardown.
  4. The options-update listener fired during the reload window (entry state
     `SETUP_RETRY` / `SETUP_IN_PROGRESS`) which could cascade into a second
     reload.
- **Fixes applied:**
  - `modbus_coordinator.async_shutdown()` now calls
    `super().async_shutdown()` first to stop scheduled polls before the TCP
    disconnect runs. `_async_update_data` raises `UpdateFailed` when the
    client is closing.
  - `modbus_client._closing` flag suppresses reconnect attempts during
    shutdown. `connect()`, `reconnect()`, and the `read_register()` exception
    path all check this flag.
  - Unload order changed to **Proxy/MQTT → Modbus shutdown → SoC stop** in
    both `async_unload_entry` and `_rollback_setup`.
  - `BatterySocController._write_normal()` skips the register restore if the
    Modbus client is closing or disconnected (prevents the 5s timeout).
  - `_async_options_updated` ignores updates unless
    `entry.state is ConfigEntryState.LOADED` — eliminates the reload-during-
    setup-retry cascade.
- **Listener cleanup** (orthogonal): `_feed_health_data` (`__init__.py`) and
  `_feed_rest_soh` (`sensor.py`) listener subscriptions now capture their
  unsubscribe handles and tie them to `entry.async_on_unload`. Without this,
  reloads left stale closures bound to the previous cycle's
  `health_monitor` / `degradation_tracker` / `battery_soh_calc` objects.

### Tests
- `Tests/test_modbus_client.py::test_read_skips_reconnect_when_closing` —
  verifies `read_register` aborts with `ModbusConnectionError("aborted
  during shutdown")` and does not call `reconnect()` when `_closing=True`.
- `Tests/test_modbus_integration.py::test_options_updated_ignored_when_entry_not_loaded_state`
  — verifies the `ConfigEntryState.LOADED` guard.

### Mitigation note for affected users
- Users who upgraded directly from b7→b8 and experienced the slow setup/loop
  can downgrade to b7 via HACS as an immediate workaround, then update to
  b9 once tagged.
- Running `kostal_plenticore` (legacy) alongside `kostal_kore` increases the
  chance of seeing the symptom because both clients share the inverter's
  modest concurrent-connection budget. Disable the legacy integration if
  only KORE is in use.

---

## [2.16.10-rc.5] — 2026-05-22 — Battery SoH Calculator, Sentinel Filters, Race Condition Fixes

### Added
- **Battery SoH Calculator** — New module `battery_soh_calculator.py` derives
  State-of-Health from Modbus telemetry without relying on the inverter's own
  (often unreliable) SoH register. Two complementary methods:
  - *Capacity-ratio SoH*: baseline = highest work-capacity ever observed
    (persisted across restarts via `Store`). Current SoH = current / baseline × 100.
    Baseline self-calibrates upward during early commissioning cycles (0.5 % threshold),
    protected by a 10 MWh sanity ceiling against corrupted Modbus frames.
  - *5-year OLS projection*: linear regression of `capacity_wh` vs `discharge_kwh`
    (industry-standard axis, discharge only). Extrapolates 5 years using observed
    annual throughput rate. Requires ≥ 30 samples AND ≥ 30 days observation window
    before reporting (gate prevents spurious early projections). Minimum 3-hour
    sampling interval, rolling 500-sample window.
  - Store schema versioned at **v2**: v1 used `charge + discharge` as throughput
    axis (double-counting); v2 uses discharge only. Migration drops v1 samples
    but preserves baseline (capacity reading, axis-independent).
- **Battery SoH Entities** — Two new diagnostic sensors backed by the calculator:
  - `sensor.*_battery_soh_calculated` — live capacity-ratio SoH in %. Attributes:
    `source`, `baseline_wh`, `current_wh`, `baseline_age_days`, `total_discharge_kwh`,
    `total_charge_kwh`, `cycles_observed`, `samples`.
  - `sensor.*_battery_soh_projection_5y` — 5-year OLS extrapolation in %. Attributes:
    `source`, `degradation_per_kwh`, `annual_discharge_kwh`, `samples`,
    `projection_reliable` (False until 30 samples / 30 days met).
  Both are `EntityCategory.DIAGNOSTIC`, unavailable until sufficient data exists.
- **REST SoH listener** — `sensor.py` now feeds the inverter's own REST-reported
  battery SoH into `health_monitor` and `degradation_tracker` via
  `process_data_update_coordinator.async_add_listener`. NaN/Infinity guard applied
  before forwarding. Fixes "Unknown" state on `Battery Health (SoH Trend)` and
  `Battery Health Warning` entities that previously never received data.
- **Isolation resistance sentinel filter** — Central constant
  `ISOLATION_SENTINEL_OHM = 65_535_000.0` (`= 0xFFFF × 1000`, UINT16-max
  multiplied by the inverter's kΩ→Ω scale factor) added to `helper.py`.
  The sentinel value is now rejected in `health_monitor.py`, `degradation_tracker.py`,
  and the Modbus coordinator's isolation restore/save path, preventing a spurious
  maximum reading from being persisted and flattening the isolation history graph.
- **Orphan-history `wr_`/`wr2_` prefix support** — `orphan_history.py` now
  recognises legacy entity IDs containing `.wr_` and `.wr2_` (dot-anchored to
  prevent substring false positives). Suffix-stripping also extended for `wr_` /
  `wr2_` prefixes. Covers WR2 (second inverter) legacy Plenticore naming.
- **Grid Feed-In Limiter race condition fix** — `GridFeedInLimiterSwitch` is now
  pre-instantiated in `__init__.py` before `async_forward_entry_setups` runs.
  The `switch` platform reads the pre-built instance from `entry_data` rather
  than constructing a new one. Eliminates the NUMBER/SWITCH platform setup race
  where the switch entity could be registered before the number entity had set
  the initial limit, causing the first write to use a stale value.

### Fixed
- **Worktime outlier false-positive** — Modbus register 144 (`worktime`, lifetime
  counter in seconds) triggered the global absolute-limit outlier guard at ~4
  months uptime. Added register-specific override in `OUTLIER_ABS_LIMIT_OVERRIDES`
  (`144: 10_000_000_000.0`, i.e. > 300 years headroom) so the counter is never
  rejected as an outlier.

### Tests
- `Tests/test_battery_soh_calculator.py` — 38 tests: baseline raise threshold,
  sentinel rejection, OLS accuracy, `denom == 0` edge case, `d_sec <= 0` edge
  case, store migration v1→v2 (samples dropped, baseline kept), save-failure
  swallowing, partial-None attribute paths, discharge-only axis verification,
  projection gate (samples + time window both required), `_opt_float` edge cases.
- `Tests/test_battery_soh_entities.py` — 8 tests: availability gating on both
  sensors, value rounding (2 decimal places), `source` attribute propagation,
  partial-None attribute passthrough, `projection_reliable` flag, factory
  `create_battery_soh_sensors` unique-ID and coordinator wiring.
- `Tests/test_health_monitor.py` — added `test_isolation_sentinel_65535000_skipped`.
- `Tests/test_orphan_history.py` — added `test_scan_orphans_sync_recognizes_wr_prefix`
  and `test_scan_orphans_sync_wr_anchor_avoids_substring_false_positives`.
- `Tests/test_modbus_client.py` — added worktime outlier coverage test.

---

## [2.16.10-rc.4] — 2026-05-19 — 100% Branch Coverage

Final push to reach enforced 100% branch + statement coverage on all measured
files. Includes a latent runtime bug fix in the migration check.

### Fixed
- **`__init__.py` migration check**: Removed reference to
  `RegistryEntry.original_unit_of_measurement` which does not exist on the
  supported HA version range. `unit_of_measurement` alone is the correct
  attribute — it already holds the effective persisted unit (user override or
  first-registration value). The original code was a latent `AttributeError`
  waiting to fire on any installation that had migrated a WorkCapacity entity.
- **mypy**: Zero errors after the `original_unit_of_measurement` removal.

### Tests
- **`test_init.py`**: Added 2 migration-check tests covering both branches of
  the WorkCapacity unit check (Ah → issue created; Wh → issue cleared). The
  "clear" path uses `_DummyPlenticore` + direct `async_setup_entry` call to
  prevent HA's platform setup stack from touching the entity registry between
  pre-fill and the migration check.
- **`test_modbus_integration.py`**: Added 4 tests covering previously-missed
  branches: MQTT bridge with empty identifiers (device_id falls back to
  entry_id), MQTT bridge with a non-matching identifier before a matching one
  (loop-continues path), `_async_options_updated` when entry_data is absent,
  and `_async_options_updated` when options changed since last setup.

## [2.16.10-rc.3] — 2026-03-29 — CI Fixes, Test Coverage, QA Regression Fixes

Third pass: CI compliance (mypy, test coverage 100%), plus self-review
regression analysis that found and fixed 4 real bugs introduced by prior fixes.

### Fixed — CI Compliance
- **mypy**: Replaced function-attribute debounce pattern (`_feed_health_data._clear_sent`) with closure-captured dict to satisfy mypy `attr-defined` when `@callback` is properly typed in CI.
- **test coverage**: Added 17+ test functions across `test_phase5_coverage.py`, `test_helper.py` to reach 100% branch coverage on all 9 measured files.
- **test assertion**: Fixed stale `format_round_back(4.4) == "4"` → `"4.4"` to match new fractional-precision behavior.

### Fixed — Regressions Found by QA Self-Review
- **coordinator.py (R5)**: `_fetch_device_metadata` now catches `asyncio.TimeoutError` in addition to `TimeoutError`. Required because `get_hostname_id` now re-raises the original `asyncio.TimeoutError` instead of wrapping it in `ApiException`.
- **charge_block_switch.py (R7)**: Restored `try/except` in `_write_block()` — a failed Modbus write no longer propagates unhandled through `async_turn_on()`, which would leave the switch in an inconsistent state (keepalive not started, `_is_on` not set). `async_turn_on` now discards the snapshot on write failure and returns gracefully.
- **__init__.py (R1)**: `async_setup_entry` now clears both legacy unscoped issue IDs (`kostal_kore_auth_failed`) AND new entry-scoped IDs (`kostal_kore_{entry_id}_auth_failed`). Prevents phantom repair issues surviving an upgrade from the old ID scheme.
- **notifications.py (P1)**: `notify_safety_clear` now fires all dismiss calls via `asyncio.gather()` instead of sequential awaits. Also dismisses legacy unscoped notification IDs for upgrade compatibility.

### Added
- **Backlog implementation wave completed**:
  - Event intelligence coordinator with bounded history + dedup/cooldown.
  - Optional KSEM coordinator and source-precedence diagnostics entities.
  - Modbus register coverage expansion and data-quality guards.
  - Advanced write safety controls (allowlist, arming, validation, verification).
- **Guarded migration services**:
  - `kostal_kore.adopt_legacy_entity_ids` for safe registry rebind previews/applies.
  - `kostal_kore.copy_legacy_history` for optional advanced recorder metadata merge.
- **Project learnings doc**: Added `LEARNINGS.md` with validated behavior and policy decisions.
- **Migration architecture decision record**: Added `MIGRATION_ARCHITECTURE.md` documenting known limitations and future-proofing plan for legacy device migration.

### Changed
- **Markdown documentation sweep**: Synced all maintained `.md` files with current `kostal_kore` naming, migration flow, and access model.
- **Developer docs refresh**: Updated `QUICK_REFERENCE.md`, `AI_DOCUMENTATION.md`, and `ENTITY_REFERENCE.md` to remove stale paths/versions and align with optional Modbus/MQTT architecture.
- **Agent runbook refresh**: Updated `AGENTS.md` test-count guidance and mypy path to current repository layout.
- **Obsolete guide cleanup**: Removed untracked/ignored `custom_components/kostal_kore/DEVELOPMENT_GUIDE.md` and consolidated references to maintained docs.
- **Auto-discovery documentation clarified**: discovery is best-effort and manual host entry remains primary fallback in segmented networks.
- **Write model clarified**: battery charge/discharge setpoint control is documented as Modbus-only by design.
- **Operations docs extended**: Added guarded migration service usage, grid feed-in optimizer behavior, and `Isolation Resistance = unknown` troubleshooting notes across README/LEARNINGS/migration guides.

### Fixed
- **REST write safety policy**: blocked unsupported REST battery charge/discharge setpoint write targets to avoid non-deterministic behavior.

## [2.16.10-rc.2] — 2026-03-28 — Codex QA Hardening Pass (continued)

Second round of Codex static analysis review. Focused on multi-inverter
notification collisions, hass.data store lifecycle, firmware edge cases,
and fire safety learning for 3-string systems. Includes a self-review
pass that caught regressions in the initial fixes.

### Fixed — Multi-Inverter Notification Scoping
- **charge_block_switch.py**: notification ID scoped by `entry_id`; notification dismissed on turn-off and entity removal.
- **battery_soc_controller.py**: added `entry_id` parameter; notification ID scoped by `entry_id`.
- **battery_test.py**: added `entry_id` parameter; notification ID scoped by `entry_id`.
- **notifications.py / diagnostic_entities.py / repairs.py** (prior session): all notification and repair IDs already scoped by `entry_id`.

### Fixed — hass.data Store Lifecycle
- **helper.py**: `integration_entry_store()` returns detached empty dict when entry has been unloaded, preventing ghost store resurrection after `async_unload_entry`.
- **text.py** (prior session): uses `.get()` instead of `.setdefault()` for same reason.

### Fixed — Firmware Edge Cases
- **diagnostics.py**: `StringCnt` clamped to `[0, MAX_SANE_STRING_COUNT]` to prevent empty or oversized feature probe from malformed firmware data.
- **switch.py**: `StringCnt` clamped to `[0, MAX_SANE_STRING_COUNT]` with warning log on out-of-range values to prevent shadow-management stall.
- **const.py**: shared `MAX_SANE_STRING_COUNT = 6` constant for consistent bounds across modules.

### Fixed — Fire Safety
- **fire_safety.py**: DC ratio learning generalized from 2-string-only to all multi-string systems. Uses deviation-from-equal-share metric instead of min/max ratio, which was unstable for 3+ strings.
- **fire_safety.py**: `_is_stable_ratio()` updated to check jitter of the deviation metric (< 0.10 threshold) instead of relative deviation from ratio mean.

### Fixed — Platform Setup
- **binary_sensor.py**: removed early return on missing `health_monitor` so fire-safety binary sensors are created independently in partial-init scenarios.
- **binary_sensor.py**: updated docstring to reflect health + fire-safety scope.
- **binary_sensor.py**: migrated from `AddEntitiesCallback` to `AddConfigEntryEntitiesCallback` compat shim.
- **manifest.json**: `loggers` updated from `["kostal"]` to `["custom_components.kostal_kore", "pykoplenti"]`.

### Breaking Changes (minor)
- Persistent notification IDs for charge block, SoC controller, and battery test now include the config entry ID. Existing automations that match on the old global notification IDs (e.g. `kostal_charge_block`, `kostal_soc_controller`, `kostal_battery_test`) must be updated to include the entry ID suffix.

## [2.16.10-rc.1] — 2026-03-28 — Codex QA Hardening Pass

Systematic code audit driven by Codex static analysis feedback. Each finding
was manually validated before implementation. ~50 findings reviewed across
15+ files; ~35 confirmed valid and fixed, ~15 rejected as false positives or
already covered by prior work.

### Fixed — Sensor & Entity Lifecycle
- **sensor.py**: `PlenticoreCalculatedSensor` missing cleanup in `async_will_remove_from_hass` — listeners now properly unsubscribed on entity removal.
- **sensor.py**: DC-string detection threshold `<= 1` → `< 1` — a single DC string is valid, only zero is not.
- **sensor.py**: `PreferredGridPowerSensor.available` now checks coordinator data availability instead of always returning `True`.
- **sensor.py**: `_virt_` filter in availability check prevented legitimate virtual sensors from reporting available.

### Fixed — Number Platform & Keepalive
- **number.py**: G3 keepalive inner write failures are now caught individually, logged as warnings, and abort after 3 consecutive failures (`_MAX_CONSECUTIVE_KA_FAILURES`).
- **number.py**: Registry migration `expected_unique_ids` changed from non-deterministic `set` to sorted list; migration target uses `canonical_uid` instead of `next(iter(set))`.

### Fixed — Coordinator & Setup Lifecycle
- **coordinator.py**: Added `_fetch_refcount` (defaultdict) for reference-counted `start_fetch_data`/`stop_fetch_data` — prevents premature removal when multiple consumers share a fetch key.
- **__init__.py**: Early `async_unload()` on `plenticore.async_setup()` failure was already present; added full `_rollback_setup()` for platform-forwarding failures (SoC controller, Modbus proxy, MQTT bridge, plenticore session).

### Fixed — Switch Platform
- **switch.py**: Three shadow management API calls (`_async_query_string_count`, batch DC-string features, per-string fallback) now wrapped with `asyncio.wait_for(timeout=SWITCH_SETTINGS_FETCH_TIMEOUT_SECONDS)`.

### Fixed — Modbus Stack
- **modbus_registers.py**: Register 529 renamed from `REG_BATTERY_OPERATION_MODE` to `REG_BATTERY_WORK_CAPACITY_SUNSPEC` (unit: Wh) to match SunSpec semantics.
- **modbus_client.py**: Added register count validation (`len(resp.registers) != count` → `ModbusReadError`).
- **modbus_client.py**: `_classify_exception_response` fallback changed from `ModbusReadError` to `ModbusClientError` for non-read exception paths.
- **modbus_proxy.py**: FC16 (Write Multiple) now validates `quantity` range (1–123) and `byte_count == quantity * 2` consistency.
- **modbus_proxy.py**: Forwarding failures (read, write-single, write-multiple) now return Modbus exception **0x04** (Server Device Failure) instead of **0x02** (Illegal Data Address) — prevents clients from permanently blacklisting valid registers.
- **modbus_proxy.py**: Incoming unit-ID is now checked against configured `self._unit_id`; mismatches return **0x0B** (Gateway Target Device Failed to Respond).

### Fixed — System Health & Monitoring
- **system_health_check.py**: `active_warnings` attribute replaced with `active_warning_count` + ParameterTracker sample extraction.
- **system_health_check.py**: Added missing `self.info_count: int = 0` to `_HealthReport`; info-level findings now increment `info_count` instead of `pass_count`.
- **health_monitor.py**: Added missing `pm_cos_phi` to `all_trackers` property.

### Fixed — Config Flow & Security
- **config_flow.py**: Replaced TCP port probe (`_probe_tcp_port`) with `_probe_kostal_api` using unauthenticated `client.get_version()` — eliminates discovery credential spray risk (P1).
- **config_flow.py**: Entire validation flow wrapped in single `asyncio.wait_for(timeout=CONNECTION_TEST_TIMEOUT_SECONDS)`.
- **config_flow.py**: Modbus smoke test counts `reg_failures`; `test_passed = False` when all register reads fail.
- **config_flow.py**: Reauth flow pins host to `existing_entry.data[CONF_HOST]` — prevents host spoofing during reauthentication.

### Fixed — Fire Safety
- **fire_safety.py**: `clear_stale_alerts(pv_active)` moved before standby/off early return — stale alerts are now cleaned up even when inverter is idle.
- **fire_safety.py**: Alert deduplication via `active_keys` set of `(category, risk_level)` — prevents repeated alerts for the same condition.

### Fixed — Battery Test
- **battery_test.py**: Added `_original_charge_limit` / `_original_discharge_limit` snapshot in `_preflight()`.
- **battery_test.py**: `_write_normal()` restores original limits on test completion; falls back to `_DEFAULT_LIMIT_W = 20000.0` if snapshot is missing.

### Fixed — Migration Services
- **migration_services.py**: `_normalise_mapping_rows()` detects many-to-one target conflicts via `target_sources` dict; raises `vol.Invalid` on collision.
- **migration_services.py**: `_ensure_guard_confirmed()` now binds a SHA-256 payload fingerprint — prevents TOCTOU tampering between preview and apply.

### Fixed — Helper Utilities
- **helper.py**: `format_float()` and `format_energy()` return `None` for `NaN`/`Inf` values instead of propagating them to entity state.
- **helper.py**: `get_hostname_id()` re-raises original exceptions instead of wrapping them in `ApiException` — preserves exception type for callers.

### Fixed — MQTT Bridge (prior session)
- **mqtt_bridge.py**: Listener leak on reconnect, idempotency guard, rate-limit ordering, payload validation.

### Fixed — Test Infrastructure
- **pytest.ini**: Suppressed coroutine-unawaited and asyncio-loop-scope warnings.
- **Tests/test_health_monitor.py**: Updated `all_trackers` assertion from 21 → 22 after `pm_cos_phi` addition.

### Known Issue — Legacy Migration Architecture
- Device identifiers are not rewritten from `("kostal_plenticore", serial)` to `("kostal_kore", serial)` during migration. This cross-domain device-linking pattern will break in HA Core 2026.8. See `MIGRATION_ARCHITECTURE.md` for the remediation plan.

### Strings
- **strings.json**: Timeout error now has its own message instead of mapping to `cannot_connect`. Reauth flow: added description text, changed host label to clarify it is read-only.

## [2.16.0-alpha.4] - 2026-03-01

### Added
- **Two-step legacy migration flow**: New button actions for (1) import/migrate and (2) delayed cleanup.
- **Registry migration routine**: Added migration logic to rebind entity/device registry records from old entry to new entry.
- **Migration tests**: Added dedicated tests for legacy migration behavior and button setup coverage.

### Changed
- **Button platform scope**: Button platform now loads for every entry (migration button always available), while Modbus-only buttons remain conditional.
- **Docs**: README now includes two-step migration guidance from old plugin.
- **Docs / provenance**: Added explicit credits and transparency disclosure (thanks to `@stegm`, AI-assisted coding disclosure, manual validation note by `@Puma7`).
- **Docs / MQTT examples**: Updated `PROXY_SETUP.md` example topic prefix from `kostal_plenticore/...` to `kostal_kore/...`.

## [2.16.0-alpha.2] - 2026-03-01

### Added
- **First-run setup wizard**: Initial setup now includes a second guided step to directly enable Modbus TCP, MQTT bridge, and Modbus proxy.
- **Best-effort auto-discovery**: If host/IP is left empty, setup now probes local IPv4 networks for reachable inverter candidates.
- **Access profile detection**: Setup now stores detected account role and installer-write capability (`access_role`, `installer_access`).
- **Plugin logo asset**: Added `docs/assets/kostal_kore_logo.svg` and integrated it into `README.md`.

### Changed
- **Installer gating basis**: Write permission checks now prioritize detected installer access, with service-code fallback for legacy entries.
- **Integration domain/path branding**: Switched release metadata and package domain to `kostal_kore` and `custom_components/kostal_kore`.
- **MQTT topic prefix / notifications**: Prefix moved from `kostal_plenticore` to `kostal_kore`.

### Fixed
- Setup UX now supports both manual host entry and discovery fallback without forcing the user into separate flows.
- Initial setup can activate Modbus/MQTT/proxy immediately, instead of requiring a post-install options roundtrip.

## [2.16.0-alpha.1] - 2026-03-01

### Added
- **HACS Alpha Release Metadata**: `hacs.json`, explicit `LICENSE`, updated manifest links (`documentation`, `issue_tracker`) and minimum Home Assistant version metadata.
- **Proxy Security Hardening**:
  - New option `modbus_proxy_bind` (default `127.0.0.1`) to avoid accidental network-wide exposure.
  - Installer access is now required for battery-control writes via Modbus proxy and MQTT bridge.
- **Worldwide Grid Profile Adaptation**:
  - Fire safety, health monitor and diagnostics now adapt thresholds to detected **50/60Hz** and **120/230V** profiles.
- **Inverter-size-aware control limits**:
  - New `power_limits.py` helper to derive safe limits from `inverter_max_power` instead of fixed 20kW assumptions.

### Changed
- **SoC Controller / charge blocking / feed-in optimizer** now clamp and restore power limits based on inverter capabilities.
- **Modbus device info polling** now includes `num_bidirectional` for better DC3/battery topology handling.
- **Version** bumped from `2.15.0` to experimental `2.16.0-alpha.1`.

### Fixed
- Removed fixed 20kW restore values in several control paths that could conflict with small inverters (e.g. 1kW/3kW/5kW systems).
- Modbus proxy FC16 arbitration now checks range overlap for protected battery registers (not only start address).

## [2.15.0] - 2026-03-01

### Added
- **Modbus TCP Proxy Server** (`modbus_proxy.py`) — Lokaler TCP-Proxy (Port 5502) für evcc und andere externe Systeme. Nur EINE Modbus-Verbindung zum Wechselrichter nötig.
  - Cache-Hit: Bekannte Register sofort aus dem Coordinator-Cache
  - Cache-Miss: SunSpec-Register (40000+) transparent an den Wechselrichter weitergeleitet
  - **Write-Arbitration**: Batterie-Register werden blockiert wenn der interne SoC-Controller aktiv ist (Modbus Exception 0x06 = Server Device Busy)
  - Konfigurierbar über HA-Oberfläche (Proxy aktivieren + Port)
- **Battery SoC Controller** (`battery_soc_controller.py`) — Automatische Lade-/Entladesteuerung auf einen Ziel-SoC.
  - `number.XXX_battery_target_soc` — Slider 10-95%
  - `number.XXX_battery_max_charge_power` — Max. Ladeleistung (W)
  - `number.XXX_battery_max_discharge_power` — Max. Entladeleistung (W)
  - Sichere Stopp-Logik: Direktionaler Vergleich verhindert Überschießen bei Pylontech SoC-Sprüngen
  - Automatischer Reset auf Automatik-Modus bei Ziel, Fehler oder Stopp
- **Battery Test Suite** (`battery_test.py`) — 4-Phasen Lade-/Entladetest mit Pre-Flight-Checks, Live-Monitoring, Keepalive und Debug-Log-Datei.
  - Pre-Flight: WR-Kapazität, HW-Limits, SoC, Temperatur, Hauslast, Isolation
  - Live-Monitoring: Direkter Register-Read alle 10s, Safety-Abbruch
  - Debug-Log: `battery_test_debug.log` mit jedem Register-Read/Write
- **Modbus Batterie-Steuerungsdoku** in `QUICK_REFERENCE.md` — Register-Tabelle, 3 Steuerungsmethoden, evcc-Konfigurationsbeispiele, HA-Automationsbeispiele
- **evcc-Anbindungsdoku** in `PROXY_SETUP.md` — Komplett überarbeitete Anleitung für Modbus TCP Proxy und MQTT Bridge

### Changed
- **Version** von 2.14.3 auf 2.15.0
- **Power Meter Voltage** — Zeigt jetzt 1 Nachkommastelle statt gerundeter ganzer Zahlen
- **DC-String Vergleich** — Nutzt `num_bidirectional` (Modbus Register 30) um DC3 als Batterie zu erkennen und aus PV-Vergleichen auszuschließen
- **Battery SoH 0%** — Wird als "nicht verfügbar" behandelt statt als kritische Warnung

### Fixed
- **Falsche DC-String Sicherheitswarnungen** — Verschiedene String-Ausrichtungen (Süd/Nord, Y-Adapter) lösen keine Fehlalarme mehr aus. Ratio-Learning erkennt stabile Leistungsverhältnisse.
- **Batterie-Steuerung: Vorzeichenkonvention** (Kostal §3.4) — Register 1034: negativ=Laden, positiv=Entladen. War invertiert implementiert.
- **Batterie-Steuerung: Deadman-Switch** — Keepalive läuft jetzt VOR den langsamen Monitor-Reads. Intervall 15s statt 25s. Verhindert Timeout des G3-Fallback-Timers.
- **G3 Firmware-Bug REG 1080** — `battery_mgmt_mode` meldet immer 0 obwohl externe Steuerung aktiv. Herabgestuft zu Warnung, Schreibtest ist der echte Gate-Keeper.
- **Batterie-Test: WR-Abschaltung bei Phasenwechsel** — Kein Reset zwischen Phasen, direkter Übergang verhindert Standby-Abschaltung des WR bei Nacht.

## [2.9.0] - 2026-02-26

### Added
- **ARCHITECTURE.md** — Konzeptdokument für die perfekte REST/Modbus-Parallelisierung (Unified Coordinator, Request Scheduler, Datenquellen-Mapping, Failover-Strategie, Migrationsplan).
- **LEARNINGS.md** — Gesammelte Erkenntnisse aus dem gesamten Projekt: Hardware (10 Punkte), Software-Architektur (7), Sicherheit (6), Diagnose (5), Performance (3).

### Changed
- **REST API Polling verlangsamt wenn Modbus aktiv**: Process Data 10s→60s, Settings 30s→90s. Modbus übernimmt Echtzeit-Daten (5s).

### Fixed
- **DC2/DC3 Sensoren nicht verfügbar** — String Count jetzt primär aus Modbus Register 34 gelesen statt REST API (Timeout-anfällig bei parallelem Polling). Sicherer Fallback auf 2 Strings.
- **PV System Safety "Unsicher" bei Nacht** — Alle Safety-Checks werden bei Inverter-State Off/Standby/Shutdown übersprungen. Isolation-Check prüft ob genug DC-Spannung für valide Messung vorhanden ist.
- **Modbus Diagnostics Button** — "Run Modbus Diagnostics" erstellt einen Report direkt als HA Persistent Notification, kein Terminal nötig.

## [2.8.0] - 2026-02-26

### Added
- **Live Test Tool** (`tools/live_test.py`) — standalone read-only diagnostic script to test Modbus connection before enabling it in HA. Reads all registers, detects endianness, identifies battery type, checks battery management mode, and generates a JSON report for developer analysis.
- **Battery Chemistry Detection** — auto-detects battery chemistry (LFP/NMC) from Modbus register 588 (battery type). Supported brands: BYD, Pyontech, VARTA, Dyness, ZYC (LFP); LG, BMZ, AXIstorage, PIKO (NMC).
- **Per-Chemistry Temperature Thresholds**:
  - LFP (LiFePO4): optimal <30°C, acceptable <40°C, warning >50°C, critical >60°C
  - NMC (Li-ion): optimal <25°C, acceptable <35°C, warning >45°C, critical >55°C
  - Unknown: conservative limits matching NMC
- **Longevity Advisor** — generates actionable tips for extending equipment lifespan:
  - Battery: temperature placement advice, cycle tracking, SoH trend monitoring
  - Inverter: ventilation tips, mounting location advice
  - PV: string imbalance, isolation trend monitoring
- **3 Longevity Sensor Entities**:
  - Batterie Langlebigkeit (battery temp assessment + chemistry-specific tips)
  - Wechselrichter Langlebigkeit (controller temp assessment + ventilation tips)
  - PV-Anlage Langlebigkeit (string health + cabling tips)

### Changed
- **Version** bumped from 2.7.0 to 2.8.0.

## [2.7.0] - 2026-02-25

### Added
- **Smart Diagnostics Engine** — per-area diagnosis with human-readable status and actionable recommendations for each subsystem.
- **5 Diagnostic Area Sensors** — one per subsystem, each showing status (ok/hinweis/warnung/kritisch) with `title`, `detail`, `action` attributes:
  - **Diagnose: DC Solaranlage** — MC4 stecker, string imbalance, cable damage, shading/soiling detection with specific recommendations.
  - **Diagnose: AC Netzanbindung** — phase voltage, frequency, power factor with grid operator contact advice.
  - **Diagnose: Batterie** — temperature, SoH degradation, thermal runaway precursors with evacuation instructions for emergencies.
  - **Diagnose: Wechselrichter** — controller temperature, active errors, communication quality with ventilation/service advice.
  - **Diagnose: Sicherheit** — isolation resistance, fire risk, cable damage with inspection recommendations.

### Changed
- **Reduced INFO spam** — INFO thresholds raised to reduce unnecessary notifications:
  - Controller temperature INFO: 55°C → 62°C (normal summer operation)
  - Battery temperature INFO: 35°C → 38°C (normal during charging)
  - Grid frequency INFO: ±0.2Hz → ±0.3Hz (normal grid variation)
  - Phase voltage INFO: 210-250V → 207-253V (matches EN 50160 standard)
- **Version** bumped from 2.6.0 to 2.7.0.

## [2.6.0] - 2026-02-25

### Added
- **Inverter Health Monitoring System** — tracks 21 parameters with 3-level thresholds (INFO → WARNING → CRITICAL) for long-term health assessment.
- **Health Score** sensor (0-100%) — overall system health derived from all monitored parameters.
- **Parameter tracking** — isolation resistance, controller/battery temperature, battery SoH/cycles/voltage/capacity, grid frequency, phase voltages (1-3), DC string voltages/powers (1-3), cos φ, EVU power limit, active error/warning counts.
- **Trend detection** — rising/stable/falling trend for every parameter based on historical samples. Enables early degradation detection.
- **DC String Imbalance** sensor — detects shading, soiling, or defective panels by comparing string powers (>30% deviation = alert).
- **Phase Voltage Imbalance** sensor — detects grid-side problems from voltage differences between L1/L2/L3.
- **Inverter State Change Counter** — frequent state changes indicate instability.
- **11 Binary Warning Sensors** — isolation, controller overheat, battery health, battery temperature, grid frequency, phase 1/2/3 voltage, DC imbalance, error rate, active errors. All usable as HA automation triggers.
- **PV Fire Safety Early Warning System** — software-based hazard detection (NOT a replacement for AFCI/smoke detectors).
  - **Isolation fault detection** — rapid or gradual drop in isolation resistance (cable damage, water ingress, rodent/bird damage). <50kΩ = EMERGENCY, <100kΩ = HIGH.
  - **DC arc fault indicators** — sudden string power drop or fluctuation while others are normal (loose MC4, damaged cable).
  - **Battery thermal runaway precursors** — temperature >60°C = EMERGENCY, rapid rise >2°C/5min = ELEVATED, voltage anomaly during high temp = cell imbalance warning.
  - **Controller overheating** — PCB >85°C = HIGH, rapid rise >3°C/5min = ELEVATED.
  - **Grid emergency** — frequency ±1.5Hz or voltage >270V/<180V = HIGH.
  - **5 risk levels**: SAFE → MONITOR → ELEVATED → HIGH → EMERGENCY.
  - **Fire safety entities**: Fire Risk Level sensor, Active Safety Alerts counter, PV System Safety (BinarySensor SAFETY class), Isolation Fault Danger, Battery Fire Risk, DC Cable Danger.

### Changed
- **Health thresholds adjusted** — Controller: 55°C info / 70°C warning / 80°C critical. Battery: 35°C info / 45°C warning / 55°C critical. Isolation in kΩ display.
- **Version** bumped from 2.5.0 to 2.6.0.

### Note
The fire safety system is a **software monitoring aid**, NOT a certified fire protection system. It does NOT replace physical safety devices (AFCI, smoke detectors, RCD/GFCI, thermal fuses).

## [2.5.0] - 2026-02-25

### Added
- **Modbus TCP Client** — direct Modbus-TCP connection to the inverter (port 1502) with async I/O, configurable endianness (auto/little/big), and automatic retry for transient faults.
- **Complete Modbus Register Map** — 90+ registers from official Kostal MODBUS-TCP/SunSpec documentation covering device info, power monitoring, phases, DC strings, battery management, G3 limitation, I/O board, and energy totals.
- **MQTT Proxy Bridge** — publishes all Modbus register values to MQTT so external systems (evcc, iobroker, Node-RED) can read inverter data without their own Modbus connection. Accepts write commands via MQTT command topics.
- **Simplified Proxy Topics** for evcc/iobroker: `proxy/pv_power`, `proxy/grid_power`, `proxy/battery_power`, `proxy/battery_soc`, `proxy/home_power`, `proxy/inverter_state` with corresponding `proxy/command/*` write topics.
- **Battery Charge Power Control** — number entity for register 1034 (DC charge power setpoint, -20kW to +20kW). Negative = charge, positive = discharge. Power limits read dynamically from inverter register 531.
- **Battery Management Entities** — Max Charge/Discharge Limits, Min/Max SoC, Active Power Setpoint, G3 Max Charge/Discharge Power.
- **G3 Cyclic Keepalive** — registers 1280/1282 are automatically re-written at `fallback_time/2` intervals to prevent fallback activation, matching the Kostal requirement for cyclic writes.
- **Modbus Connection Test** — two-step options flow: configure settings → automatic connection test (reads product name, serial, state, max power, battery mgmt mode) before saving. Shows clear error report on failure.
- **Reset Modbus Registers Button** — button entity in the HA UI to clear suppressed registers after firmware updates or inverter replacement.
- **Options Flow (GUI)** — configure Modbus TCP (enable, port, unit-id, endianness) and MQTT bridge directly in HA UI under integration settings.
- **`pymodbus>=3.6`** added as dependency for Modbus-TCP communication.
- **`PROXY_SETUP.md`** — documentation with evcc and iobroker MQTT configuration examples.

### Changed
- **pyright compliance** — resolved all 28 pyright errors for full Platinum standard compliance (mypy + pyright both zero errors).
- **Version** bumped from 2.4.1 to 2.5.0.

### Security
- **Defense-in-depth write validation** — NaN/Infinity blocked at 3 layers (entity, coordinator, client). Value range checked before every Modbus write. Integer overflow caught and translated to meaningful errors.
- **Active Power Setpoint** min changed from 0 to 1 (per Kostal docs range 1..100; writing 0 could disable inverter output).
- **Min SoC floor** raised from 0% to 5% to prevent deep battery discharge.
- **MQTT admin register protection** — `modbus_enable`, `unit_id`, `byte_order` excluded from MQTT command topics to prevent remote lockout.
- **MQTT rate limiting** — max 1 write per register per second, command serialization via asyncio lock, source tracking on every write.
- **Read-back verification** — registers are read back after write; mismatches logged as warnings.
- **Battery management mode check** — register 1080 is read at setup; warning logged if external Modbus control is not enabled on the inverter.
- **Register 1024** access corrected from R/W to R/O per Kostal documentation.

### Robustness
- **Classified Modbus exceptions** — ILLEGAL_FUNCTION (01), ILLEGAL_DATA_ADDRESS (02), ILLEGAL_DATA_VALUE (03) are permanent errors (no retry). SERVER_DEVICE_FAILURE (04), SERVER_DEVICE_BUSY (06) are transient (retry with backoff up to 5 times).
- **Strike system for unavailable registers** — registers returning ILLEGAL_DATA_ADDRESS are not permanently deleted but suppressed after 3 strikes with auto-expiring cooldown. Handles firmware updates adding new registers.
- **Auto-reconnect** — TCP connection loss triggers reconnect + endianness re-detection + retry.
- **Per-operation timeout** (5s) prevents hanging on unresponsive inverters.
- **Per-register error handling** — coordinator only marks integration as failed if ALL fast-poll registers fail, not on individual errors.

## [2.4.1] - 2026-02-14

### Changed
- Clarified `BatteryEfficiency` description as a hybrid metric (Discharge DC / Charge DC+AC).

### Fixed
- Corrected `BatteryDischargeTotal` calculation to use pure AC values (`HomeBat` + `DischargeGrid`) for consistency.
- Reached 100% test coverage by adding missing test case for `select` validation error.

### Removed
- Removed redundant `BatteryEfficiencyPvOnly` sensor (mathematically identical to `BatteryEfficiency`).
- Removed redundant `GridChargeEfficiency` sensor (mathematically identical to `BatteryNetEfficiency`).

## [2.4.0] - 2026-02-14

### Added
- **Repair issues system** — persistent HA repair notifications for `auth_failed`, `api_unreachable`, `inverter_busy`, `installer_required`.
- **Stale device removal** — `async_remove_config_entry_device` allows HA to clean up orphaned devices.
- **Auto-clear `inverter_busy`** — repair issue is automatically dismissed on successful API communication.
- **Translated write errors** — `HomeAssistantError` with `translation_domain`/`translation_key` provides UI feedback on failed write operations.
- **Select coordinator per-entity tracking** — `_fetch` stores options per `data_id` to prevent overwriting when multiple select entities share a module.
- **`const.py`: centralised `AddConfigEntryEntitiesCallback`** — single location for the HA version-dependent import, removing try/except boilerplate from all platform files.

### Changed
- **pykoplenti** bumped from `1.3.0` to `1.5.0`.
- **`coordinator.py`: removed `RequestCache`** — the HMAC-based deduplication cache was removed in favour of the coordinator's native deduplication. Reduces complexity and CPU overhead.
- **`coordinator.py`: `SettingDataUpdateCoordinator` 503 fallback** — returns `_last_result` on transient inverter-busy errors to keep entities available.
- **`coordinator.py`: simplified `Plenticore.async_unload`** — removed fragile `hass.state` string check; uses `remove_listener` sentinel instead.
- **`helper.py`: Modbus exceptions centralised** — all `ModbusException` subclasses and `parse_modbus_exception` live in `helper.py` (moved from `coordinator.py`).
- **Quality Scale** self-assessment updated to Platinum (all rules done/exempt).

### Fixed
- **Select coordinator fetch overwrite** — previously, registering a second select entity would overwrite the first entity's options, causing state loss.
- **Logout during shutdown** — `async_unload` no longer attempts logout when called from `EVENT_HOMEASSISTANT_STOP`, preventing timeout errors.

### Removed
- **`RequestCache` class** — replaced by coordinator-level deduplication.
- **`hmac` import** — no longer needed after cache simplification.
- **Debug/verification scripts** from test suite (`debug_flow_check.py`, `debug_schema.py`, `verify_calculated_sensor_*.py`, `test_debug_integration_path.py`).

---

## [2.3.3] - 2026-02-05

### Added
- Repair issue for missing installer/service code when advanced controls are used.

---

## [2.3.2] - 2026-01-08

### Fixed
- REST ID mapping for G3 battery limits and AC charge power (PLENTICORE G3 L).
- G3 fallback limit/time settings now use REST `Battery:Limit:*` identifiers.

---

## [2.3.1] - 2026-01-07

### Fixed
- Rate-limit refresh no longer double-delays.
- Initial refresh debounce lowered from 2s to 0.5s.

### Changed
- Shadow Management detection now logs only expected API/network errors.
- `RequestCache` key generation simplified to a deterministic hash.
- Shared installer-access validation centralised between numbers and switches.

---

## [0.1.0] - 2025-12-01

### Added
- Initial public release.
- Home Assistant config flow with reauth support.
- Sensors, numbers, switches, and selects aligned with REST API discovery.
- Battery efficiency and total energy calculated sensors for the Energy Dashboard.
- Repair issues for common errors (auth, API unreachable, inverter busy).
- Strict typing (mypy strict) applied to integration core.
- Test suite with full coverage for core modules and key flows.
