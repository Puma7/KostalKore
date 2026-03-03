"""Test Kostal Plenticore helper."""

from collections.abc import Generator
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp.client_exceptions import ClientError
from pykoplenti import ApiClient, ApiException, ExtendedApiClient, SettingsData
import pytest

from custom_components.kostal_kore.const import DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from pytest_homeassistant_custom_component.common import MockConfigEntry


from custom_components.kostal_kore import coordinator
from custom_components.kostal_kore.const import CONF_INSTALLER_ACCESS, CONF_SERVICE_CODE
from custom_components.kostal_kore.helper import (
    ModbusException,
    ModbusIllegalFunctionError,
    ModbusIllegalDataAddressError,
    ModbusIllegalDataValueError,
    ModbusMemoryParityError,
    ModbusServerDeviceFailureError,
    ModbusServerDeviceBusyError,
    PlenticoreDataFormatter,
    ensure_installer_access,
    generate_confirmation_code,
    get_hostname_id,
    integration_entry_store,
    is_battery_control,
    normalize_isolation_resistance_ohm,
    parse_modbus_exception,
    requires_installer_service_code,
)

@pytest.fixture
def mock_apiclient() -> Generator[ApiClient]:
    """Return a mocked ApiClient class."""
    with patch.object(
        coordinator,
        "ExtendedApiClient",
        autospec=True,
    ) as mock_api_class:

        apiclient = MagicMock(spec=ExtendedApiClient)
        apiclient.__aenter__.return_value = apiclient
        apiclient.__aexit__ = AsyncMock()
        mock_api_class.return_value = apiclient
        yield apiclient


async def test_plenticore_async_setup_g1(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_apiclient: ApiClient,
) -> None:
    """Tests the async_setup() method of the Plenticore class for G1 models."""
    mock_apiclient.get_settings = AsyncMock(
        return_value={
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
    mock_apiclient.get_setting_values = AsyncMock(
        # G1 model has the entry id "Hostname"
        return_value={
            "devices:local": {
                "Properties:SerialNo": "12345",
                "Branding:ProductName1": "PLENTICORE",
                "Branding:ProductName2": "plus 10",
                "Properties:VersionIOC": "01.45",
                "Properties:VersionMC": "01.46",
            },
            "scb:network": {"Hostname": "scb"},
        }
    )

    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    plenticore = mock_config_entry.runtime_data

    assert plenticore.device_info == DeviceInfo(
        configuration_url="http://192.168.1.2",
        identifiers={(DOMAIN, "12345")},
        manufacturer="Kostal",
        model="PLENTICORE plus 10",
        name="scb",
        sw_version="IOC: 01.45 MC: 01.46",
    )


async def test_plenticore_async_setup_g2(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_apiclient: ApiClient,
) -> None:
    """Tests the async_setup() method of the Plenticore class for G2 models."""
    mock_apiclient.get_settings = AsyncMock(
        return_value={
            "scb:network": [
                SettingsData(
                    min="1",
                    max="63",
                    default=None,
                    access="readwrite",
                    unit=None,
                    id="Network:Hostname",
                    type="string",
                )
            ]
        }
    )
    mock_apiclient.get_setting_values = AsyncMock(
        # G1 model has the entry id "Hostname"
        return_value={
            "devices:local": {
                "Properties:SerialNo": "12345",
                "Branding:ProductName1": "PLENTICORE",
                "Branding:ProductName2": "plus 10",
                "Properties:VersionIOC": "01.45",
                "Properties:VersionMC": "01.46",
            },
            "scb:network": {"Network:Hostname": "scb"},
        }
    )

    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    plenticore = mock_config_entry.runtime_data

    assert plenticore.device_info == DeviceInfo(
        configuration_url="http://192.168.1.2",
        identifiers={(DOMAIN, "12345")},
        manufacturer="Kostal",
        model="PLENTICORE plus 10",
        name="scb",
        sw_version="IOC: 01.45 MC: 01.46",
    )


def test_helper_battery_control_checks() -> None:
    assert requires_installer_service_code("Battery:MaxChargePowerG3") is True
    assert requires_installer_service_code("Battery:MinSocRel") is False
    assert is_battery_control("Battery:MinSocRel") is True
    assert is_battery_control("Grid:Power") is False


def test_integration_entry_store_reuses_entry_dict(hass: HomeAssistant) -> None:
    first = integration_entry_store(hass, "entry-test")
    first["x"] = 1
    second = integration_entry_store(hass, "entry-test")
    assert first is second
    assert second["x"] == 1


def test_generate_confirmation_code_uses_expected_alphabet() -> None:
    code = generate_confirmation_code()
    assert len(code) == 6
    assert all(c in "ABCDEFGHJKLMNPQRSTUVWXYZ23456789" for c in code)


def test_normalize_isolation_resistance_ohm_handles_kohm_variant() -> None:
    assert normalize_isolation_resistance_ohm(
        65.5, pv_active=True, inverter_state=6
    ) == 65500.0
    assert normalize_isolation_resistance_ohm(
        65500.0, pv_active=True, inverter_state=6
    ) == 65500.0
    assert normalize_isolation_resistance_ohm(
        65.5, pv_active=False, inverter_state=10
    ) == 65.5
    assert normalize_isolation_resistance_ohm(
        65.5, pv_active=True, inverter_state=10
    ) == 65.5
    assert normalize_isolation_resistance_ohm(
        float("nan"), pv_active=True, inverter_state=6
    ) is None
    assert normalize_isolation_resistance_ohm(
        "bad", pv_active=True, inverter_state=6
    ) is None


def test_format_energy_clamps_negative_values() -> None:
    assert PlenticoreDataFormatter.format_energy("-100") == 0.0
    assert PlenticoreDataFormatter.format_energy("2500") == 2.5


def test_ensure_installer_access() -> None:
    entry = SimpleNamespace(data={})
    assert ensure_installer_access(entry, False, "devices:local", "Battery:MinSocRel", "setting") is True
    assert ensure_installer_access(entry, True, "devices:local", "Battery:MinSocRel", "setting") is False
    entry_with_code = SimpleNamespace(data={CONF_SERVICE_CODE: "1234"})
    assert ensure_installer_access(entry_with_code, True, "devices:local", "Battery:MinSocRel", "setting") is True
    entry_with_service_but_user = SimpleNamespace(
        data={CONF_SERVICE_CODE: "1234", CONF_INSTALLER_ACCESS: False}
    )
    assert (
        ensure_installer_access(
            entry_with_service_but_user,
            True,
            "devices:local",
            "Battery:MinSocRel",
            "setting",
        )
        is False
    )


def test_parse_modbus_exception_variants() -> None:
    assert isinstance(
        parse_modbus_exception(ApiException("illegal data value")),
        ModbusIllegalDataValueError,
    )
    assert isinstance(
        parse_modbus_exception(ApiException("illegal function")),
        ModbusIllegalFunctionError,
    )
    assert isinstance(
        parse_modbus_exception(ApiException("server device failure")),
        ModbusServerDeviceFailureError,
    )
    assert isinstance(
        parse_modbus_exception(ApiException("server device busy")),
        ModbusServerDeviceBusyError,
    )
    assert isinstance(
        parse_modbus_exception(ApiException("illegal data address")),
        ModbusIllegalDataAddressError,
    )
    assert isinstance(
        parse_modbus_exception(ApiException("memory parity")),
        ModbusMemoryParityError,
    )
    assert isinstance(
        parse_modbus_exception(ApiException("unexpected error")),
        ModbusException,
    )


def test_format_em_manager_state_passthrough() -> None:
    assert PlenticoreDataFormatter.format_em_manager_state("unknown") == "unknown"


@pytest.mark.asyncio
async def test_get_hostname_id_timeout() -> None:
    client = MagicMock(spec=ApiClient)
    client.get_settings = AsyncMock(side_effect=asyncio.TimeoutError())
    with pytest.raises(ApiException):
        await get_hostname_id(client)


@pytest.mark.asyncio
async def test_get_hostname_id_client_error() -> None:
    client = MagicMock(spec=ApiClient)
    client.get_settings = AsyncMock(side_effect=ClientError("boom"))
    with pytest.raises(ApiException):
        await get_hostname_id(client)


@pytest.mark.asyncio
async def test_get_hostname_id_missing_network_settings() -> None:
    client = MagicMock(spec=ApiClient)
    client.get_settings = AsyncMock(return_value={})
    with pytest.raises(ApiException):
        await get_hostname_id(client)
