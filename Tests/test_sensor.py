"""Test the Kostal Plenticore Solar Inverter sensor platform."""

from unittest.mock import Mock

import pytest
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from datetime import timedelta

from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

pytestmark = [
    pytest.mark.usefixtures("mock_plenticore_client"),
]

@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_setup_sensors(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test if all available sensors are setup."""
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Check some basic sensors
    assert entity_registry.async_get("sensor.scb_inverter_state") is not None
    assert entity_registry.async_get("sensor.scb_solar_power") is not None
    assert entity_registry.async_get("sensor.scb_grid_power") is not None
    assert entity_registry.async_get("sensor.scb_home_power_from_battery") is not None

@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_sensor_values(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_get_process_data_values: dict[str, dict[str, str]],
) -> None:
    """Test if sensors have the correct values."""
    mock_get_process_data_values["devices:local"].update({
        "Inverter:State": "6",
        "Dc_P": "5000.6",
        "Grid_P": "250.2",
        "HomeBat_P": "0.1",
    })

    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Trigger update
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()

    assert hass.states.get("sensor.scb_inverter_state").state == "FeedIn"
    assert hass.states.get("sensor.scb_solar_power").state == "5001" # rounded
    assert hass.states.get("sensor.scb_grid_power").state == "250" # rounded
    assert hass.states.get("sensor.scb_home_power_from_battery").state == "0" # rounded

@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_pv3_sensors(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
    mock_get_setting_values: dict[str, dict[str, str]],
) -> None:
    """Test if PV3 sensors are created when string count is 3."""
    mock_get_setting_values["devices:local"]["Properties:StringCnt"] = "3"
    
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Note: sensor names might depend on implementation, usually dc3_power or similar
    # We check for dc3 power if it's in SENSOR_PROCESS_DATA with module devices:local and key Dc3_P
    # Actually sensor.scb_dc3_power is likely correct.
    assert entity_registry.async_get("sensor.scb_dc3_power") is not None

@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_sensor_unavailable(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_plenticore_client: Mock,
) -> None:
    """Test if sensors become unavailable when update fails."""
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Mock failure of the data update
    mock_plenticore_client.get_process_data_values.side_effect = Exception("API error")

    # Trigger update
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()

    assert hass.states.get("sensor.scb_solar_power").state == STATE_UNAVAILABLE


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_efficiency_calculated_sensors(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_get_process_data: dict[str, list[str]],
    mock_get_process_data_values: dict[str, dict[str, str]],
) -> None:
    """Test calculated efficiency sensor values."""
    mock_get_process_data["scb:statistic:EnergyFlow"] = [
        "Statistic:EnergyChargePv:Total",
        "Statistic:EnergyChargeGrid:Total",
        "Statistic:EnergyDischarge:Total",
        "Statistic:EnergyDischargeGrid:Total",
        "Statistic:EnergyHomeBat:Total",
    ]
    mock_get_process_data_values["scb:statistic:EnergyFlow"] = {
        "Statistic:EnergyChargePv:Total": "10",
        "Statistic:EnergyChargeGrid:Total": "5",
        "Statistic:EnergyDischarge:Total": "12",
        "Statistic:EnergyDischargeGrid:Total": "3",
        "Statistic:EnergyHomeBat:Total": "9",
    }

    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()

    # BatteryEfficiency: Discharge / (ChargePv + ChargeGrid) = 12/15 = 80%
    assert hass.states.get("sensor.scb_battery_efficiency_total").state == "80"
    # BatteryEfficiencyPvOnly: pv_share=10/15, out=12*10/15=8, 8/10=80%
    assert hass.states.get("sensor.scb_battery_efficiency_pv_only_total").state == "80"
    # BatteryNetEfficiency: (HomeBat + DischargeGrid) / (ChargePv + ChargeGrid) = (9+3)/15 = 80%
    assert hass.states.get("sensor.scb_battery_net_efficiency_total").state == "80"
    # InverterDischargeEfficiency: (HomeBat + DischargeGrid) / Discharge = (9+3)/12 = 100%
    assert hass.states.get("sensor.scb_inverter_discharge_efficiency_total").state == "100"
    # GridChargeEfficiency: grid_share=5/15, ac_out=12*5/15=4, 4/5=80%
    assert hass.states.get("sensor.scb_grid_charge_efficiency_total").state == "80"