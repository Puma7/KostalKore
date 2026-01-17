"""Test configuration for the Kostal Plenticore integration tests.

This configuration file sets up pytest with the necessary fixtures, plugins,
and configuration for comprehensive testing of the Platinum-standard integration.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_asyncio import is_async_test
import pytest_aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.const import CONF_HOST, CONF_PASSWORD

from tests.common import MockPlenticoreClient

# Configure pytest for async testing
pytest_plugins = ("pytest_asyncio", "pytest_aiohttp", "pytest_cov")

# Coverage configuration
COV_MIN_COVERAGE = 95
COV_FAIL_UNDER = 80

# Test configuration
pytest_options = [
    "--cov=kostal_plenticore",
    "--cov-report=term-missing",
    "--cov-report=html",
    "--cov-report=xml",
    "--strict-markers",
    "--disable-warnings",
    "--tb=short",
]

# Logging configuration for tests
logging.basicConfig(level=logging.DEBUG)


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def aiohttp_client(event_loop):
    """Create an aiohttp client for testing."""
    return pytest_aiohttp.AsyncClient(loop=event_loop)


@pytest.fixture
def mock_plenticore_client():
    """Create a mock Plenticore client for testing."""
    return MockPlenticoreClient()


@pytest.fixture
def mock_plenticore():
    """Create a mock Plenticore instance for testing."""
    plenticore = MagicMock()
    plenticore.client = MockPlenticoreClient()
    plenticore.device_info = {
        "identifiers": {"kostal_plenticore": "test_serial"},
        "manufacturer": "Kostal",
        "model": "Test Model",
        "name": "Test Inverter",
        "sw_version": "1.0.0",
    }
    return plenticore


@pytest.fixture
async def hass():
    """Create a Home Assistant instance for testing."""
    hass = HomeAssistant()
    await async_setup_component(hass, {})
    return hass


@pytest.fixture
def mock_config_entry():
    """Create a mock configuration entry."""
    from homeassistant.config_entries import ConfigEntry
    from .const import DOMAIN
    
    return ConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Test Kostal Plenticore",
        data={
            CONF_HOST: "192.168.1.100",
            CONF_PASSWORD: "test_password",
        },
        source="test",
    )


@pytest.fixture
def mock_available_modules():
    """Mock available modules data."""
    return [
        MagicMock(id="devices:local"),
        MagicMock(id="scb:statistic:EnergyFlow"),
        MagicMock(id="scb:event"),
        MagicMock(id="scb:system"),
        MagicMock(id="scb:network"),
    ]


@pytest.fixture
def mock_device_metadata():
    """Mock device metadata for testing."""
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
def mock_process_data():
    """Mock process data for testing."""
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
def mock_settings_data():
    """Mock settings data for testing."""
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


# Performance test fixtures
@pytest.fixture
def performance_monitor():
    """Monitor performance during tests."""
    from collections import defaultdict
    
    metrics = defaultdict(list)
    
    class PerformanceMonitor:
        def __init__(self):
            self.start_time = None
            self.end_time = None
            
        def start(self):
            self.start_time = asyncio.get_event_loop().time()
            
        def stop(self):
            self.end_time = asyncio.get_event_loop().time()
            
        @property
        def duration(self):
            if self.start_time and self.end_time:
                return (self.end_time - self.start_time).total_seconds()
            return 0
            
        def record_metric(self, name: str, value: float):
            metrics[name].append(value)
            
        def get_metrics(self):
            return dict(metrics)
    
    return PerformanceMonitor()


# Mock patch fixtures
@pytest.fixture
def patch_asyncio_client():
    """Patch aiohttp client for testing."""
    with patch("aiohttp.ClientSession") as mock_session:
        yield mock_session


@pytest.fixture
def patch_pykoplenti():
    """Patch pykoplenti module for testing."""
    with patch("pykoplenti.ApiClient") as mock_client:
        yield mock_client


@pytest.fixture
def patch_timeouts():
    """Patch time-related functions for testing."""
    with patch("time.sleep") as mock_sleep, \
         patch("datetime.datetime.now") as mock_now:
        
        # Mock time progression
        current_time = 1609459200.0
        def time_side_effect(seconds):
            nonlocal current_time
            current_time += seconds
            return current_time
            
        mock_sleep.side_effect = time_side_effect
        mock_now.return_value = datetime.fromtimestamp(current_time)
        
        yield mock_sleep, mock_now
