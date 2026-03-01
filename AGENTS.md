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
The suite currently collects about 412 tests. 3 known errors in `test_modbus_integration.py` (`test_setup_entry_modbus_enabled_success`, `test_setup_entry_modbus_auto_endianness`, `test_setup_entry_mqtt_bridge_enabled`) are pre-existing "Lingering timer" fixture teardown issues. Coverage config (`.coveragerc`) omits many platform files from branch-coverage counting (see the file for full list).

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

### Notes
- This is not a standalone application. It is a Home Assistant plugin tested entirely via pytest with mocked HA internals.
- No GUI, no dev server, no build step. The "hello world" is running the test suite successfully.
- `develop` is the default working branch; `main` reflects tagged releases.
- `pymodbus>=3.6` is listed in `manifest.json` but not in `requirements_test.txt`; the update script installs it separately.
- System package `python3.12-venv` is required on Ubuntu (not pre-installed on cloud VMs).
