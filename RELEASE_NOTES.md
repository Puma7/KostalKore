# Release Notes

## v2.16.0-alpha.4 (Experimental)

This alpha update adds two-step migration support from the legacy plugin.

- Added **Import Legacy Plenticore Data** and **Finalize Legacy Cleanup** buttons.
- Added registry migration logic to preserve existing entity IDs/history where possible.
- Added migration test coverage (data/options import, host matching, duplicate handling).

> Alpha notice: Please report bugs via GitHub Issues with diagnostics attached.

## v2.16.0-alpha.2 (Experimental)

This alpha update focuses on setup UX, access control clarity, and branding alignment.

- Added a first-run setup wizard step to directly enable Modbus TCP, MQTT bridge, and Modbus proxy.
- Added best-effort local auto-discovery when host/IP is left empty during setup.
- Added account-role detection and explicit installer-write capability handling.
- Switched integration release domain/path branding to `kostal_kore`.
- Added plugin logo asset and integrated it into README.

> Alpha notice: Please report bugs via GitHub Issues with diagnostics attached.

## v2.16.0-alpha.1 (Experimental)

This is the first **experimental alpha release** for HACS rollout preparation.

- Added HACS release metadata (`hacs.json`), explicit MIT license, and updated manifest metadata.
- Hardened external write channels (Modbus proxy + MQTT bridge) with installer-access checks.
- Introduced proxy bind-address hardening (`127.0.0.1` default) to reduce network exposure.
- Replaced fixed high power defaults with inverter-aware dynamic limits.
- Added adaptive 50/60Hz and 120/230V diagnostic profiles for broader worldwide compatibility.
- Fixed `num_bidirectional` device-info polling to improve topology handling.

> Alpha notice: Please report bugs via GitHub Issues with diagnostics attached.

## v2.4.1

Hotfix for calculation accuracy and test coverage.

- **Fixed**: `BatteryDischargeTotal` now uses AC-side values (`HomeBat` + `DischargeGrid`) instead of mixing DC/AC.
- **Fixed**: `BatteryEfficiency` description is now accurate (hybrid DC/AC metric).
- **Removed**: Redundant `BatteryEfficiencyPvOnly` and `GridChargeEfficiency` sensors.
- **Improved**: Test coverage reached 100%.

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
