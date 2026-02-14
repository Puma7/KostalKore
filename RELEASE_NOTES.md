# Release Notes

## v2.4.0

Major quality and robustness release.

- **Repair Issues System**: HA repair notifications for auth failures, API errors, inverter busy, and missing installer code.
- **Stale Device Removal**: Orphaned devices can now be removed from the device registry.
- **Auto-Recovery**: `inverter_busy` repair issue auto-clears on successful communication.
- **UI Feedback**: Failed write operations now show translated error messages in the UI.
- **Select Fix**: Multiple select entities no longer overwrite each other's state.
- **Code Simplification**: Removed `RequestCache` and HMAC-based caching in favour of coordinator-native deduplication.
- **Dependency**: pykoplenti bumped to 1.5.0.

## v2.3.3

- Added: Repair issue for missing installer/service code when advanced controls are used.

## v2.3.2

- Fixed REST ID mapping for G3 battery limits and AC charge power (PLENTICORE G3 L).
- G3 fallback limit/time settings now use REST `Battery:Limit:*` identifiers.

## v2.3.1

- Performance: rate-limit refresh no longer double-delays; initial refresh debounce lowered to 0.5s.
- Reliability: Shadow Management detection now logs only expected API/network errors.
- Performance: RequestCache key generation simplified to a deterministic hash.
- Maintenance: Shared installer-access validation centralized between numbers and switches.

## v0.1.0

Initial public release of the integration.

Highlights:
- Home Assistant config flow with reauth support.
- Sensors, numbers, switches, and selects aligned with REST API discovery.
- Battery efficiency and total energy calculated sensors for the Energy Dashboard.
- Repair issues for common errors (auth, API unreachable, inverter busy).
- Strict typing applied in the integration core (mypy strict).
- Test suite with full coverage for core modules and key flows.

Notes:
- This release targets the REST API via `pykoplenti`.
- Use `develop` for ongoing work; `main` tracks tagged releases.
