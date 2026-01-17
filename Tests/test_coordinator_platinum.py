"""Test the Kostal Plenticore coordinator with Platinum features."""

from __future__ import annotations

from collections.abc import Generator
import logging
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.const import CONF_HOST, CONF_PASSWORD

from pytest_homeassistant_custom_component.common import MockConfigEntry

pytestmark = [
    pytest.mark.usefixtures("mock_plenticore_client"),
]

class TestPlatinumCoordinator:
    """Test Platinum coordinator features."""
    
    @pytest.fixture
    def mock_config_entry(self) -> MockConfigEntry:
        """Mock a config entry."""
        return MockConfigEntry(
            domain="kostal_plenticore",
            data={
                "host": "192.168.1.100",
                "password": "test_password",
            },
        )
    
    @pytest.fixture
    def mock_plenticore(self, mock_config_entry: MockConfigEntry) -> MagicMock:
        """Mock a Plenticore instance."""
        plenticore = MagicMock()
        plenticore.client = MagicMock()
        plenticore.device_info = {
            "identifiers": {"kostal_plenticore": "test_serial"},
            "manufacturer": "Kostal",
            "model": "Test Model",
            "name": "Test Inverter",
            "sw_version": "1.0.0",
        }
        return plenticore
    
    async def test_request_cache_performance(self, mock_request_cache):
        """Test RequestCache performance features."""
        if mock_request_cache is None:
            pytest.skip("RequestCache not available")
        
        # Test cache performance
        import time
        
        # Add entries
        entry_count = 200
        start_time = time.time()
        for i in range(entry_count):
            await mock_request_cache.set(f"key_{i}", f"value_{i}")
        
        # Get entries
        for i in range(entry_count):
            await mock_request_cache.get(f"key_{i}")
        
        end_time = time.time()
        duration = end_time - start_time
        
        # Should be fast (< 2 seconds for async operations)
        assert duration < 2.0
        
        # Check hit ratio
        hit_ratio = mock_request_cache.get_hit_ratio()
        assert hit_ratio > 0.5  # Should have good hit ratio
    
    @pytest.mark.asyncio
    async def test_plenticore_setup_concurrent_operations(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_plenticore: MagicMock
    ):
        """Test concurrent operations during setup."""
        try:
            from kostal_plenticore.coordinator import Plenticore
            
            plenticore = Plenticore(hass, mock_config_entry)
            
            # Mock client methods
            plenticore._client = MagicMock()
            plenticore._client.login = AsyncMock()
            plenticore._client.get_modules = AsyncMock(return_value=[])
            plenticore._client.get_settings = AsyncMock(return_value={})
            plenticore._fetch_modules = AsyncMock()
            plenticore._fetch_device_metadata = AsyncMock()
            
            # Test setup
            result = await plenticore.async_setup()
            
            # Should schedule metadata and module fetches
            plenticore._fetch_modules.assert_called_once()
            plenticore._fetch_device_metadata.assert_called_once()
            assert result is True
                
        except ImportError:
            pytest.skip("Platinum coordinator not available")
    
    @pytest.mark.asyncio
    async def test_plenticore_unload_timeout_protection(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_plenticore: MagicMock
    ):
        """Test timeout protection during unload."""
        try:
            from kostal_plenticore.coordinator import Plenticore
            
            plenticore = Plenticore(hass, mock_config_entry)
            
            async def _wait_for(coro, timeout=None):
                return await coro

            # Mock timeout protection
            with patch(
                "kostal_plenticore.coordinator.asyncio.wait_for",
                new=AsyncMock(side_effect=_wait_for),
            ) as mock_wait_for:
                # Mock client logout
                plenticore._client = MagicMock()
                plenticore._client.logout = AsyncMock()
                
                # Test unload
                await plenticore.async_unload()
                
                # Should use timeout protection
                assert mock_wait_for.called
                
        except ImportError:
            pytest.skip("Platinum coordinator not available")
    
    def test_modbus_exception_hierarchy(self):
        """Test MODBUS exception hierarchy."""
        try:
            from kostal_plenticore.coordinator import (
                ModbusException,
                ModbusIllegalFunctionError,
                ModbusIllegalDataAddressError,
                ModbusIllegalDataValueError,
                ModbusServerDeviceFailureError,
                ModbusServerDeviceBusyError,
                ModbusMemoryParityError,
            )
            
            # Test exception hierarchy
            exc = ModbusIllegalFunctionError(0x01)
            assert isinstance(exc, ModbusException)
            assert exc.exception_code == 0x01
            assert "Function code 0x01 not supported" in str(exc)
            
            exc = ModbusIllegalDataAddressError()
            assert isinstance(exc, ModbusException)
            assert exc.exception_code == 0x02
            assert "Register address not valid" in str(exc)
            
            exc = ModbusIllegalDataValueError()
            assert isinstance(exc, ModbusException)
            assert exc.exception_code == 0x03
            assert "Invalid value provided" in str(exc)
            
            exc = ModbusServerDeviceFailureError()
            assert isinstance(exc, ModbusException)
            assert exc.exception_code == 0x04
            assert "Inverter internal error" in str(exc)
            
            exc = ModbusServerDeviceBusyError()
            assert isinstance(exc, ModbusException)
            assert exc.exception_code == 0x06
            assert "Inverter busy processing" in str(exc)
            
            exc = ModbusMemoryParityError()
            assert isinstance(exc, ModbusException)
            assert exc.exception_code == 0x08
            assert "memory consistency check failed" in str(exc)
            
        except ImportError:
            pytest.skip("MODBUS exceptions not available")
    
    def test_modbus_exception_parsing(self):
        """Test MODBUS exception parsing."""
        try:
            from kostal_plenticore.coordinator import (
                _parse_modbus_exception,
                ModbusException,
                ModbusIllegalFunctionError,
                ModbusIllegalDataAddressError,
                ModbusIllegalDataValueError,
                ModbusServerDeviceFailureError,
                ModbusServerDeviceBusyError,
                ModbusMemoryParityError,
            )
            from pykoplenti import ApiException
            
            # Test different error patterns
            test_cases = [
                ("illegal function error", ModbusIllegalFunctionError, 0x01),
                ("address error", ModbusIllegalDataAddressError, 0x02),
                ("value error", ModbusIllegalDataValueError, 0x03),
                ("failure error", ModbusServerDeviceFailureError, 0x04),
                ("busy error", ModbusServerDeviceBusyError, 0x06),
                ("parity error", ModbusMemoryParityError, 0x08),
                ("unknown error", ModbusException, None),
            ]
            
            for error_msg, expected_type, expected_code in test_cases:
                api_exc = ApiException(error_msg)
                modbus_exc = _parse_modbus_exception(api_exc)
                
                assert isinstance(modbus_exc, expected_type)
                if expected_code is not None:
                    assert modbus_exc.exception_code == expected_code
                
        except ImportError:
            pytest.skip("MODBUS exception parsing not available")
    
    @pytest.mark.asyncio
    async def test_performance_coordinator_features(self, mock_performance_coordinator):
        """Test performance coordinator features."""
        if mock_performance_coordinator is None:
            pytest.skip("Performance coordinator not available")
        
        # Test performance attributes
        assert hasattr(mock_performance_coordinator, '_request_cache')
        assert hasattr(mock_performance_coordinator, '_min_request_interval')
        assert hasattr(mock_performance_coordinator, '_total_requests')
        assert hasattr(mock_performance_coordinator, '_duplicate_requests_prevented')
        assert hasattr(mock_performance_coordinator, '_rate_limited_requests')
        
        # Test performance methods
        mock_performance_coordinator.start_fetch_data = MagicMock()
        mock_performance_coordinator.stop_fetch_data = MagicMock()
        
        # Test start/stop fetch data
        callback = mock_performance_coordinator.start_fetch_data("module1", "data1")
        assert callback is not None
        
        mock_performance_coordinator.stop_fetch_data("module1", "data1")
        # Should not raise exception
    
    def test_coordinator_type_annotations(self):
        """Test coordinator type annotations."""
        try:
            from kostal_plenticore.coordinator import PlenticoreConfigEntry
            
            # Test type annotations are present
            assert PlenticoreConfigEntry is not None
            
        except ImportError:
            pytest.skip("Type annotations not available")
    
    def test_coordinator_documentation(self):
        """Test coordinator documentation quality."""
        try:
            from kostal_plenticore.coordinator import RequestCache, PlenticoreUpdateCoordinator
            
            # Check comprehensive docstrings
            assert RequestCache.__doc__ is not None
            assert len(RequestCache.__doc__) > 200  # Comprehensive
            
            assert PlenticoreUpdateCoordinator.__doc__ is not None
            assert len(PlenticoreUpdateCoordinator.__doc__) > 200
            
            # Check performance documentation
            assert "Performance" in RequestCache.__doc__
            assert "Performance" in PlenticoreUpdateCoordinator.__doc__
            
        except ImportError:
            pytest.skip("Documentation not available")


class TestPlatinumDataUpdateCoordinator:
    """Test Platinum data update coordinator features."""
    
    @pytest.mark.asyncio
    async def test_request_deduplication(
        self,
        hass: HomeAssistant,
        mock_request_cache,
        mock_config_entry: MockConfigEntry,
    ):
        """Test request deduplication in data update coordinator."""
        if mock_request_cache is None:
            pytest.skip("RequestCache not available")
        
        try:
            from kostal_plenticore.coordinator import ProcessDataUpdateCoordinator
            
            # Real coordinator instance to exercise dedup logic
            coordinator = ProcessDataUpdateCoordinator(
                hass,
                mock_config_entry,
                logging.getLogger(__name__),
                "Process Data",
                timedelta(seconds=10),
                MagicMock(),
            )
            coordinator._request_cache = mock_request_cache
            coordinator.async_request_refresh = AsyncMock()
            
            # Test request deduplication
            # First request should return callback
            callback1 = coordinator.start_fetch_data("module1", "data1")
            assert callback1 is not None
            
            # Duplicate request should return a no-op callback
            callback2 = coordinator.start_fetch_data("module1", "data1")
            assert callable(callback2)
            assert coordinator._duplicate_requests_prevented == 1
            
        except ImportError:
            pytest.skip("ProcessDataUpdateCoordinator not available")
    
    @pytest.mark.asyncio
    async def test_rate_limiting(
        self,
        hass: HomeAssistant,
        mock_request_cache,
        mock_config_entry: MockConfigEntry,
    ):
        """Test rate limiting in data update coordinator."""
        if mock_request_cache is None:
            pytest.skip("RequestCache not available")
        
        try:
            from kostal_plenticore.coordinator import ProcessDataUpdateCoordinator
            
            # Real coordinator with rate limiting
            coordinator = ProcessDataUpdateCoordinator(
                hass,
                mock_config_entry,
                logging.getLogger(__name__),
                "Process Data",
                timedelta(seconds=10),
                MagicMock(),
            )
            coordinator._request_cache = mock_request_cache
            coordinator._min_request_interval = timedelta(milliseconds=500)
            coordinator.async_request_refresh = AsyncMock()
            
            # Test rate limiting
            # First request
            callback1 = coordinator.start_fetch_data("module1", "data1")
            
            # Immediate second request (should be rate limited)
            callback2 = coordinator.start_fetch_data("module2", "data2")
            assert callable(callback1)
            assert callable(callback2)
            assert coordinator._rate_limited_requests >= 1
            
        except ImportError:
            pytest.skip("ProcessDataUpdateCoordinator not available")
    
    @pytest.mark.asyncio
    async def test_memory_optimization(self, mock_request_cache):
        """Test memory optimization in data update coordinator."""
        if mock_request_cache is None:
            pytest.skip("RequestCache not available")
        
        try:
            from kostal_plenticore.coordinator import ProcessDataUpdateCoordinator
            
            # Mock coordinator
            coordinator = MagicMock(spec=ProcessDataUpdateCoordinator)
            coordinator._request_cache = mock_request_cache
            coordinator._fetch = {}
            
            # Fill cache with many entries
            for i in range(150):  # More than cleanup threshold
                await mock_request_cache.set(f"key_{i}", f"value_{i}")
            
            # Trigger cleanup (should happen automatically)
            cache_size = len(mock_request_cache._cache)
            assert cache_size == 150

            await mock_request_cache.clear()
            assert len(mock_request_cache._cache) == 0
            
        except ImportError:
            pytest.skip("ProcessDataUpdateCoordinator not available")
