"""Test Kostal Plenticore diagnostics."""

import asyncio
from unittest.mock import Mock

from homeassistant.components.diagnostics import REDACTED
from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import ANY, MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator


from pykoplenti import ApiException

from kostal_plenticore.diagnostics import _handle_diagnostics_error, async_get_config_entry_diagnostics


async def get_diagnostics_for_config_entry(hass, config_entry):
    """Return diagnostics for a config entry."""
    return await async_get_config_entry_diagnostics(hass, config_entry)


async def test_entry_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_plenticore_client: Mock,
    init_integration: MockConfigEntry,
) -> None:
    """Test config entry diagnostics."""
    # set some test process data for the diagnostics output
    mock_plenticore_client.get_process_data.return_value = {
        "devices:local": ["HomeGrid_P", "HomePv_P"]
    }

    diagnostics = await get_diagnostics_for_config_entry(
        hass, init_integration
    )

    assert diagnostics["config_entry"]["entry_id"] == "2ab8dd92a62787ddfe213a67e09406bd"
    assert diagnostics["config_entry"]["title"] == "scb"
    assert diagnostics["config_entry"]["data"] == {"host": "192.168.1.2", "password": REDACTED}

    assert diagnostics["client"]["version"].api_version == "0.2.0"
    assert diagnostics["client"]["me"].role == "USER"

    assert diagnostics["client"]["available_process_data"] == {"devices:local": ["HomeGrid_P", "HomePv_P"]}
    assert "Battery:MinSoc" in str(diagnostics["client"]["available_settings_data"]["devices:local"])

    assert diagnostics["configuration"] == {
        "devices:local": {
            "Properties:String0Features": "1",
            "Properties:String1Features": "1",
        }
    }

    assert diagnostics["device"]["model"] == "PLENTICORE plus 10"
    assert diagnostics["device"]["sw_version"] == "IOC: 01.45 MC: 01.46"


async def test_entry_diagnostics_invalid_string_count(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_plenticore_client: Mock,
    mock_get_setting_values: Mock,
    init_integration: MockConfigEntry,
) -> None:
    """Test config entry diagnostics if string count is invalid."""
    # set some test process data for the diagnostics output
    mock_plenticore_client.get_process_data.return_value = {
        "devices:local": ["HomeGrid_P", "HomePv_P"]
    }

    mock_get_setting_values["devices:local"]["Properties:StringCnt"] = "invalid"

    diagnostic_data = await get_diagnostics_for_config_entry(
        hass, init_integration
    )

    assert diagnostic_data["configuration"] == {}


def test_handle_diagnostics_error_branches() -> None:
    assert _handle_diagnostics_error(ApiException("boom"), "string_count") == 0
    assert _handle_diagnostics_error(ApiException("boom"), "version") == "Unknown"
    assert _handle_diagnostics_error(ValueError("bad"), "string_count") == 0
    assert _handle_diagnostics_error(ValueError("bad"), "settings data") == {}
    assert _handle_diagnostics_error(asyncio.TimeoutError(), "me") == "Unknown"
    assert _handle_diagnostics_error(asyncio.TimeoutError(), "process data") == {}
    assert _handle_diagnostics_error(RuntimeError("boom"), "string_count") == 0
    assert _handle_diagnostics_error(asyncio.TimeoutError(), "string_count") == 0
    assert _handle_diagnostics_error(RuntimeError("boom"), "version") == "Unknown"
