"""PV subscriptions must follow the REST catalog, not battery-capable DC slots."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pykoplenti import ProcessData

from custom_components.kostal_kore.const import DOMAIN


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
@pytest.mark.parametrize("pv_inputs", [(1, 2), (1, 3), ()])
async def test_only_advertised_pv_inputs_are_polled(
    hass: HomeAssistant, mock_config_entry, mock_plenticore_client,
    mock_get_process_data, mock_get_setting_values, pv_inputs,
):
    """A third DC connector assigned to storage must not become a failed PV poll."""
    mock_get_setting_values.setdefault("devices:local", {})["Properties:StringCnt"] = "3"
    for number in (1, 2, 3):
        if number not in pv_inputs:
            mock_get_process_data.pop(f"devices:local:pv{number}", None)

    async def values(request):
        return {
            module: {
                key: ProcessData(id=key, unit="", value="1000" if module.startswith("devices:local:pv") and key == "P" else "0")
                for key in keys
            }
            for module, keys in request.items()
            if not module.startswith("devices:local:pv") or module in mock_get_process_data
        }

    mock_plenticore_client.get_process_data_values.side_effect = values
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["process_coordinator"]
    await coordinator.async_refresh()
    assert coordinator.communication_stale is False
    actual_modules = {module for module, keys in coordinator._fetch.items() if keys and module.startswith("devices:local:pv")}
    assert actual_modules == {f"devices:local:pv{number}" for number in pv_inputs}
    registry = er.async_get(hass)
    for entity in registry.entities.values():
        if entity.config_entry_id != mock_config_entry.entry_id:
            continue
        for number in {1, 2, 3} - set(pv_inputs):
            assert f"_devices:local:pv{number}_" not in entity.unique_id
    sum_entry = next(entity for entity in registry.entities.values()
                     if entity.config_entry_id == mock_config_entry.entry_id
                     and entity.unique_id.endswith("__virt__pv_P"))
    solar = hass.states.get(sum_entry.entity_id)
    assert solar is not None
    if pv_inputs:
        assert float(solar.state) == 1000 * len(pv_inputs)
    else:
        assert solar.state == "unavailable"

    if pv_inputs:
        # A genuinely advertised input disappearing later must still fault.
        missing_module = f"devices:local:pv{pv_inputs[0]}"
        original = mock_get_process_data.pop(missing_module)
        await coordinator.async_refresh()
        assert coordinator.communication_stale is True
        mock_get_process_data[missing_module] = original
        await coordinator.async_refresh()
        assert coordinator.communication_stale is False
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_failed_catalog_discovery_keeps_dc_count_fallback(
    hass: HomeAssistant, mock_config_entry, mock_plenticore_client, mock_get_setting_values,
):
    """An unavailable capability request must not silently remove all PV sensors."""
    mock_plenticore_client.get_process_data = AsyncMock(return_value={})
    mock_get_setting_values.setdefault("devices:local", {})["Properties:StringCnt"] = "2"
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["process_coordinator"]
    assert coordinator._fetch["devices:local:pv1"]
    assert coordinator._fetch["devices:local:pv2"]
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_unadvertised_pv_power_field_is_not_requested(
    hass: HomeAssistant, mock_config_entry, mock_plenticore_client,
    mock_get_process_data, mock_get_setting_values,
):
    mock_get_process_data["devices:local:pv2"] = ["U", "I"]
    mock_get_process_data.pop("devices:local:pv3")
    mock_get_setting_values.setdefault("devices:local", {})["Properties:StringCnt"] = "2"
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["process_coordinator"]
    assert set(coordinator._fetch["devices:local:pv2"]) == {"U", "I"}
    registry = er.async_get(hass)
    assert not any(entity.unique_id.endswith("_devices:local:pv2_P")
                   for entity in registry.entities.values())
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
