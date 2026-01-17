from pathlib import Path
path = Path("Tests/test_phase5_coverage.py")
text = path.read_text(encoding="utf-8")
text = text.replace('errors = config_flow._handle_config_flow_error(ClientError("x"), "net")\n    assert errors[CONF_HOST] == "timeout"', 'errors = config_flow._handle_config_flow_error(ClientError("x"), "net")\n    assert errors[CONF_HOST] == "cannot_connect"')
text = text.replace('side_effect=[0.0, 10.0])', 'side_effect=[0.0, 10.0, 10.0])')
path.write_text(text, encoding="utf-8")
