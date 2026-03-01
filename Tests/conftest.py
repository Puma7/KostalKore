"""Fixtures for KOSTAL KORE tests."""

from __future__ import annotations

import asyncio
import threading
import sys
import os
from pathlib import Path
from datetime import timedelta
from collections.abc import Generator, Iterable
import copy
from unittest.mock import patch, MagicMock

# Use SelectorEventLoop on Windows to satisfy aiodns requirements
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Prevent aiohttp from loading aiodns on Windows tests
os.environ.setdefault("AIOHTTP_NO_EXTENSIONS", "1")

try:
    import aiohttp
    from aiohttp import resolver as aiohttp_resolver

    if sys.platform == "win32":
        aiohttp_resolver.AsyncResolver = aiohttp_resolver.ThreadedResolver
except Exception:
    pass

from pykoplenti import ExtendedApiClient, MeData, SettingsData, VersionData, ProcessData
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from pytest_homeassistant_custom_component.common import MockConfigEntry

# Ensure SelectorEventLoop on Windows (aiodns requirement)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Pre-load local integration package and map HA component aliases.
try:
    import custom_components.kostal_kore as kostal_kore
    import custom_components.kostal_kore.binary_sensor
    import custom_components.kostal_kore.button
    import custom_components.kostal_kore.config_flow
    import custom_components.kostal_kore.const
    import custom_components.kostal_kore.coordinator
    import custom_components.kostal_kore.health_monitor
    import custom_components.kostal_kore.health_sensor
    import custom_components.kostal_kore.health_binary_sensor

    # Primary component path for new domain.
    _ha_prefix = "homeassistant.components.kostal_kore"
    sys.modules[_ha_prefix] = kostal_kore
    # Backward-compat alias used by legacy tests.
    sys.modules["homeassistant.components.kostal_plenticore"] = kostal_kore
    for _sub in (
        "binary_sensor", "button", "config_flow", "const", "coordinator",
        "health_monitor", "health_sensor", "health_binary_sensor",
    ):
        module = getattr(kostal_kore, _sub)
        sys.modules[f"{_ha_prefix}.{_sub}"] = module
        sys.modules[f"homeassistant.components.kostal_plenticore.{_sub}"] = module

except ImportError as e:
    print(f"Warning: Could not import Platinum version: {e}")
    import custom_components.kostal_kore as kostal_kore

import pytest_socket

# Aggressively disable socket blocking for Windows compatibility
pytest_socket.disable_socket = lambda *args, **kwargs: None
pytest_socket.socket_allow_hosts = lambda *args, **kwargs: None
pytest_socket.enable_socket()

from custom_components.kostal_kore import coordinator
from custom_components.kostal_kore.const import DOMAIN

@pytest.fixture
def mock_performance_coordinator():
    """Mock performance-optimized coordinator."""
    try:
        from custom_components.kostal_kore.coordinator import PlenticoreUpdateCoordinator
        coordinator = MagicMock(spec=PlenticoreUpdateCoordinator)
        coordinator._fetch = {}
        return coordinator
    except ImportError:
        return MagicMock()

@pytest.fixture
def mock_device_scanner():
    """Mock KostalDeviceScanner for Platinum discovery features."""
    try:
        from custom_components.kostal_kore.config_flow import discover_inverter_hosts
        return MagicMock(spec=discover_inverter_hosts)
    except ImportError:
        # Fallback for older version
        return MagicMock()

@pytest.fixture(scope="session", autouse=True)
def allow_all_sockets():
    """Enable sockets for the entire session.
    
    This is required on Windows because the ProactorEventLoop uses sockets 
    internally for IPC, and pytest-socket (used by HA tests) blocks them by default.
    """
    pytest_socket.enable_socket()

@pytest.fixture
def event_loop():
    """Create an instance of the default event loop for each test case."""
    if sys.platform == "win32":
        # aiodns requires SelectorEventLoop on Windows
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        loop = asyncio.SelectorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    for handle in list(getattr(loop, "_scheduled", [])):
        handle.cancel()
    loop.close()
    asyncio.set_event_loop(None)

@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def expose_kostal_kore_custom_component(hass: HomeAssistant):
    """Expose the local integration in the temporary HA config directory."""
    source = Path(__file__).resolve().parents[1] / "custom_components" / "kostal_kore"
    custom_components_dir = Path(hass.config.path("custom_components"))
    custom_components_dir.mkdir(parents=True, exist_ok=True)
    target = custom_components_dir / "kostal_kore"
    if not target.exists():
        target.symlink_to(source, target_is_directory=True)
    yield


@pytest.fixture(autouse=True)
def immediate_async_call_later(hass: HomeAssistant):
    """Execute async_call_later callbacks immediately to avoid lingering timers."""
    def _immediate_call_later(hass: HomeAssistant, _delay: float, action):
        async def _run_action():
            result = action(dt_util.utcnow())
            if asyncio.iscoroutine(result):
                await result

        hass.async_create_task(_run_action())
        return lambda: None

    with (
        patch("homeassistant.helpers.event.async_call_later", side_effect=_immediate_call_later),
        patch("custom_components.kostal_kore.coordinator.async_call_later", side_effect=_immediate_call_later),
        patch("custom_components.kostal_kore.number.async_call_later", side_effect=_immediate_call_later),
    ):
        yield


@pytest.fixture(autouse=True)
async def unload_entries_after_test(hass: HomeAssistant):
    """Unload config entries after each test to avoid lingering timers."""
    yield
    for entry in list(hass.config_entries.async_entries(DOMAIN)):
        try:
            await hass.config_entries.async_unload(entry.entry_id)
        except Exception:
            pass
    await hass.async_block_till_done()


@pytest.fixture(autouse=True, scope="session")
def filter_shutdown_threads():
    """Filter asyncio shutdown helper threads from cleanup checks on Windows."""
    original_enumerate = threading.enumerate

    def _filtered_enumerate():
        return [
            thread
            for thread in original_enumerate()
            if "_run_safe_shutdown_loop" not in thread.name
        ]

    with patch("threading.enumerate", side_effect=_filtered_enumerate):
        yield


@pytest.fixture(autouse=True)
def cancel_ssl_shutdown_timers(hass: HomeAssistant):
    """Cancel lingering SSL shutdown timers to satisfy cleanup checks."""
    yield
    loop = hass.loop
    for handle in list(getattr(loop, "_scheduled", [])):
        if "SSLProtocol._start_shutdown" in repr(handle):
            handle.cancel()


@pytest.fixture
def entity_registry_enabled_by_default() -> Generator[None, None, None]:
    """Test fixture that ensures all entities are enabled in the registry."""
    with patch(
        "homeassistant.helpers.entity.Entity.entity_registry_enabled_default",
        return_value=True,
    ):
        yield


DEFAULT_SETTING_VALUES = {
    "devices:local": {
        "Properties:StringCnt": "2",
        "Properties:String0Features": "1",
        "Properties:String1Features": "1",
        "Properties:SerialNo": "42",
        "Branding:ProductName1": "PLENTICORE",
        "Branding:ProductName2": "plus 10",
        "Properties:VersionIOC": "01.45",
        "Properties:VersionMC": "01.46",
        "Battery:MinSoc": "5",
        "Battery:MinHomeComsumption": "50",
    },
    "scb:network": {"Hostname": "scb"},
}

DEFAULT_PROCESS_DATA = {
    "devices:local": [
        "Inverter:State",
        "Dc_P",
        "Grid_P",
        "HomeBat_P",
        "HomeGrid_P",
        "HomeOwn_P",
        "HomePv_P",
        "Home_P",
        "Properties:StringCnt",
        "Properties:String0Features",
        "Properties:String1Features",
        "Properties:String2Features",
    ],
    "devices:local:pv1": ["P", "U", "I"],
    "devices:local:pv2": ["P", "U", "I"],
    "devices:local:pv3": ["P", "U", "I"],
    "devices:local:battery": ["P", "SoC", "Cycles"],
}

DEFAULT_SETTINGS = {
    "devices:local": [
        SettingsData(
            min="5",
            max="100",
            default=None,
            access="readwrite",
            unit="%",
            id="Battery:MinSoc",
            type="byte",
        ),
        SettingsData(
            min="50",
            max="38000",
            default=None,
            access="readwrite",
            unit="W",
            id="Battery:MinHomeComsumption",
            type="byte",
        ),
    ],
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
    ],
}


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a mocked ConfigEntry for testing."""
    return MockConfigEntry(
        entry_id="2ab8dd92a62787ddfe213a67e09406bd",
        title="scb",
        domain=DOMAIN,
        data={"host": "192.168.1.2", "password": "SecretPassword"},
    )


@pytest.fixture
def mock_installer_config_entry() -> MockConfigEntry:
    """Return a mocked ConfigEntry for testing with installer login."""
    return MockConfigEntry(
        entry_id="2ab8dd92a62787ddfe213a67e09406bd",
        title="scb",
        domain=DOMAIN,
        data={
            "host": "192.168.1.2",
            "password": "secret_password",
            "service_code": "12345",
        },
    )


@pytest.fixture
def mock_get_settings() -> dict[str, list[SettingsData]]:
    """Add setting data to mock_plenticore_client.

    Returns a dictionary with setting data which can be mutated by test cases.
    """
    return copy.deepcopy(DEFAULT_SETTINGS)


@pytest.fixture
def mock_get_setting_values() -> dict[str, dict[str, str]]:
    """Add setting values to mock_plenticore_client.

    Returns a dictionary with setting values which can be mutated by test cases.
    """
    # Add default settings values - this values are always retrieved by the integration on startup
    return copy.deepcopy(DEFAULT_SETTING_VALUES)


@pytest.fixture
def mock_get_process_data() -> dict[str, list[str]]:
    """Add process data structure to mock_plenticore_client."""
    return copy.deepcopy(DEFAULT_PROCESS_DATA)


@pytest.fixture
def mock_get_process_data_values() -> dict[str, dict[str, str]]:
    """Add process data values to mock_plenticore_client."""
    return {
        "devices:local": {
            "Inverter:State": "0",
            "Dc_P": "0.0",
            "Grid_P": "0.0",
            "HomeBat_P": "0.0",
            "HomeGrid_P": "0.0",
            "HomeOwn_P": "0.0",
            "HomePv_P": "0.0",
            "Home_P": "0.0",
        }
    }


@pytest.fixture
def mock_plenticore_client(
    mock_get_settings: dict[str, list[SettingsData]],
    mock_get_setting_values: dict[str, dict[str, str]],
    mock_get_process_data: dict[str, list[str]],
    mock_get_process_data_values: dict[str, dict[str, str]],
) -> Generator[ExtendedApiClient]:
    """Return a patched ExtendedApiClient."""
    with patch.object(
        coordinator,
        "ExtendedApiClient",
        autospec=True,
    ) as plenticore_client_class:

        def default_settings_data(*args):
            # the get_setting_values method can be called with different argument types and numbers
            match args:
                case (str() as module_id, str() as data_id):
                    request = {module_id: [data_id]}
                case (str() as module_id, Iterable() as data_ids):
                    request = {module_id: data_ids}
                case (dict() as d,):
                    request = d
                case _:
                    raise NotImplementedError

            result = {}
            for module_id, data_ids in request.items():
                if (values := mock_get_setting_values.get(module_id)) is not None:
                    result[module_id] = {}
                    for data_id in data_ids:
                        if data_id in values:
                            result[module_id][data_id] = values[data_id]
                else:
                    # Module not found – return empty dict for this module
                    result[module_id] = {}

            return result

        client = plenticore_client_class.return_value
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.get_setting_values.side_effect = default_settings_data
        client.get_settings.return_value = mock_get_settings
        client.get_process_data.return_value = mock_get_process_data

        def default_process_data_values(request):
            result = {}
            for module_id, data_ids in request.items():
                result[module_id] = {}
                values = mock_get_process_data_values.get(module_id, {})
                for data_id in data_ids:
                    val = values.get(data_id, "0")
                    result[module_id][data_id] = ProcessData(id=data_id, unit="", value=val)
            return result

        client.get_process_data_values.side_effect = default_process_data_values

        client.get_me.return_value = MeData(
            locked=False,
            active=True,
            authenticated=True,
            permissions=[],
            anonymous=False,
            role="USER",
        )
        client.get_version.return_value = VersionData(
            api_version="0.2.0",
            hostname="scb",
            name="PUCK RESTful API",
            sw_version="01.16.05025",
        )

        yield client


@pytest.fixture
async def init_integration(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> MockConfigEntry:
    """Set up Kostal Plenticore integration for testing."""

    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    return mock_config_entry
