# AGENTS.md

## Cursor Cloud specific instructions

### Project overview
Home Assistant custom integration for Kostal Plenticore solar inverters (`kostal_plenticore/`). Pure Python, no Docker/DB/external services needed. All hardware interactions are mocked in tests.

### Test directory case sensitivity
The test directory is `Tests/` (uppercase T) but `pytest.ini` references `testpaths = tests` (lowercase). On Linux (case-sensitive) a symlink `tests -> Tests` must exist at the workspace root. The update script creates this automatically.

### Running tests
```bash
source .venv/bin/activate
python -m pytest Tests/ -v --timeout=60
```
All 138 tests should pass with 100% coverage. Coverage config (`.coveragerc`) omits `coordinator.py`, `number.py`, `sensor.py`, `switch.py` from branch-coverage counting.

### Type checking
```bash
source .venv/bin/activate
python -m mypy kostal_plenticore/
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
