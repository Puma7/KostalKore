"""Tests for the Kostal Plenticore coordinator module.

This test suite provides comprehensive coverage for the coordinator module,
including unit tests for the RequestCache, PlenticoreUpdateCoordinator,
and related classes. Tests cover normal operation, error handling, and
performance optimization features.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryNotReady

from pykoplenti import ApiException, AuthenticationException

from .common import (
    MockPlenticoreClient,
    create_mock_modules,
    create_mock_process_data,
    create_mock_settings_data,
    assert_cache_hit_ratio,
    assert_performance_metrics,
    wait_for_condition,
)
from ..coordinator import (
    RequestCache,
    Plenticore,
    PlenticoreUpdateCoordinator,
    ProcessDataUpdateCoordinator,
    SettingDataUpdateCoordinator,
    ModbusException,
    ModbusIllegalFunctionError,
    ModbusIllegalDataAddressError,
    ModbusIllegalDataValueError,
    ModbusServerDeviceFailureError,
    ModbusServerDeviceBusyError,
    ModbusMemoryParityError,
    _parse_modbus_exception,
)


class TestRequestCache:
    """Test the RequestCache class for performance optimization."""
    
    def test_cache_initialization(self) -> None:
        """Test cache initialization with default TTL."""
        cache = RequestCache()
        
        assert cache._ttl == timedelta(seconds=5.0)
        assert cache._cache == {}
        assert cache._timestamps == {}
        assert cache._hits == 0
        assert cache._misses == 0
    
    def test_cache_initialization_custom_ttl(self) -> None:
        """Test cache initialization with custom TTL."""
        custom_ttl = 10.0
        cache = RequestCache(ttl_seconds=custom_ttl)
        
        assert cache._ttl == timedelta(seconds=custom_ttl)
    
    def test_cache_set_and_get_valid(self) -> None:
        """Test setting and getting valid cached values."""
        cache = RequestCache(ttl_seconds=5.0)
        key = "test_key"
        value = "test_value"
        
        cache.set(key, value)
        result = cache.get(key)
        
        assert result == value
        assert cache._hits == 1
        assert cache._misses == 0
    
    def test_cache_get_miss(self) -> None:
        """Test getting non-existent cached values."""
        cache = RequestCache()
        
        result = cache.get("non_existent_key")
        
        assert result is None
        assert cache._hits == 0
        assert cache._misses == 1
    
    def test_cache_get_expired(self) -> None:
        """Test getting expired cached values."""
        cache = RequestCache(ttl_seconds=0.1)  # Very short TTL
        key = "test_key"
        value = "test_value"
        
        cache.set(key, value)
        
        # Wait for cache to expire
        asyncio.sleep(0.2)
        
        result = cache.get(key)
        
        assert result is None
        assert cache._hits == 0
        assert cache._misses == 1
        assert key not in cache._cache
        assert key not in cache._timestamps
    
    def test_cache_invalidate(self) -> None:
        """Test cache invalidation."""
        cache = RequestCache()
        key = "test_key"
        value = "test_value"
        
        cache.set(key, value)
        cache.invalidate(key)
        
        result = cache.get(key)
        
        assert result is None
        assert key not in cache._cache
        assert key not in cache._timestamps
    
    def test_cache_clear(self) -> None:
        """Test cache clearing."""
        cache = RequestCache()
        
        # Add some data
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        
        # Clear cache
        cache.clear()
        
        assert cache._cache == {}
        assert cache._timestamps == {}
        assert cache._hits == 0
        assert cache._misses == 0
    
    def test_cache_hit_ratio(self) -> None:
        """Test cache hit ratio calculation."""
        cache = RequestCache()
        
        # Add some data
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        
        # Get some hits
        cache.get("key1")
        cache.get("key2")
        
        # Get some misses
        cache.get("key3")
        cache.get("key4")
        
        hit_ratio = cache.get_hit_ratio()
        assert hit_ratio == 0.5  # 2 hits out of 4 total requests
    
    def test_cache_hit_ratio_empty(self) -> None:
        """Test cache hit ratio with no requests."""
        cache = RequestCache()
        
        hit_ratio = cache.get_hit_ratio()
        assert hit_ratio == 0.0
    
    def test_cache_performance(self, performance_monitor) -> None:
        """Test cache performance with many operations."""
        cache = RequestCache()
        
        performance_monitor.start()
        
        # Add 1000 entries
        for i in range(1000):
            cache.set(f"key_{i}", f"value_{i}")
        
        # Get 1000 entries (should all be hits)
        for i in range(1000):
            cache.get(f"key_{i}")
        
        performance_monitor.stop()
        performance_monitor.record_metric("cache_operations", 2000)
        
        # Performance assertions
        assert performance_monitor.duration < 1.0  # Should complete in < 1 second
        assert cache.get_hit_ratio() > 0.99  # Should have > 99% hit ratio


class TestModbusExceptions:
    """Test MODBUS exception classes and parsing."""
    
    def test_modbus_exception_base(self) -> None:
        """Test base MODBUS exception."""
        message = "Test error"
        exception_code = 0x01
        
        exc = ModbusException(message, exception_code)
        
        assert str(exc) == message
        assert exc.exception_code == exception_code
        assert exc.message == message
    
    def test_modbus_exception_no_code(self) -> None:
        """Test MODBUS exception without code."""
        message = "Test error"
        
        exc = ModbusException(message)
        
        assert str(exc) == message
        assert exc.exception_code is None
        assert exc.message == message
    
    def test_modbus_illegal_function_error(self) -> None:
        """Test illegal function error."""
        function_code = 0x01
        
        exc = ModbusIllegalFunctionError(function_code)
        
        assert str(exc) == "Function code 0x01 not supported by inverter"
        assert exc.exception_code == 0x01
    
    def test_modbus_illegal_data_address_error(self) -> None:
        """Test illegal data address error."""
        exc = ModbusIllegalDataAddressError()
        
        assert str(exc) == "Register address not valid for this inverter model"
        assert exc.exception_code == 0x02
    
    def test_modbus_illegal_data_value_error(self) -> None:
        """Test illegal data value error."""
        exc = ModbusIllegalDataValueError()
        
        assert str(exc) == "Invalid value provided"
        assert exc.exception_code == 0x03
    
    def test_modbus_server_device_failure_error(self) -> None:
        """Test server device failure error."""
        exc = ModbusServerDeviceFailureError()
        
        assert str(exc) == "Inverter internal error during operation"
        assert exc.exception_code == 0x04
    
    def test_modbus_server_device_busy_error(self) -> None:
        """Test server device busy error."""
        exc = ModbusServerDeviceBusyError()
        
        assert str(exc) == "Inverter busy processing long command, retry later"
        assert exc.exception_code == 0x06
    
    def test_modbus_memory_parity_error(self) -> None:
        """Test memory parity error."""
        exc = ModbusMemoryParityError()
        
        assert str(exc) == "Inverter memory consistency check failed"
        assert exc.exception_code == 0x08
    
    def test_parse_modbus_exception_illegal_function(self) -> None:
        """Test parsing illegal function exception."""
        api_exception = ApiException("illegal function error")
        
        result = _parse_modbus_exception(api_exception)
        
        assert isinstance(result, ModbusIllegalFunctionError)
        assert result.exception_code == 0x01
    
    def test_parse_modbus_exception_illegal_address(self) -> None:
        """Test parsing illegal address exception."""
        api_exception = ApiException("address error")
        
        result = _parse_modbus_exception(api_exception)
        
        assert isinstance(result, ModbusIllegalDataAddressError)
        assert result.exception_code == 0x02
    
    def test_parse_modbus_exception_illegal_value(self) -> None:
        """Test parsing illegal value exception."""
        api_exception = ApiException("value error")
        
        result = _parse_modbus_exception(api_exception)
        
        assert isinstance(result, ModbusIllegalDataValueError)
        assert result.exception_code == 0x03
    
    def test_parse_modbus_exception_server_failure(self) -> None:
        """Test parsing server failure exception."""
        api_exception = ApiException("failure error")
        
        result = _parse_modbus_exception(api_exception)
        
        assert isinstance(result, ModbusServerDeviceFailureError)
        assert result.exception_code == 0x04
    
    def test_parse_modbus_exception_server_busy(self) -> None:
        """Test parsing server busy exception."""
        api_exception = ApiException("busy error")
        
        result = _parse_modbus_exception(api_exception)
        
        assert isinstance(result, ModbusServerDeviceBusyError)
        assert result.exception_code == 0x06
    
    def test_parse_modbus_exception_memory_parity(self) -> None:
        """Test parsing memory parity exception."""
        api_exception = ApiException("parity error")
        
        result = _parse_modbus_exception(api_exception)
        
        assert isinstance(result, ModbusMemoryParityError)
        assert result.exception_code == 0x08
    
    def test_parse_modbus_exception_generic(self) -> None:
        """Test parsing generic exception."""
        api_exception = ApiException("unknown error")
        
        result = _parse_modbus_exception(api_exception)
        
        assert isinstance(result, ModbusException)
        assert "MODBUS communication error" in str(result)


class TestPlenticore:
    """Test the Plenticore class."""
    
    @pytest.fixture
    def mock_config_entry(self) -> ConfigEntry:
        """Create a mock config entry."""
        return ConfigEntry(
            version=1,
            domain="kostal_plenticore",
            title="Test",
            data={
                CONF_HOST: "192.168.1.100",
                CONF_PASSWORD: "test_password",
            },
            source="test",
        )
    
    @pytest.fixture
    def mock_hass(self) -> HomeAssistant:
        """Create a mock Home Assistant instance."""
        hass = MagicMock()
        hass.bus.async_listen_once = MagicMock()
        return hass
    
    @pytest.fixture
    def mock_client(self) -> MockPlenticoreClient:
        """Create a mock client."""
        return MockPlenticoreClient()
    
    @pytest.fixture
    def plenticore(self, mock_hass: HomeAssistant, mock_config_entry: ConfigEntry) -> Plenticore:
        """Create a Plenticore instance."""
        return Plenticore(mock_hass, mock_config_entry)
    
    def test_plenticore_initialization(self, plenticore: Plenticore) -> None:
        """Test Plenticore initialization."""
        assert plenticore.hass is not None
        assert plenticore.config_entry is not None
        assert plenticore._client is None
        assert plenticore._shutdown_remove_listener is None
        assert plenticore.device_info == {}
        assert plenticore.available_modules == []
    
    def test_plenticore_host_property(self, plenticore: Plenticore) -> None:
        """Test host property."""
        assert plenticore.host == "192.168.1.100"
    
    def test_plenticore_client_property_no_client(self, plenticore: Plenticore) -> None:
        """Test client property when no client is set."""
        with pytest.raises(AttributeError):
            _ = plenticore.client
    
    @pytest.mark.asyncio
    async def test_plenticore_async_setup_success(
        self, plenticore: Plenticore, mock_client: MockPlenticoreClient
    ) -> None:
        """Test successful async setup."""
        with patch("pykoplenti.ExtendedApiClient", return_value=mock_client):
            result = await plenticore.async_setup()
            
            assert result is True
            assert plenticore._client is mock_client
            assert plenticore._shutdown_remove_listener is not None
    
    @pytest.mark.asyncio
    async def test_plenticore_async_setup_auth_failure(
        self, plenticore: Plenticore, mock_client: MockPlenticoreClient
    ) -> None:
        """Test async setup with authentication failure."""
        mock_client.set_should_fail_login(True)
        
        with patch("pykoplenti.ExtendedApiClient", return_value=mock_client):
            result = await plenticore.async_setup()
            
            assert result is False
            assert plenticore._client is mock_client
    
    @pytest.mark.asyncio
    async def test_plenticore_async_setup_network_error(
        self, plenticore: Plenticore, mock_client: MockPlenticoreClient
    ) -> None:
        """Test async setup with network error."""
        mock_client.set_timeout(True)
        
        with patch("pykoplenti.ExtendedApiClient", return_value=mock_client):
            with pytest.raises(ConfigEntryNotReady):
                await plenticore.async_setup()
    
    @pytest.mark.asyncio
    async def test_plenticore_async_setup_api_error(
        self, plenticore: Plenticore, mock_client: MockPlenticoreClient
    ) -> None:
        """Test async setup with API error."""
        mock_client.set_should_fail_login(True)
        
        with patch("pykoplenti.ExtendedApiClient", return_value=mock_client):
            with patch("pykoplenti.ApiException"):
                with pytest.raises(ConfigEntryNotReady):
                    await plenticore.async_setup()
    
    @pytest.mark.asyncio
    async def test_plenticore_async_setup_concurrent_operations(
        self, plenticore: Plenticore, mock_client: MockPlenticoreClient
    ) -> None:
        """Test concurrent operations during setup."""
        mock_client.set_modules(create_mock_modules(["devices:local", "scb:system"]))
        mock_client.set_settings_data({
            "devices:local": {"Properties:SerialNo": "TEST123"},
            "scb:network": {"Network:Hostname": "test"},
        })
        
        with patch("pykoplenti.ExtendedApiClient", return_value=mock_client):
            start_time = datetime.now()
            result = await plenticore.async_setup()
            duration = (datetime.now() - start_time).total_seconds()
            
            assert result is True
            assert duration < 2.0  # Should complete quickly with concurrent operations
            assert len(mock_client.get_method_calls()) >= 4  # login, modules, settings, hostname
    
    @pytest.mark.asyncio
    async def test_plenticore_async_unload(
        self, plenticore: Plenticore, mock_client: MockPlenticoreClient
    ) -> None:
        """Test async unload."""
        # Setup first
        with patch("pykoplenti.ExtendedApiClient", return_value=mock_client):
            await plenticore.async_setup()
        
        # Then unload
        await plenticore.async_unload()
        
        assert plenticore._client is None
        assert mock_client.get_method_calls()[-1] == ("logout",)
    
    @pytest.mark.asyncio
    async def test_plenticore_async_unload_with_listener(
        self, plenticore: Plenticore, mock_client: MockPlenticoreClient
    ) -> None:
        """Test async unload with shutdown listener."""
        # Setup first
        with patch("pykoplenti.ExtendedApiClient", return_value=mock_client):
            await plenticore.async_setup()
        
        mock_listener = MagicMock()
        plenticore._shutdown_remove_listener = mock_listener
        
        # Then unload
        await plenticore.async_unload()
        
        mock_listener.assert_called_once()
        assert plenticore._client is None
    
    @pytest.mark.asyncio
    async def test_plenticore_async_unload_error_handling(
        self, plenticore: Plenticore, mock_client: MockPlenticoreClient
    ) -> None:
        """Test async unload with error handling."""
        # Setup first
        with patch("pykoplenti.ExtendedApiClient", return_value=mock_client):
            await plenticore.async_setup()
        
        # Make logout fail
        mock_client.set_should_fail_login(True)
        
        # Then unload (should not raise exception)
        await plenticore.async_unload()
        
        assert plenticore._client is None


class TestPlenticoreUpdateCoordinator:
    """Test the PlenticoreUpdateCoordinator class."""
    
    @pytest.fixture
    def mock_hass(self) -> HomeAssistant:
        """Create a mock Home Assistant instance."""
        hass = MagicMock()
        return hass
    
    @pytest.fixture
    def mock_config_entry(self) -> ConfigEntry:
        """Create a mock config entry."""
        return ConfigEntry(
            version=1,
            domain="kostal_plenticore",
            title="Test",
            data={},
            source="test",
        )
    
    @pytest.fixture
    def mock_plenticore(self) -> MagicMock:
        """Create a mock Plenticore instance."""
        plenticore = MagicMock()
        plenticore.client = MockPlenticoreClient()
        return plenticore
    
    @pytest.fixture
    def coordinator(
        self, mock_hass: HomeAssistant, mock_config_entry: ConfigEntry, mock_plenticore: MagicMock
    ) -> PlenticoreUpdateCoordinator:
        """Create a coordinator instance."""
        from datetime import timedelta
        import logging
        
        return PlenticoreUpdateCoordinator(
            hass=mock_hass,
            config_entry=mock_config_entry,
            logger=logging.getLogger(__name__),
            name="test_coordinator",
            update_inverval=timedelta(seconds=10),
            plenticore=mock_plenticore,
        )
    
    def test_coordinator_initialization(self, coordinator: PlenticoreUpdateCoordinator) -> None:
        """Test coordinator initialization."""
        assert coordinator._fetch == {}
        assert coordinator._plenticore is not None
        assert coordinator._request_cache is not None
        assert coordinator._min_request_interval == timedelta(milliseconds=500)
        assert coordinator._total_requests == 0
        assert coordinator._duplicate_requests_prevented == 0
        assert coordinator._rate_limited_requests == 0
    
    def test_coordinator_start_fetch_data_new_request(self, coordinator: PlenticoreUpdateCoordinator) -> None:
        """Test starting fetch data for new request."""
        callback = coordinator.start_fetch_data("module1", "data1")
        
        assert "module1" in coordinator._fetch
        assert "data1" in coordinator._fetch["module1"]
        assert coordinator._total_requests == 1
        assert coordinator._duplicate_requests_prevented == 0
        assert callback is not None
    
    def test_coordinator_start_fetch_data_duplicate_request(self, coordinator: PlenticoreUpdateCoordinator) -> None:
        """Test starting fetch data for duplicate request."""
        # First request
        callback1 = coordinator.start_fetch_data("module1", "data1")
        
        # Duplicate request
        callback2 = coordinator.start_fetch_data("module1", "data1")
        
        assert coordinator._total_requests == 2
        assert coordinator._duplicate_requests_prevented == 1
        assert callback2 is None  # Should return no-op callback
    
    def test_coordinator_start_fetch_data_rate_limiting(
        self, coordinator: PlenticoreUpdateCoordinator, performance_monitor
    ) -> None:
        """Test rate limiting of fetch data requests."""
        performance_monitor.start()
        
        # First request
        callback1 = coordinator.start_fetch_data("module1", "data1")
        
        # Immediate second request (should be rate limited)
        callback2 = coordinator.start_fetch_data("module2", "data2")
        
        performance_monitor.stop()
        
        assert coordinator._rate_limited_requests == 1
        assert callback2 is not None  # Should return delayed callback
        assert performance_monitor.duration < 0.1  # Should be very fast
    
    def test_coordinator_stop_fetch_data(self, coordinator: PlenticoreUpdateCoordinator) -> None:
        """Test stopping fetch data."""
        # Start fetching
        coordinator.start_fetch_data("module1", "data1")
        coordinator.start_fetch_data("module1", "data2")
        
        # Stop fetching
        coordinator.stop_fetch_data("module1", "data1")
        
        assert "data1" not in coordinator._fetch["module1"]
        assert "data2" in coordinator._fetch["module1"]
    
    def test_coordinator_stop_fetch_data_nonexistent(self, coordinator: PlenticoreUpdateCoordinator) -> None:
        """Test stopping fetch data for non-existent data."""
        # Should not raise exception
        coordinator.stop_fetch_data("module1", "nonexistent")
        coordinator.stop_fetch_data("nonexistent", "data1")
    
    def test_coordinator_performance_monitoring(self, coordinator: PlenticoreUpdateCoordinator) -> None:
        """Test performance monitoring capabilities."""
        # Simulate some requests
        coordinator.start_fetch_data("module1", "data1")
        coordinator.start_fetch_data("module1", "data2")  # Duplicate
        coordinator.start_fetch_data("module2", "data1")
        
        # Check metrics
        assert coordinator._total_requests == 3
        assert coordinator._duplicate_requests_prevented == 1
        assert coordinator._rate_limited_requests == 0
        
        # Check cache performance
        cache = coordinator._request_cache
        assert cache.get_hit_ratio() >= 0.0


class TestProcessDataUpdateCoordinator:
    """Test the ProcessDataUpdateCoordinator class."""
    
    @pytest.fixture
    def mock_hass(self) -> HomeAssistant:
        """Create a mock Home Assistant instance."""
        hass = MagicMock()
        return hass
    
    @pytest.fixture
    def mock_config_entry(self) -> ConfigEntry:
        """Create a mock config entry."""
        return ConfigEntry(
            version=1,
            domain="kostal_plenticore",
            title="Test",
            data={},
            source="test",
        )
    
    @pytest.fixture
    def mock_plenticore(self) -> MagicMock:
        """Create a mock Plenticore instance."""
        plenticore = MagicMock()
        plenticore.client = MockPlenticoreClient()
        return plenticore
    
    @pytest.fixture
    def coordinator(
        self, mock_hass: HomeAssistant, mock_config_entry: ConfigEntry, mock_plenticore: MagicMock
    ) -> ProcessDataUpdateCoordinator:
        """Create a process data coordinator instance."""
        from datetime import timedelta
        import logging
        
        return ProcessDataUpdateCoordinator(
            hass=mock_hass,
            config_entry=mock_config_entry,
            logger=logging.getLogger(__name__),
            name="test_process_coordinator",
            update_inverval=timedelta(seconds=10),
            plenticore=mock_plenticore,
        )
    
    @pytest.mark.asyncio
    async def test_process_data_update_no_fetch(self, coordinator: ProcessDataUpdateCoordinator) -> None:
        """Test update when no data is being fetched."""
        result = await coordinator._async_update_data()
        
        assert result == {}
    
    @pytest.mark.asyncio
    async def test_process_data_update_no_client(self, coordinator: ProcessDataUpdateCoordinator) -> None:
        """Test update when client is None."""
        coordinator._plenticore.client = None
        
        result = await coordinator._async_update_data()
        
        assert result == {}
    
    @pytest.mark.asyncio
    async def test_process_data_update_success(
        self, coordinator: ProcessDataUpdateCoordinator, mock_process_data: dict
    ) -> None:
        """Test successful process data update."""
        # Setup mock data
        mock_client = coordinator._plenticore.client
        mock_client.set_process_data(create_mock_process_data(mock_process_data))
        
        # Start fetching data
        coordinator.start_fetch_data("devices:local", "P")
        coordinator.start_fetch_data("devices:local:pv1", "P")
        
        # Update data
        result = await coordinator._async_update_data()
        
        assert "devices:local" in result
        assert "devices:local:pv1" in result
        assert result["devices:local"]["P"] == "5000"
        assert result["devices:local:pv1"]["P"] == "2500"
    
    @pytest.mark.asyncio
    async def test_process_data_update_cache_hit(
        self, coordinator: ProcessDataUpdateCoordinator, mock_process_data: dict
    ) -> None:
        """Test process data update with cache hit."""
        # Setup mock data
        mock_client = coordinator._plenticore.client
        mock_client.set_process_data(create_mock_process_data(mock_process_data))
        
        # Start fetching data
        coordinator.start_fetch_data("devices:local", "P")
        
        # First update (cache miss)
        result1 = await coordinator._async_update_data()
        
        # Second update (cache hit)
        result2 = await coordinator._async_update_data()
        
        assert result1 == result2
        assert coordinator._request_cache.get_hit_ratio() > 0.0
    
    @pytest.mark.asyncio
    async def test_process_data_update_timeout(
        self, coordinator: ProcessDataUpdateCoordinator
    ) -> None:
        """Test process data update with timeout."""
        # Setup timeout
        mock_client = coordinator._plenticore.client
        mock_client.set_timeout(True)
        
        # Start fetching data
        coordinator.start_fetch_data("devices:local", "P")
        
        # Update should fail with timeout
        from homeassistant.helpers.update_coordinator import UpdateFailed
        
        with pytest.raises(UpdateFailed, match="Timeout fetching process data"):
            await coordinator._async_update_data()
    
    @pytest.mark.asyncio
    async def test_process_data_update_api_error(
        self, coordinator: ProcessDataUpdateCoordinator
    ) -> None:
        """Test process data update with API error."""
        # Setup API error
        mock_client = coordinator._plenticore.client
        mock_client.set_should_fail_process_data(True)
        
        # Start fetching data
        coordinator.start_fetch_data("devices:local", "P")
        
        # Update should fail
        from homeassistant.helpers.update_coordinator import UpdateFailed
        
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()
    
    @pytest.mark.asyncio
    async def test_process_data_update_memory_cleanup(
        self, coordinator: ProcessDataUpdateCoordinator, mock_process_data: dict
    ) -> None:
        """Test memory cleanup during update."""
        # Setup mock data
        mock_client = coordinator._plenticore.client
        mock_client.set_process_data(create_mock_process_data(mock_process_data))
        
        # Fill cache with many entries
        for i in range(150):  # More than cleanup threshold
            coordinator.start_fetch_data(f"module_{i}", f"data_{i}")
        
        # Update should trigger cleanup
        await coordinator._async_update_data()
        
        # Cache should be cleaned up
        assert len(coordinator._request_cache._cache) < 100


# Performance tests
class TestPerformanceOptimizations:
    """Test performance optimization features."""
    
    @pytest.mark.asyncio
    async def test_request_deduplication_performance(self, performance_monitor) -> None:
        """Test request deduplication performance."""
        cache = RequestCache(ttl_seconds=5.0)
        
        performance_monitor.start()
        
        # Add 1000 unique entries
        for i in range(1000):
            cache.set(f"key_{i}", f"value_{i}")
        
        # Get 1000 entries (should all be hits)
        for i in range(1000):
            cache.get(f"key_{i}")
        
        # Get 500 duplicate entries (should all be hits)
        for i in range(500):
            cache.get(f"key_{i}")
        
        performance_monitor.stop()
        
        # Performance assertions
        assert performance_monitor.duration < 0.5  # Should be very fast
        assert cache.get_hit_ratio() > 0.75  # Should have good hit ratio
        assert_cache_hit_ratio(cache, 0.75)
    
    @pytest.mark.asyncio
    async def test_coordinator_rate_limiting_performance(
        self, coordinator: PlenticoreUpdateCoordinator, performance_monitor
    ) -> None:
        """Test coordinator rate limiting performance."""
        performance_monitor.start()
        
        # Make many rapid requests
        for i in range(100):
            coordinator.start_fetch_data(f"module_{i}", f"data_{i}")
        
        performance_monitor.stop()
        
        # Should have rate limited some requests
        assert coordinator._rate_limited_requests > 0
        assert performance_monitor.duration < 1.0  # Should be fast despite rate limiting
    
    @pytest.mark.asyncio
    async def test_batch_entity_creation_performance(
        self, performance_monitor, mock_plenticore
    ) -> None:
        """Test batch entity creation performance."""
        from ..sensor import create_entities_batch
        from ..sensor import PlenticoreSensorEntityDescription
        from datetime import timedelta
        
        # Create many descriptions
        descriptions = []
        for i in range(100):
            desc = PlenticoreSensorEntityDescription(
                key=f"data_{i}",
                name=f"Sensor {i}",
                module_id=f"module_{i}",
                native_unit_of_measurement="W",
                device_class=None,
                state_class=None,
                entity_category=None,
                icon=None,
                entity_registry_enabled_default=True,
                suggested_display_precision=None,
                suggested_unit_of_measurement=None,
            )
            descriptions.append(desc)
        
        performance_monitor.start()
        
        # Create entities in batch
        entities = create_entities_batch(
            coordinator=None,  # Not needed for this test
            descriptions=descriptions,
            available_process_data={},
            entry=None,  # Not needed for this test
            plenticore=mock_plenticore,
        )
        
        performance_monitor.stop()
        
        # Performance assertions
        assert len(entities) == 100
        assert performance_monitor.duration < 0.5  # Should be fast
        performance_monitor.record_metric("entities_created", len(entities))
        assert_performance_metrics(performance_monitor, max_duration=0.5)


# Integration tests
class TestCoordinatorIntegration:
    """Integration tests for coordinator components."""
    
    @pytest.mark.asyncio
    async def test_full_coordinator_workflow(
        self, mock_hass: HomeAssistant, mock_config_entry: ConfigEntry, mock_plenticore: MagicMock
    ) -> None:
        """Test full coordinator workflow from setup to data updates."""
        from datetime import timedelta
        import logging
        
        # Setup coordinator
        coordinator = ProcessDataUpdateCoordinator(
            hass=mock_hass,
            config_entry=mock_config_entry,
            logger=logging.getLogger(__name__),
            name="test_coordinator",
            update_inverval=timedelta(seconds=10),
            plenticore=mock_plenticore,
        )
        
        # Setup mock data
        mock_client = mock_plenticore.client
        mock_client.set_process_data(create_mock_process_data({
            "devices:local": {"P": "5000"},
            "devices:local:pv1": {"P": "2500"},
        }))
        
        # Start fetching data
        coordinator.start_fetch_data("devices:local", "P")
        coordinator.start_fetch_data("devices:local:pv1", "P")
        
        # Update data
        result = await coordinator._async_update_data()
        
        # Verify results
        assert "devices:local" in result
        assert "devices:local:pv1" in result
        assert result["devices:local"]["P"] == "5000"
        assert result["devices:local:pv1"]["P"] == "2500"
        
        # Stop fetching
        coordinator.stop_fetch_data("devices:local", "P")
        coordinator.stop_fetch_data("devices:local:pv1", "P")
    
    @pytest.mark.asyncio
    async def test_coordinator_error_recovery(
        self, mock_hass: HomeAssistant, mock_config_entry: ConfigEntry, mock_plenticore: MagicMock
    ) -> None:
        """Test coordinator error recovery."""
        from datetime import timedelta
        import logging
        from homeassistant.helpers.update_coordinator import UpdateFailed
        
        # Setup coordinator
        coordinator = ProcessDataUpdateCoordinator(
            hass=mock_hass,
            config_entry=mock_config_entry,
            logger=logging.getLogger(__name__),
            name="test_coordinator",
            update_inverval=timedelta(seconds=10),
            plenticore=mock_plenticore,
        )
        
        # Setup mock client to fail
        mock_client = mock_plenticore.client
        mock_client.set_should_fail_process_data(True)
        
        # Start fetching data
        coordinator.start_fetch_data("devices:local", "P")
        
        # Update should fail
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()
        
        # Fix the client
        mock_client.set_should_fail_process_data(False)
        mock_client.set_process_data(create_mock_process_data({
            "devices:local": {"P": "5000"},
        }))
        
        # Update should now succeed
        result = await coordinator._async_update_data()
        assert result["devices:local"]["P"] == "5000"
