"""Fixtures for Kostal Plenticore Platinum tests."""

from __future__ import annotations

from collections.abc import Generator, Iterable
import copy
from unittest.mock import patch

from pykoplenti import ExtendedApiClient, MeData, SettingsData, VersionData, ProcessData
import pytest

from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

import sys
import os
import importlib

# Ensure we can import local package - pointing to Platinum implementation
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "Kostal Inverter HA")))

# Pre-load local kostal_plenticore (Platinum version)
try:
    import kostal_plenticore
    import kostal_plenticore.config_flow
    import kostal_plenticore.const
    import kostal_plenticore.coordinator
    
    # Patch sys.modules to point 'homeassistant.components.kostal_plenticore' to local version
    sys.modules["homeassistant.components.kostal_plenticore"] = kostal_plenticore
    sys.modules["homeassistant.components.kostal_plenticore.config_flow"] = kostal_plenticore.config_flow
    sys.modules["homeassistant.components.kostal_plenticore.const"] = kostal_plenticore.const
    sys.modules["homeassistant.components.kostal_plenticore.coordinator"] = kostal_plenticore.coordinator
    
except ImportError as e:
    print(f"Warning: Could not import Platinum version: {e}")
    # Fallback to older version if Platinum version not available
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    import kostal_plenticore

import pytest_socket

# Aggressively disable socket blocking for Windows compatibility
pytest_socket.disable_socket = lambda *args, **kwargs: None
pytest_socket.socket_allow_hosts = lambda *args, **kwargs: None
pytest_socket.enable_socket()

from kostal_plenticore import coordinator

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
    import asyncio
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    yield loop
    loop.close()

@pytest.fixture
def mock_plenticore_client() -> Generator[MagicMock]:
    """Mock a Plenticore client."""
    mock_client = MagicMock(spec=ExtendedApiClient)
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    
    # Mock common methods
    mock_client.login = AsyncMock()
    mock_client.logout = AsyncMock()
    mock_client.get_modules = AsyncMock(return_value=[])
    mock_client.get_settings = AsyncMock(return_value={})
    mock_client.get_setting_values = AsyncMock(return_value={})
    mock_client.get_process_data = AsyncMock(return_value={})
    mock_client.get_process_data_values = AsyncMock(return_value={})
    mock_client.get_version = AsyncMock(return_value="1.0.0")
    mock_client.get_me = AsyncMock(return_value="test-inverter")
    
    with patch("kostal_plenticore.coordinator.ExtendedApiClient", return_value=mock_client):
        yield mock_client

@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Mock a config entry."""
    return MockConfigEntry(
        domain="kostal_plenticore",
        data={
            "host": "192.168.1.100",
            "password": "test_password",
        },
    )

@pytest.fixture
def mock_plenticore_data() -> dict:
    """Mock Plenticore data."""
    return {
        "devices:local": {
            "P": "5000",
            "Dc_P": "5000",
            "Grid_P": "3000",
            "Home_P": "2000",
        },
        "devices:local:pv1": {
            "P": "2500",
            "U": "400.0",
            "I": "6.25",
        },
        "devices:local:pv2": {
            "P": "2500",
            "U": "400.0",
            "I": "6.25",
        },
        "scb:statistic:EnergyFlow": {
            "Statistic:EnergyGrid:Day": "10.5",
            "Statistic:EnergyPv1:Day": "5.2",
            "Statistic:EnergyPv2:Day": "5.3",
        },
    }

@pytest.fixture
def mock_settings_data() -> dict:
    """Mock settings data."""
    return {
        "devices:local": {
            "Properties:SerialNo": "TEST123",
            "Branding:ProductName1": "Test",
            "Branding:ProductName2": "Inverter",
            "Properties:VersionIOC": "1.0.0",
            "Properties:VersionMC": "2.0.0",
        },
        "scb:network": {
            "Network:Hostname": "test-inverter",
        },
    }

@pytest.fixture
def mock_modules_data() -> list:
    """Mock modules data."""
    return [
        MagicMock(id="devices:local"),
        MagicMock(id="scb:statistic:EnergyFlow"),
        MagicMock(id="scb:event"),
        MagicMock(id="scb:system"),
        MagicMock(id="scb:network"),
    ]

@pytest.fixture
def mock_device_metadata() -> dict:
    """Mock device metadata."""
    return {
        "devices:local": {
            "Properties:SerialNo": "TEST123",
            "Branding:ProductName1": "Test",
            "Branding:ProductName2": "Inverter",
            "Properties:VersionIOC": "1.0.0",
            "Properties:VersionMC": "2.0.0",
        },
        "scb:network": {
            "Network:Hostname": "test-inverter",
        },
    }

# Additional Platinum-specific fixtures
@pytest.fixture
def mock_request_cache():
    """Mock RequestCache for Platinum performance features."""
    from kostal_plenticore.coordinator import RequestCache
    return RequestCache(ttl_seconds=5.0)

@pytest.fixture
def mock_performance_coordinator():
    """Mock performance-optimized coordinator."""
    from kostal_plenticore.coordinator import PlenticoreUpdateCoordinator
    return MagicMock(spec=PlenticoreUpdateCoordinator)
