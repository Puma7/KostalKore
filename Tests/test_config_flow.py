"""Test the KOSTAL KORE config flow wizard."""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
from aiohttp.client_exceptions import ClientError
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pykoplenti import ApiClient, AuthenticationException, SettingsData
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kostal_kore import config_flow as kore_config_flow
from custom_components.kostal_kore.const import DOMAIN


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


def test_installer_access_unknown_role_falls_back_to_service_code() -> None:
    # Explicit role strings: hard-coded mapping, no service-code influence.
    assert kore_config_flow._installer_access_from_role("INSTALLER", None) is True
    assert kore_config_flow._installer_access_from_role("USER", "12345") is False
    # Truly unknown role ("UNKNOWN" / "") falls back to service code.
    assert kore_config_flow._installer_access_from_role("UNKNOWN", None) is False
    assert kore_config_flow._installer_access_from_role("UNKNOWN", "12345") is True
    assert kore_config_flow._installer_access_from_role("", "12345") is True
    # Unrecognised role strings ("mystery", "INSTALLER_TRIAL", …) are denied
    # even with a service code — only explicitly-known roles unlock writes.
    assert kore_config_flow._installer_access_from_role("mystery", None) is False
    assert kore_config_flow._installer_access_from_role("mystery", "12345") is False


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
    # HA < 2024.4 uses "reauth_successful"; 2024.4+ uses "reconfigure_successful"
    assert result["reason"] in ("reconfigure_successful", "reauth_successful")
    assert mock_config_entry.data[CONF_HOST] == "1.1.1.1"
    assert mock_config_entry.data["access_role"] == "INSTALLER"
    assert mock_config_entry.data["installer_access"] is True


async def test_probe_and_discovery_helpers_cover_remaining_paths(
    hass: HomeAssistant,
) -> None:
    """Probe/discovery helpers handle positive and negative candidates."""
    version = SimpleNamespace(api_version="1", sw_version="2")
    api_ctx = MagicMock()
    api_ctx.get_version = AsyncMock(return_value=version)
    api_client = MagicMock()
    api_client.__aenter__.return_value = api_ctx
    api_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "custom_components.kostal_kore.config_flow.ApiClient",
        return_value=api_client,
    ):
        assert await kore_config_flow._probe_kostal_api("192.168.1.10", hass) is True

    with patch(
        "custom_components.kostal_kore.config_flow.ApiClient",
        side_effect=RuntimeError("boom"),
    ):
        assert await kore_config_flow._probe_kostal_api("192.168.1.11", hass) is False

    async def _fake_adapters(_hass: HomeAssistant) -> list[dict[str, object]]:
        return [
            {
                "ipv4": [
                    {"address": "192.168.10.10", "network_prefix": "24"},
                    {"address": "192.168.10.10", "network_prefix": "bad"},
                    {"address": "192.168.10.10", "network_prefix": 31},
                    {"address": "not-an-ip", "network_prefix": 24},
                ]
            }
        ]

    with patch(
        "homeassistant.components.network.async_get_adapters",
        AsyncMock(side_effect=_fake_adapters),
    ):
        candidates = await kore_config_flow._build_discovery_candidates(hass)

    assert candidates
    assert "192.168.10.10" not in candidates
    assert len(candidates) <= kore_config_flow.DISCOVERY_MAX_HOSTS_PER_ADAPTER
    assert len(candidates) == len(set(candidates))

    class _EmptyNetwork:
        def hosts(self) -> list[object]:
            return []

    class _EmptyInterface:
        ip = "10.0.0.10"
        network = _EmptyNetwork()

    with patch(
        "homeassistant.components.network.async_get_adapters",
        AsyncMock(return_value=[{"ipv4": [{"address": "10.0.0.10", "network_prefix": 24}]}]),
    ), patch(
        "custom_components.kostal_kore.config_flow.ipaddress.ip_interface",
        return_value=_EmptyInterface(),
    ):
        assert await kore_config_flow._build_discovery_candidates(hass) == []

    with patch(
        "custom_components.kostal_kore.config_flow._build_discovery_candidates",
        AsyncMock(return_value=[]),
    ):
        assert await kore_config_flow.discover_inverter_hosts(hass) == []

    with patch(
        "custom_components.kostal_kore.config_flow._build_discovery_candidates",
        AsyncMock(return_value=["a", "b", "c"]),
    ), patch(
        "custom_components.kostal_kore.config_flow._probe_kostal_api",
        AsyncMock(side_effect=[False, True, False]),
    ):
        assert await kore_config_flow.discover_inverter_hosts(hass) == ["b"]

    class _DupeNetwork:
        def __init__(self):
            self._hosts = [
                kore_config_flow.ipaddress.ip_address("192.168.1.10"),
                kore_config_flow.ipaddress.ip_address("192.168.1.11"),
            ]

        def hosts(self):
            return list(self._hosts)

    class _DupeInterface:
        def __init__(self):
            self.ip = kore_config_flow.ipaddress.ip_address("192.168.1.10")
            self.network = _DupeNetwork()

    with patch(
        "homeassistant.components.network.async_get_adapters",
        AsyncMock(
            return_value=[
                {"enabled": True, "ipv4": [{"address": "192.168.1.10", "network_prefix": 24}]},
                {"enabled": True, "ipv4": [{"address": "192.168.1.10", "network_prefix": 24}]},
            ]
        ),
    ), patch(
        "custom_components.kostal_kore.config_flow.ipaddress.ip_interface",
        return_value=_DupeInterface(),
    ):
        assert await kore_config_flow._build_discovery_candidates(hass) == ["192.168.1.11"]


async def test_connection_and_resolve_helpers_cover_error_paths(
    hass: HomeAssistant,
) -> None:
    """Connection helpers cover empty host and discovery edge cases."""
    with pytest.raises(kore_config_flow.NoDiscoveredInverterError):
        await kore_config_flow.test_connection_safe(
            hass,
            {CONF_PASSWORD: "pw"},
        )

    with patch(
        "custom_components.kostal_kore.config_flow.discover_inverter_hosts",
        AsyncMock(return_value=["10.0.0.1", "10.0.0.2"]),
    ), patch(
        "custom_components.kostal_kore.config_flow.test_connection_safe",
        AsyncMock(
            side_effect=[
                AuthenticationException(401, "bad"),
                AuthenticationException(401, "bad-again"),
            ]
        ),
    ):
        with pytest.raises(kore_config_flow.DiscoveryAuthFailedError):
            await kore_config_flow.resolve_connection_safe(
                hass,
                {CONF_PASSWORD: "pw"},
            )

    with patch(
        "custom_components.kostal_kore.config_flow.discover_inverter_hosts",
        AsyncMock(return_value=["10.0.0.3"]),
    ), patch(
        "custom_components.kostal_kore.config_flow.test_connection_safe",
        AsyncMock(side_effect=ClientError("network")),
    ):
        with pytest.raises(ClientError):
            await kore_config_flow.resolve_connection_safe(
                hass,
                {CONF_PASSWORD: "pw"},
            )

    with patch(
        "custom_components.kostal_kore.config_flow.discover_inverter_hosts",
        AsyncMock(return_value=["10.0.0.4"]),
    ), patch(
        "custom_components.kostal_kore.config_flow.DISCOVERY_MAX_AUTH_ATTEMPTS",
        0,
    ):
        with pytest.raises(kore_config_flow.NoDiscoveredInverterError):
            await kore_config_flow.resolve_connection_safe(
                hass,
                {CONF_PASSWORD: "pw"},
            )


async def test_run_modbus_connection_test_all_register_reads_fail() -> None:
    """All register failures should produce a failed smoke test."""
    fake_client = MagicMock()
    fake_client.connect = AsyncMock()
    fake_client.detect_endianness = AsyncMock(return_value="little")
    fake_client.read_register = AsyncMock(side_effect=RuntimeError("nope"))
    fake_client.disconnect = AsyncMock()

    with patch(
        "custom_components.kostal_kore.modbus_client.KostalModbusClient",
        return_value=fake_client,
    ):
        passed, log = await kore_config_flow.run_modbus_connection_test(
            "127.0.0.1",
            {},
        )

    assert passed is False
    assert "All 6 register reads failed." in log


async def test_flow_step_helpers_cover_remaining_branches(hass: HomeAssistant) -> None:
    """Direct step invocation covers wizard branches not hit by end-to-end flows."""
    flow = kore_config_flow.KostalPlenticoreConfigFlow()
    flow.hass = hass

    with patch.object(flow, "async_step_user", AsyncMock(return_value={"step_id": "user"})):
        assert await flow.async_step_setup_options() == {"step_id": "user"}

    flow._pending_entry_data = {
        CONF_HOST: "1.2.3.4",
        "access_role": "USER",
        "installer_access": False,
    }
    flow._pending_entry_title = "host"

    with patch.object(
        flow,
        "async_step_setup_modbus_test",
        AsyncMock(return_value={"step_id": "setup_modbus_test"}),
    ):
        result = await flow.async_step_setup_options({"modbus_enabled": True})
    assert result == {"step_id": "setup_modbus_test"}

    flow._pending_options = {"modbus_enabled": True}
    # Submit now re-runs the connection test (HIGH-01 fix): a passing test
    # must be mocked, otherwise Submit returns a form with modbus_test_failed
    # instead of creating the entry.
    with patch(
        "custom_components.kostal_kore.config_flow.run_modbus_connection_test",
        AsyncMock(return_value=(True, "ok")),
    ):
        result = await flow.async_step_setup_modbus_test({"confirm": True})
    assert result["type"] is FlowResultType.CREATE_ENTRY

    with patch(
        "custom_components.kostal_kore.config_flow.run_modbus_connection_test",
        AsyncMock(return_value=(False, "broken")),
    ):
        result = await flow.async_step_setup_modbus_test()
    assert result["step_id"] == "setup_modbus_test"
    assert result["errors"] == {"base": "modbus_test_failed"}

    with patch(
        "custom_components.kostal_kore.config_flow.resolve_connection_safe",
        AsyncMock(side_effect=asyncio.TimeoutError()),
    ):
        result = await flow.async_step_reconfigure(
            {CONF_HOST: "1.2.3.4", CONF_PASSWORD: "pw"}
        )
    assert result["errors"] == {CONF_HOST: "timeout"}
