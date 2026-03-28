# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
