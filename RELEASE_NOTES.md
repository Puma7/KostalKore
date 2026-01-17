# Release Notes

## Unreleased

- Performance: rate-limit refresh no longer double-delays; initial refresh debounce lowered to 0.5s.

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
