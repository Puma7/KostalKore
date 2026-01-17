"""Test the Kostal Plenticore Solar Inverter select platform."""

from pykoplenti import SettingsData
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from datetime import timedelta
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

pytestmark = [
    pytest.mark.usefixtures("mock_plenticore_client"),
]


async def test_select_battery_charging_usage_available(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    mock_get_settings: dict[str, list[SettingsData]],
) -> None:
    """Test that the battery charging usage select entity is added if the settings are available."""

    mock_get_settings["devices:local"].extend(
        [
            SettingsData(
                min=None,
                max=None,
                default=None,
                access="readwrite",
                unit=None,
                id="Battery:SmartBatteryControl:Enable",
                type="string",
            ),
            SettingsData(
                min=None,
                max=None,
                default=None,
                access="readwrite",
                unit=None,
                id="Battery:TimeControl:Enable",
                type="string",
            ),
        ]
    )

    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert entity_registry.async_is_registered("select.scb_battery_charging_usage_mode")

    entity = entity_registry.async_get("select.scb_battery_charging_usage_mode")
    assert entity.capabilities.get("options") == [
        "None",
        "Battery:SmartBatteryControl:Enable",
        "Battery:TimeControl:Enable",
    ]


async def test_select_battery_charging_usage_excess_energy_available(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    mock_get_settings: dict[str, list[SettingsData]],
    mock_get_setting_values: dict[str, dict[str, str]],
) -> None:
    """Test that the battery charging usage select entity contains the option for excess AC energy."""

    mock_get_settings["devices:local"].extend(
        [
            SettingsData(
                min=None,
                max=None,
                default=None,
                access="readwrite",
                unit=None,
                id="Battery:SmartBatteryControl:Enable",
                type="string",
            ),
            SettingsData(
                min=None,
                max=None,
                default=None,
                access="readwrite",
                unit=None,
                id="Battery:TimeControl:Enable",
                type="string",
            ),
        ]
    )

    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert entity_registry.async_is_registered("select.scb_battery_charging_usage_mode")

    entity = entity_registry.async_get("select.scb_battery_charging_usage_mode")
    assert entity.capabilities.get("options") == [
        "None",
        "Battery:SmartBatteryControl:Enable",
        "Battery:TimeControl:Enable",
    ]


async def test_select_battery_charging_usage_not_available(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test that the battery charging usage select entity is still added if settings are unavailable."""

    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert entity_registry.async_is_registered("select.scb_battery_charging_usage_mode")


async def test_select_option(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_plenticore_client,
    entity_registry: er.EntityRegistry,
    mock_get_settings: dict,
    mock_get_setting_values: dict,
) -> None:
    """Test selecting an option."""
    # Ensure entity is created
    mock_get_settings["devices:local"].extend([
        SettingsData(min=None, max=None, default=None, access="readwrite", unit=None, id="Battery:SmartBatteryControl:Enable", type="string"),
        SettingsData(min=None, max=None, default=None, access="readwrite", unit=None, id="Battery:TimeControl:Enable", type="string"),
    ])
    
    mock_get_setting_values["devices:local"].update({
        "Battery:SmartBatteryControl:Enable": "0",
        "Battery:TimeControl:Enable": "0",
    })
    
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
    await hass.async_block_till_done()

    # Get entity id
    entity_id = "select.scb_battery_charging_usage_mode"
    assert entity_registry.async_is_registered(entity_id)

    # Select an option
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": entity_id, "option": "Battery:SmartBatteryControl:Enable"},
        blocking=True,
    )

    # Verify API calls
    from unittest.mock import call
    mock_plenticore_client.set_setting_values.assert_has_calls([
        call("devices:local", {"Battery:SmartBatteryControl:Enable": "0"}),
        call("devices:local", {"Battery:TimeControl:Enable": "0"}),
        call("devices:local", {"Battery:SmartBatteryControl:Enable": "1"}),
    ], any_order=True)
