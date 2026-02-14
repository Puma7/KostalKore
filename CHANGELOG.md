# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
