"""Test the Kostal Plenticore Solar Inverter initialization."""

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

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
