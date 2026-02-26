# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
