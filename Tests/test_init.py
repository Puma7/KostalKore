"""Test the Kostal Plenticore Solar Inverter initialization."""

from unittest.mock import call, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, issue_registry as ir

from custom_components.kostal_kore.const import DOMAIN

from pytest_homeassistant_custom_component.common import MockConfigEntry

async def test_setup_unload_entry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_plenticore_client,
) -> None:
    """Test setting up and unloading the config entry."""
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_fails(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_plenticore_client,
) -> None:
    """Test setup failure when inverter is not reachable."""
    mock_plenticore_client.login.side_effect = Exception("Connection failed")

    mock_config_entry.add_to_hass(hass)

    # HA async_setup will return True but log the failure if it's handled,
    # or return False if it's not. Plenticore.async_setup returns False on Exception.
    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR


async def test_migration_check_creates_issue_when_unit_is_ah(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_plenticore_client,
) -> None:
    """Migration check fires repair issue when WorkCapacity entity still has Ah unit."""
    mock_config_entry.add_to_hass(hass)

    entity_registry = er.async_get(hass)
    reg_entry = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{mock_config_entry.entry_id}_devices:local:battery_WorkCapacity",
        config_entry=mock_config_entry,
    )
    entity_registry.async_update_entity(reg_entry.entity_id, unit_of_measurement="Ah")

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    issue_registry = ir.async_get(hass)
    expected_issue_id = f"{DOMAIN}_{mock_config_entry.entry_id}_battery_capacity_unit_migration"
    assert issue_registry.async_get_issue(DOMAIN, expected_issue_id) is not None


async def test_migration_check_clears_issue_when_unit_already_migrated(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_plenticore_client,
) -> None:
    """Migration check clears repair issue when WorkCapacity entity no longer has Ah unit."""
    mock_config_entry.add_to_hass(hass)

    entity_registry = er.async_get(hass)
    reg_entry = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{mock_config_entry.entry_id}_devices:local:battery_WorkCapacity",
        config_entry=mock_config_entry,
    )
    entity_registry.async_update_entity(reg_entry.entity_id, unit_of_measurement="Wh")

    with patch(
        "custom_components.kostal_kore.__init__.clear_issue"
    ) as mock_clear:
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    battery_migration_calls = [
        c for c in mock_clear.call_args_list
        if len(c.args) > 1 and c.args[1] == "battery_capacity_unit_migration"
    ]
    assert battery_migration_calls == [
        call(hass, "battery_capacity_unit_migration", entry_id=mock_config_entry.entry_id)
    ]


async def test_async_remove_config_entry_device(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_plenticore_client,
) -> None:
    """Test that stale devices can be removed from the registry."""
    from unittest.mock import MagicMock
    from kostal_plenticore import async_remove_config_entry_device

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    device_entry = MagicMock()
    result = await async_remove_config_entry_device(hass, mock_config_entry, device_entry)
    assert result is True
