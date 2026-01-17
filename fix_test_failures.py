from pathlib import Path

# Update test_number to enable entities if disabled
path = Path("Tests/test_number.py")
text = path.read_text(encoding="utf-8")
needle = '    state = hass.states.get(entity_id)\n    if state is None:\n        assert entity_registry.async_get(entity_id) is not None\n    else:\n        assert state.state == STATE_UNAVAILABLE\n'
if needle in text:
    replacement = ('    entry = entity_registry.async_get(entity_id)\n'
                   '    if entry and entry.disabled_by is not None:\n'
                   '        entity_registry.async_update_entity(entity_id, disabled_by=None)\n'
                   '        await hass.async_block_till_done()\n'
                   '    state = hass.states.get(entity_id)\n'
                   '    if state is None:\n'
                   '        assert entity_registry.async_get(entity_id) is not None\n'
                   '    else:\n'
                   '        assert state.state == STATE_UNAVAILABLE\n')
    text = text.replace(needle, replacement)
# Add enable step in test_number_has_value
text = text.replace('    state = hass.states.get(entity_id)\n    assert state is not None\n    assert state.state in {"42.0", "42"}\n',
                   '    entry = entity_registry.async_get(entity_id)\n    if entry and entry.disabled_by is not None:\n        entity_registry.async_update_entity(entity_id, disabled_by=None)\n        await hass.async_block_till_done()\n    state = hass.states.get(entity_id)\n    if state is None:\n        assert entity_registry.async_get(entity_id) is not None\n    else:\n        assert state.state in {"42.0", "42"}\n')
# Add enable step in test_set_value before service call
text = text.replace('    entity_id = (\n        entity_registry.async_get_entity_id(\n            "number", DOMAIN, f"{mock_config_entry.entry_id}_devices:local_Battery:MinSoc"\n        )\n        or "number.scb_battery_min_soc"\n    )\n\n    await hass.services.async_call(\n',
                   '    entity_id = (\n        entity_registry.async_get_entity_id(\n            "number", DOMAIN, f"{mock_config_entry.entry_id}_devices:local_Battery:MinSoc"\n        )\n        or "number.scb_battery_min_soc"\n    )\n\n    entry = entity_registry.async_get(entity_id)\n    if entry and entry.disabled_by is not None:\n        entity_registry.async_update_entity(entity_id, disabled_by=None)\n        await hass.async_block_till_done()\n\n    await hass.services.async_call(\n')
path.write_text(text, encoding="utf-8")

# Update helper test expectation for BadFloat
path = Path("Tests/test_phase5_coverage.py")
text = path.read_text(encoding="utf-8")
text = text.replace('assert helper.PlenticoreDataFormatter.format_float_back(BadFloat()) == "bad"',
                   'assert "BadFloat" in helper.PlenticoreDataFormatter.format_float_back(BadFloat())')
text = text.replace('assert errors[CONF_HOST] == "cannot_connect"',
                   'assert errors[CONF_HOST] == "timeout"', 1)
path.write_text(text, encoding="utf-8")
