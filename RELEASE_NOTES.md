# Release Notes

## Unreleased

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
