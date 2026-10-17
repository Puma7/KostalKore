"""A numeric sensor cannot publish old feedback when new text is unparsable."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.kostal_kore.coordinator import ProcessDataUpdateCoordinator
from custom_components.kostal_kore.sensor import SENSOR_PROCESS_DATA, PlenticoreDataSensor
from Tests.test_third_review_communication import coordinator


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["broken", "--", "?"])
async def test_numeric_feedback_unavailable_instead_of_fresh_cached_value(tmp_path, bad):
    hass = HomeAssistant(str(tmp_path))
    description = next(item for item in SENSOR_PROCESS_DATA if item.key == "SoC")
    owner = SimpleNamespace(hass=None, client=SimpleNamespace(get_process_data_values=AsyncMock()))
    item = coordinator(ProcessDataUpdateCoordinator, hass, owner)
    item._fetch[description.module_id] = [description.key]
    sensor = PlenticoreDataSensor(item, description, "entry", "test", {})

    for raw, available in [("20", True), (bad, False), (bad, False), ("0", True), ("25.5", True)]:
        owner.client.get_process_data_values.return_value = {
            description.module_id: {description.key: SimpleNamespace(value=raw)}}
        item.async_set_updated_data(await item._async_update_data())
        assert sensor.available is available
        if available:
            assert sensor.native_value is not None
