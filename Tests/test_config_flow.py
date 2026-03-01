"""Test the KOSTAL KORE config flow wizard."""

from __future__ import annotations

from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from pykoplenti import ApiClient, AuthenticationException, SettingsData
import pytest

from homeassistant import config_entries
from custom_components.kostal_kore import config_flow as kore_config_flow
from custom_components.kostal_kore.const import DOMAIN
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from pytest_homeassistant_custom_component.common import MockConfigEntry


@pytest.fixture(autouse=True)
def clear_rate_limit() -> Generator[None]:
    """Clear config flow rate limiting between tests."""
    yield


@pytest.fixture(autouse=True)
def mock_async_reload(hass: HomeAssistant) -> Generator[None]:
    """Prevent config entry reloads from performing real setup during tests."""
    with patch.object(hass.config_entries, "async_reload", AsyncMock(return_value=True)):
        yield


@pytest.fixture(autouse=True)
def mock_clientsession() -> Generator[None]:
    """Avoid creating real aiohttp sessions in config flow tests."""
    with patch(
        "custom_components.kostal_kore.config_flow.async_get_clientsession",
        return_value=MagicMock(),
    ):
        yield


@pytest.fixture
def mock_apiclient() -> ApiClient:
    """Return a mocked ApiClient context manager."""
    apiclient = MagicMock(spec=ApiClient)
    apiclient.__aenter__.return_value = apiclient
    apiclient.__aexit__ = AsyncMock()
    apiclient.login = AsyncMock()
    apiclient.get_settings = AsyncMock(
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
                ),
            ]
        }
    )
    apiclient.get_setting_values = AsyncMock(
        return_value={"scb:network": {"Hostname": "scb"}}
    )
    apiclient.get_me = AsyncMock(return_value=SimpleNamespace(role="USER"))
    return apiclient


@pytest.fixture
def mock_apiclient_class(mock_apiclient: ApiClient) -> Generator[type[ApiClient]]:
    """Patch ApiClient class used by config_flow."""
    with patch(
        "custom_components.kostal_kore.config_flow.ApiClient",
        autospec=True,
    ) as mock_api_class:
        mock_api_class.return_value = mock_apiclient
        yield mock_api_class


async def test_manual_host_wizard_creates_entry_with_options(
    hass: HomeAssistant,
    mock_apiclient_class: type[ApiClient],
    mock_apiclient: ApiClient,
) -> None:
    """Manual host entry goes to setup wizard and creates entry with options."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "1.1.1.1",
            CONF_PASSWORD: "test-password",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "setup_options"

    with patch(
        "custom_components.kostal_kore.async_setup_entry",
        return_value=True,
    ) as mock_setup_entry:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "modbus_enabled": False,
                "mqtt_bridge_enabled": False,
                "modbus_proxy_enabled": False,
            },
        )
        await hass.async_block_till_done()

    mock_apiclient_class.assert_called_once_with(ANY, "1.1.1.1")
    mock_apiclient.login.assert_called_once()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "scb"
    assert result["data"] == {
        "host": "1.1.1.1",
        "password": "test-password",
        "access_role": "USER",
        "installer_access": False,
    }
    assert result["options"]["modbus_enabled"] is False
    assert len(mock_setup_entry.mock_calls) == 1


async def test_auto_discovery_uses_detected_host(
    hass: HomeAssistant,
    mock_apiclient_class: type[ApiClient],
    mock_apiclient: ApiClient,
) -> None:
    """Leaving host empty triggers auto-discovery in user step."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "custom_components.kostal_kore.config_flow.discover_inverter_hosts",
        new=AsyncMock(return_value=["192.168.1.23"]),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_PASSWORD: "test-password",
            },
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "setup_options"
    mock_apiclient_class.assert_called_once_with(ANY, "192.168.1.23")
    mock_apiclient.login.assert_called_once()


async def test_discovery_error_when_no_inverter_found(hass: HomeAssistant) -> None:
    """Empty host + no discovered hosts returns a host error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "custom_components.kostal_kore.config_flow.discover_inverter_hosts",
        new=AsyncMock(return_value=[]),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_PASSWORD: "test-password",
            },
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_HOST: "no_discovered_inverter"}


async def test_form_invalid_auth(hass: HomeAssistant) -> None:
    """Invalid credentials map to password error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "custom_components.kostal_kore.config_flow.ApiClient"
    ) as mock_api_class:
        mock_api_ctx = MagicMock()
        mock_api_ctx.login = AsyncMock(
            side_effect=AuthenticationException(404, "invalid user"),
        )
        mock_api = MagicMock()
        mock_api.__aenter__.return_value = mock_api_ctx
        mock_api.__aexit__.return_value = None
        mock_api_class.return_value = mock_api

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: "1.1.1.1",
                CONF_PASSWORD: "test-password",
            },
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_PASSWORD: "invalid_auth"}


async def test_installer_role_is_detected(
    hass: HomeAssistant,
    mock_apiclient: ApiClient,
) -> None:
    """Installer role detection sets installer_access=True in entry data."""
    mock_apiclient.get_me = AsyncMock(return_value=SimpleNamespace(role="INSTALLER"))
    with patch(
        "custom_components.kostal_kore.config_flow.ApiClient",
        autospec=True,
        return_value=mock_apiclient,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: "1.1.1.1",
                CONF_PASSWORD: "test-password",
                "service_code": "12345",
            },
        )
        assert result["step_id"] == "setup_options"

        with patch(
            "custom_components.kostal_kore.async_setup_entry",
            return_value=True,
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    "modbus_enabled": False,
                },
            )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["access_role"] == "INSTALLER"
    assert result["data"]["installer_access"] is True


def test_normalize_options_enables_modbus_dependencies() -> None:
    """MQTT/proxy request forces modbus_enabled=True."""
    options = kore_config_flow._normalize_options(
        {
            "modbus_enabled": False,
            "mqtt_bridge_enabled": True,
            "modbus_proxy_enabled": True,
        }
    )
    assert options["modbus_enabled"] is True


async def test_reconfigure_updates_entry_with_access_profile(
    hass: HomeAssistant,
    mock_apiclient_class: type[ApiClient],
    mock_apiclient: ApiClient,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Reconfigure stores host/password plus detected access profile."""
    mock_apiclient.get_me = AsyncMock(return_value=SimpleNamespace(role="INSTALLER"))
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": "reconfigure",
            "entry_id": mock_config_entry.entry_id,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "1.1.1.1",
            CONF_PASSWORD: "test-password",
            "service_code": "12345",
        },
    )
    await hass.async_block_till_done()

    mock_apiclient_class.assert_called_once_with(ANY, "1.1.1.1")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data[CONF_HOST] == "1.1.1.1"
    assert mock_config_entry.data["access_role"] == "INSTALLER"
    assert mock_config_entry.data["installer_access"] is True
