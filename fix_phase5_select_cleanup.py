from pathlib import Path
path = Path("Tests/test_phase5_coverage.py")
text = path.read_text(encoding="utf-8")
marker = '    select_entity.coordinator.async_request_refresh = AsyncMock(side_effect=RuntimeError("boom"))\n    select_entity.coordinator.async_write_data = AsyncMock(return_value=True)\n    await select_entity.async_added_to_hass()\n'
if marker in text:
    replacement = marker + '    select_entity.async_write_ha_state = MagicMock()\n    select_entity.hass = hass\n'
    text = text.replace(marker, replacement)
path.write_text(text, encoding="utf-8")
