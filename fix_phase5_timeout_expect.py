from pathlib import Path
path = Path("Tests/test_phase5_coverage.py")
text = path.read_text(encoding="utf-8")
text = text.replace('assert errors[CONF_HOST] == "cannot_connect"', 'assert errors[CONF_HOST] == "timeout"', 1)
path.write_text(text, encoding="utf-8")
