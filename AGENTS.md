# AGENTS.md

## Cursor Cloud specific instructions

### Project overview
Home Assistant custom integration for Kostal solar inverters (`kostal_kore/` package mapped from `custom_components/kostal_kore/`). Pure Python, no Docker/DB/external services needed. All hardware interactions are mocked in tests.

### Test directory case sensitivity
The test directory is `Tests/` (uppercase T) but `pytest.ini` references `testpaths = tests` (lowercase). On Linux (case-sensitive) a symlink `tests -> Tests` must exist at the workspace root. The update script creates this automatically.

### Running tests
```bash
source .venv/bin/activate
python -m pytest Tests/ -v --timeout=60
```
The suite currently collects about 648 tests. Coverage config (`.coveragerc`) omits many platform files from branch-coverage counting (see the file for full list). `pytest.ini` enforces `--cov-fail-under=100`; all measured files must reach 100% branch coverage.

### Type checking
```bash
source .venv/bin/activate
python -m mypy custom_components/kostal_kore/
```
Uses strict mode per `mypy.ini`. Should report zero issues.

### Key dependencies
- `pykoplenti` is vendored at `pykoplenti-master/` and installed in editable mode.
- `pytest-homeassistant-custom-component` provides a mocked Home Assistant runtime for tests.
- No lockfile exists; `requirements_test.txt` lists unpinned test deps.

### CI Python versions
CI (`.github/workflows/ci.yml`) runs the test + mypy job on a Python matrix: **3.14** — the Python version current Home Assistant requires (HA pins one Python minor at a time per ADR-0020; 3.14 since HA 2026.3) — and **3.12** — our manifest floor (`homeassistant: 2024.12.0`). The 3.14 leg keeps the smoke tests aligned with the runtime real users are on; **bump it whenever Home Assistant raises its minimum Python**. `pytest-homeassistant-custom-component` is unpinned, so the 3.12 leg resolves an older HA build (floor coverage) while 3.14 pulls current HA.

### Notes
- This is not a standalone application. It is a Home Assistant plugin tested entirely via pytest with mocked HA internals.
- **Battery-control coexistence**: KORE is single-inverter and drives the battery over Modbus (REG 1034/1038, G3 1280/1282) with a 15s keepalive, arbitrated only against itself and external Modbus clients — NOT against the inverter's own firmware control. Native Smart AC Charge (default-on since FW 3.05), native scheduling/dynamic-tariff modes, MDC battery control (FW 3.06.10+), and EEBus can all fight KORE. Items needing real-hardware validation before further coexistence work are tracked in `HARDWARE_VALIDATION_TODO.md`.
- No GUI, no dev server, no build step. The "hello world" is running the test suite successfully.
- `main` is the default branch; all work lands via pull requests into `main` and releases are tagged from it. (`develop` is historical/stale — do not target it.)
- `pymodbus>=3.6` is listed in `manifest.json` but not in `requirements_test.txt`; the update script installs it separately.
- System package `python3.12-venv` is required on Ubuntu (not pre-installed on cloud VMs).
