"""Tests for Kostal Plenticore model compatibility (G1, G2, G3)."""
from unittest.mock import MagicMock
from pykoplenti import SettingsData, ProcessData
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

pytestmark = [
    pytest.mark.usefixtures("mock_plenticore_client"),
]

# Define all valid hardware combinations
# Plenticore supports max 3 PV strings.
# Battery is optional.
SCENARIOS = [
    ("1PV_NoBat", 1, False),
    ("1PV_WithBat", 1, True),
    ("2PV_NoBat", 2, False),
    ("2PV_WithBat", 2, True),
    ("3PV_NoBat", 3, False),
    ("3PV_WithBat", 3, True),
]

@pytest.mark.parametrize("name, string_count, has_battery", SCENARIOS)
async def test_hardware_configurations(
    hass: HomeAssistant,
    mock_get_settings: dict[str, list[SettingsData]],
    mock_get_setting_values: dict[str, dict[str, str]],
    mock_get_process_data_values: dict[str, dict[str, str]],
    mock_get_process_data: dict[str, list[str]],
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    name: str,
    string_count: int,
    has_battery: bool,
) -> None:
    """Test all valid hardware permutations (1-3 Strings, +/- Battery)."""
    
    # 1. Configure String Count
    mock_get_setting_values["devices:local"].update({
        "Properties:StringCnt": str(string_count),
        "Properties:VersionIOC": "01.45.00000",
    })

    # 2. Configure Battery Presence
    if has_battery:
        # Battery exists in settings and process data
        mock_get_setting_values["devices:local"]["Battery:MinSoc"] = "10"
        mock_get_process_data_values["devices:local:battery"] = {
            "P": "-500", "SoC": "80", "Cycles": "50"
        }
    else:
        # Remove battery traces
        if "devices:local:battery" in mock_get_process_data:
            del mock_get_process_data["devices:local:battery"]
        
        # Remove battery settings
        keys_to_remove = [k for k in mock_get_setting_values["devices:local"] if "Battery" in k]
        for k in keys_to_remove:
            del mock_get_setting_values["devices:local"][k]

    # 3. Configure PV Data for existence
    for i in range(1, 4):
        key = f"devices:local:pv{i}"
        if i <= string_count:
            # String should exist
            mock_get_process_data_values[key] = {"P": "1000", "U": "400", "I": "2.5"}
        else:
            # String should NOT exist
            if key in mock_get_process_data_values:
                del mock_get_process_data_values[key]

    # Run Setup
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Verify Battery Entity
    battery_sensor = entity_registry.async_is_registered("sensor.scb_battery_soc")
    if has_battery:
        assert battery_sensor, f"Scenario {name}: Battery sensor missing"
    else:
        assert not battery_sensor, f"Scenario {name}: Battery sensor should not exist"

    # Verify PV Entities
    for i in range(1, 4):
        pv_sensor = entity_registry.async_is_registered(f"sensor.scb_dc{i}_power")
        if i <= string_count:
            assert pv_sensor, f"Scenario {name}: PV{i} sensor missing"
        else:
            assert not pv_sensor, f"Scenario {name}: PV{i} sensor should not exist"

async def test_fallback_logic(
    hass: HomeAssistant,
    mock_get_setting_values: dict[str, dict[str, str]],
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test fallback: If string count query fails, assume 1 string."""
    # Simulate failed fetch by removing the key
    if "Properties:StringCnt" in mock_get_setting_values["devices:local"]:
        del mock_get_setting_values["devices:local"]["Properties:StringCnt"]

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Should default to 1 string
    assert entity_registry.async_is_registered("sensor.scb_dc1_power")
    assert not entity_registry.async_is_registered("sensor.scb_dc2_power")
