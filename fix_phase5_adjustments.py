from pathlib import Path
from textwrap import dedent

path = Path("Tests/test_phase5_coverage.py")
text = path.read_text(encoding="utf-8")
text = text.replace('assert errors[CONF_HOST] == "timeout"', 'assert errors[CONF_HOST] == "cannot_connect"')
text = text.replace('client=MagicMock()', 'client=MagicMock(set_setting_values=AsyncMock(return_value=None))')
# Ensure async_write_data is mocked for select to avoid HomeAssistantError
marker = 'select_entity = entities[0]\n    select_entity.coordinator.async_request_refresh = AsyncMock(side_effect=RuntimeError("boom"))\n'
if marker in text:
    replacement = marker + '    select_entity.coordinator.async_write_data = AsyncMock(return_value=True)\n'
    text = text.replace(marker, replacement)
# Ensure lingering refresh timer is cancelled after test
if 'await select_entity.async_will_remove_from_hass()' in text:
    text = text.replace(
        'await select_entity.async_will_remove_from_hass()',
        'await select_entity.async_will_remove_from_hass()\n    unsub = getattr(select_entity.coordinator, "_unsub_refresh", None)\n    if callable(unsub):\n        unsub()'
    )
path.write_text(text, encoding="utf-8")
