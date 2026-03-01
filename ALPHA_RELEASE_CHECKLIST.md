# KOSTAL KORE Experimental Alpha - Release Checklist

## HACS publication checklist

- [x] Repository structure uses `custom_components/kostal_kore`.
- [x] Root `hacs.json` exists.
- [x] Integration `manifest.json` contains `domain`, `name`, `version`, `documentation`, `issue_tracker`, `codeowners`.
- [x] `LICENSE` file is present (MIT).
- [x] README includes HACS custom-repository installation flow.
- [x] README includes clear alpha warning and issue-reporting path.
- [x] CI workflows exist for HACS validation and test/type checks.

## Home Assistant quality/platinum self-check

- [x] Config flow and options flow available.
- [x] Strict typing (`mypy`) enabled and clean.
- [x] Diagnostics and repair flows available.
- [x] Entity unique IDs and categories are present.
- [x] Test suite and quality-scale metadata are included.

## Security hardening implemented for alpha

- [x] Modbus proxy defaults to `127.0.0.1` bind.
- [x] Installer/service code required for battery-control writes via MQTT and Modbus proxy.
- [x] Proxy FC16 arbitration checks full write-range overlap for protected registers.
- [x] Grid diagnostics/safety thresholds adapt to detected 50/60 Hz and 120/230 V profiles.
- [x] Runtime control paths avoid fixed 20 kW restore values and use inverter-aware caps.

## Migration readiness

- [x] Two-step routine exists: migrate first, cleanup old legacy entry later.

## Known limitations (alpha)

- Some teardown errors in `Tests/test_modbus_integration.py` are pre-existing lingering-timer issues.
- The integration is production-oriented but still marked alpha to gather broad hardware feedback (G1/G2/G3, 1-20 kW classes, regional grids).

## Feedback policy

- Report issues at: <https://github.com/Puma7/Kostal/issues>
- Include:
  - inverter generation/model
  - inverter size class (e.g. 1 kW, 3 kW, 5 kW, 20 kW)
  - grid profile (50/60 Hz)
  - diagnostics payload / logs
