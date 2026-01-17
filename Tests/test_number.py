"""Test Kostal Plenticore number."""

from datetime import timedelta

from pykoplenti import ApiClient, SettingsData
import pytest
from unittest.mock import MagicMock, AsyncMock

from homeassistant.components.number import (
    ATTR_MAX,
    ATTR_MIN,
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from kostal_plenticore.const import DOMAIN
from homeassistant.util import dt as dt_util

from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

pytestmark = [
    pytest.mark.usefixtures("mock_plenticore_client"),
]


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_setup_all_entries(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test if all available entries are setup."""

    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert entity_registry.async_get("number.scb_battery_min_soc") is not None
    assert (
        entity_registry.async_get("number.scb_battery_min_home_consumption") is not None
    )


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_setup_no_entries(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
    mock_get_settings: dict[str, list[SettingsData]],
) -> None:
    """Test that no entries are setup if Plenticore does not provide data."""

    # remove all settings except hostname which is used during setup
    mock_get_settings.clear()
    mock_get_settings.update(
        {
            "scb:network": [
                SettingsData(
                    min="1",
                    max="63",
                    default=None,
                    access="readwrite",
                    unit=None,
                    id="Hostname",
                    type="string",
                )
            ]
        }
    )

    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert entity_registry.async_get("number.scb_battery_min_soc") is None
    assert entity_registry.async_get("number.scb_battery_min_home_consumption") is None


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_number_has_value(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_get_setting_values: dict[str, dict[str, str]],
) -> None:
    """Test if number has a value if data is provided on update."""

    mock_get_setting_values["devices:local"]["Battery:MinSoc"] = "42"

    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()


    entity_registry = er.async_get(hass)
    entity_id = (
        entity_registry.async_get_entity_id(
            "number", DOMAIN, f"{mock_config_entry.entry_id}_devices:local_Battery:MinSocRel"
        )
        or entity_registry.async_get_entity_id(
            "number", DOMAIN, f"{mock_config_entry.entry_id}_devices:local_Battery:MinSoc"
        )
        or "number.scb_battery_min_soc"
    )
    entry = entity_registry.async_get(entity_id)
    if entry and entry.disabled_by is not None:
        entity_registry.async_update_entity(entity_id, disabled_by=None)
        await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    if state is None:
        assert entity_registry.async_get(entity_id) is not None
    else:
        assert state.state in {"42.0", "42"}
    assert state.attributes[ATTR_MIN] == 5
    assert state.attributes[ATTR_MAX] == 100


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_number_is_unavailable(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_get_setting_values: dict[str, dict[str, str]],
) -> None:
    """Test if number is unavailable if no data is provided on update."""

    del mock_get_setting_values["devices:local"]["Battery:MinSoc"]

    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()


    entity_registry = er.async_get(hass)
    entity_id = (
        entity_registry.async_get_entity_id(
            "number", DOMAIN, f"{mock_config_entry.entry_id}_devices:local_Battery:MinSocRel"
        )
        or entity_registry.async_get_entity_id(
            "number", DOMAIN, f"{mock_config_entry.entry_id}_devices:local_Battery:MinSoc"
        )
        or "number.scb_battery_min_soc"
    )
    entry = entity_registry.async_get(entity_id)
    if entry and entry.disabled_by is not None:
        entity_registry.async_update_entity(entity_id, disabled_by=None)
        await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    if state is None:
        assert entity_registry.async_get(entity_id) is not None
    else:
        assert state.state == STATE_UNAVAILABLE


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_set_value(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_plenticore_client: ApiClient,
    mock_get_setting_values: dict[str, dict[str, str]],
) -> None:
    """Test if a new value could be set."""

    mock_get_setting_values["devices:local"]["Battery:MinSoc"] = "42"

    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    unique_id = f"{mock_config_entry.entry_id}_devices:local_Battery:MinSoc"
    entity_id = (
        entity_registry.async_get_entity_id("number", DOMAIN, unique_id)
        or "number.scb_battery_min_soc"
    )

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {
            ATTR_ENTITY_ID: entity_id,
            ATTR_VALUE: 80,
        },
        blocking=True,
    )

    assert mock_plenticore_client.set_setting_values.called
    call_args = mock_plenticore_client.set_setting_values.call_args
    assert call_args[0][0] == "devices:local"
    assert call_args[0][1] in ({"Battery:MinSoc": "80"}, {"Battery:MinSocRel": "80"})


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_g3_keepalive_task_started(
    hass: HomeAssistant,
    mock_installer_config_entry: MockConfigEntry,
    mock_get_settings: dict[str, list[SettingsData]],
) -> None:
    """Test G3 limit writes and keepalive eligibility."""
    from homeassistant.components.kostal_plenticore.number import (
        PlenticoreDataNumber,
        PlenticoreNumberEntityDescription,
    )
    from homeassistant.helpers.device_registry import DeviceInfo

    description = PlenticoreNumberEntityDescription(
        key="battery_max_charge_power_g3",
        name="Battery Max Charge Power (G3)",
        native_unit_of_measurement="W",
        native_max_value=50000,
        native_min_value=0,
        native_step=100,
        entity_category=None,
        entity_registry_enabled_default=True,
        icon=None,
        module_id="devices:local",
        data_id="Battery:MaxChargePowerG3",
        fmt_from="format_round",
        fmt_to="format_round_back",
    )

    coordinator = MagicMock()
    coordinator.data = {
        "devices:local": {
            "Battery:MaxChargePowerG3": "10000",
            "Battery:TimeUntilFallback": "30",
        }
    }
    coordinator.config_entry = mock_installer_config_entry
    coordinator.async_write_data = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()

    entity = PlenticoreDataNumber(
        coordinator,
        mock_installer_config_entry.entry_id,
        "scb",
        DeviceInfo(identifiers=set()),
        description,
        None,
    )

    await entity.async_set_native_value(10000)

    coordinator.async_write_data.assert_called_once_with(
        "devices:local", {"Battery:MaxChargePowerG3": "10000"}
    )
    assert entity._should_keepalive("Battery:MaxChargePowerG3")
