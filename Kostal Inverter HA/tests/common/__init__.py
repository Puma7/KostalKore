"""Common test utilities and mock classes for Kostal Plenticore integration tests.

This module provides reusable test utilities, mock classes, and helper functions
to support comprehensive testing of the Platinum-standard integration.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, AsyncMock
from collections.abc import Mapping
import asyncio
from datetime import datetime

from pykoplenti import ApiException


class MockPlenticoreClient:
    """Mock Plenticore API client for testing.
    
    This mock client simulates the behavior of the real Plenticore API client
    without requiring actual network communication. It supports all the methods
    used by the integration with realistic responses and error simulation.
    """
    
    def __init__(self) -> None:
        """Initialize the mock client."""
        self._modules = []
        self._settings_data = {}
        self._process_data = {}
        self._version = "1.0.0"
        self._me = "test_inverter"
        self._should_fail_login = False
        self._should_fail_modules = False
        self._should_fail_settings = False
        self._should_fail_process_data = False
        self._should_fail_version = False
        self._should_fail_me = False
        self._timeout = False
        
        # Track method calls for testing
        self._method_calls = []
        self._last_error = None
    
    async def login(self, password: str, service_code: str | None = None) -> None:
        """Mock login method."""
        self._method_calls.append(("login", password, service_code))
        
        if self._should_fail_login:
            raise ApiException("Authentication failed")
        
        if self._timeout:
            raise asyncio.TimeoutError("Login timeout")
    
    async def logout(self) -> None:
        """Mock logout method."""
        self._method_calls.append(("logout",))
        
        if self._timeout:
            raise asyncio.TimeoutError("Logout timeout")
    
    async def get_modules(self) -> list[Any]:
        """Mock get_modules method."""
        self._method_calls.append(("get_modules",))
        
        if self._should_fail_modules:
            raise ApiException("Failed to get modules")
        
        if self._timeout:
            raise asyncio.TimeoutError("Get modules timeout")
        
        return self._modules
    
    async def get_settings(self) -> Mapping[str, Mapping[Any, Any]]:
        """Mock get_settings method."""
        self._method_calls.append(("get_settings",))
        
        if self._should_fail_settings:
            raise ApiException("Failed to get settings")
        
        if self._timeout:
            raise asyncio.TimeoutError("Get settings timeout")
        
        return self._settings_data
    
    async def get_setting_values(
        self, module_id: str, data_ids: str | tuple[str, ...] | dict[str, str | tuple[str, ...]]
    ) -> Mapping[str, Mapping[str, str]]:
        """Mock get_setting_values method."""
        self._method_calls.append(("get_setting_values", module_id, data_ids))
        
        if self._should_fail_settings:
            raise ApiException("Failed to get setting values")
        
        if self._timeout:
            raise asyncio.TimeoutError("Get setting values timeout")
        
        # Return mock data based on module_id
        if module_id in self._settings_data:
            if isinstance(data_ids, str):
                data_ids = (data_ids,)
            
            result = {}
            module_data = self._settings_data[module_id]
            for data_id in data_ids:
                if data_id in module_data:
                    result[data_id] = module_data[data_id]
            return result
        
        return {}
    
    async def get_process_data(self) -> Mapping[str, Mapping[Any, Any]]:
        """Mock get_process_data method."""
        self._method_calls.append(("get_process_data",))
        
        if self._should_fail_process_data:
            raise ApiException("Failed to get process data")
        
        if self._timeout:
            raise asyncio.TimeoutError("Get process data timeout")
        
        return self._process_data
    
    async def get_process_data_values(
        self, modules: dict[str, list[str]]
    ) -> Mapping[str, Mapping[str, str]]:
        """Mock get_process_data_values method."""
        self._method_calls.append(("get_process_data_values", modules))
        
        if self._should_fail_process_data:
            raise ApiException("Failed to get process data values")
        
        if self._timeout:
            raise asyncio.TimeoutError("Get process data values timeout")
        
        result = {}
        for module_id, data_ids in modules.items():
            if module_id in self._process_data:
                module_data = self._process_data[module_id]
                result[module_id] = {}
                for data_id in data_ids:
                    if data_id in module_data:
                        result[module_id][data_id] = module_data[data_id]
        
        return result
    
    async def get_version(self) -> str:
        """Mock get_version method."""
        self._method_calls.append(("get_version",))
        
        if self._should_fail_version:
            raise ApiException("Failed to get version")
        
        if self._timeout:
            raise asyncio.TimeoutError("Get version timeout")
        
        return self._version
    
    async def get_me(self) -> str:
        """Mock get_me method."""
        self._method_calls.append(("get_me",))
        
        if self._should_fail_me:
            raise ApiException("Failed to get me")
        
        if self._timeout:
            raise asyncio.TimeoutError("Get me timeout")
        
        return self._me
    
    def set_should_fail_login(self, should_fail: bool = True) -> None:
        """Configure whether login should fail."""
        self._should_fail_login = should_fail
    
    def set_should_fail_modules(self, should_fail: bool = True) -> None:
        """Configure whether get_modules should fail."""
        self._should_fail_modules = should_fail
    
    def set_should_fail_settings(self, should_fail: bool = True) -> None:
        """Configure whether get_settings should fail."""
        self._should_fail_settings = should_fail
    
    def set_should_fail_process_data(self, should_fail: bool = True) -> None:
        """Configure whether get_process_data should fail."""
        self._should_fail_process_data = should_fail
    
    def set_should_fail_version(self, should_fail: bool = True) -> None:
        """Configure whether get_version should fail."""
        self._should_fail_version = should_fail
    
    def set_should_fail_me(self, should_fail: bool = True) -> None:
        """Configure whether get_me should fail."""
        self._should_fail_me = should_fail
    
    def set_timeout(self, should_timeout: bool = True) -> None:
        """Configure whether operations should timeout."""
        self._timeout = should_timeout
    
    def set_modules(self, modules: list[Any]) -> None:
        """Set mock modules data."""
        self._modules = modules
    
    def set_settings_data(self, settings_data: Mapping[str, Mapping[Any, Any]]) -> None:
        """Set mock settings data."""
        self._settings_data = settings_data
    
    def set_process_data(self, process_data: Mapping[str, Mapping[Any, Any]]) -> None:
        """Set mock process data."""
        self._process_data = process_data
    
    def set_version(self, version: str) -> None:
        """Set mock version."""
        self._version = version
    
    def set_me(self, me: str) -> None:
        """Set mock me value."""
        self._me = me
    
    def get_method_calls(self) -> list[tuple]:
        """Get list of method calls made to the mock."""
        return self._method_calls.copy()
    
    def clear_method_calls(self) -> None:
        """Clear the method call history."""
        self._method_calls.clear()
    
    def get_last_error(self) -> Exception | None:
        """Get the last error that occurred."""
        return self._last_error
    
    def set_last_error(self, error: Exception) -> None:
        """Set the last error that occurred."""
        self._last_error = error


class MockProcessData:
    """Mock process data object for testing."""
    
    def __init__(self, module_id: str, data_id: str, value: str) -> None:
        """Initialize mock process data."""
        self.id = data_id
        self.value = value


class MockSettingData:
    """Mock setting data object for testing."""
    
    def __init__(self, setting_id: str, value: str) -> None:
        """Initialize mock setting data."""
        self.id = setting_id
        self.value = value


def create_mock_process_data(data: dict[str, dict[str, str]]) -> dict[str, dict[str, MockProcessData]]:
    """Create mock process data from a dictionary."""
    result = {}
    for module_id, module_data in data.items():
        result[module_id] = {}
        for data_id, value in module_data.items():
            result[module_id][data_id] = MockProcessData(module_id, data_id, value)
    return result


def create_mock_settings_data(data: dict[str, dict[str, str]]) -> dict[str, dict[str, MockSettingData]]:
    """Create mock settings data from a dictionary."""
    result = {}
    for module_id, module_data in data.items():
        result[module_id] = {}
        for setting_id, value in module_data.items():
            result[module_id][setting_id] = MockSettingData(setting_id, value)
    return result


def create_mock_modules(names: list[str]) -> list[Any]:
    """Create mock module objects from a list of names."""
    return [MagicMock(id=name) for name in names]


async def wait_for_condition(
    condition: callable[[], bool],
    timeout: float = 5.0,
    interval: float = 0.1,
) -> bool:
    """Wait for a condition to become true or timeout."""
    start_time = datetime.now()
    while not condition():
        if (datetime.now() - start_time).total_seconds() > timeout:
            return False
        await asyncio.sleep(interval)
    return True


def assert_method_called(mock_obj: Any, method_name: str, *args: Any, **kwargs: Any) -> None:
    """Assert that a method was called with specific arguments."""
    mock_obj.assert_called_once_with(method_name, *args, **kwargs)


def assert_method_not_called(mock_obj: Any, method_name: str) -> None:
    """Assert that a method was not called."""
    mock_obj.assert_not_called()
    if hasattr(mock_obj, method_name):
        getattr(mock_obj, method_name).assert_not_called()


def assert_cache_hit_ratio(cache: Any, expected_min: float = 0.5) -> None:
    """Assert cache hit ratio meets minimum threshold."""
    hit_ratio = cache.get_hit_ratio()
    assert hit_ratio >= expected_min, f"Cache hit ratio {hit_ratio:.2%} below expected {expected_min:.2%}"


def assert_performance_metrics(
    monitor: Any, 
    max_duration: float = None,
    min_cache_ratio: float = 0.5,
    max_memory_usage: float = None
) -> None:
    """Assert performance metrics meet requirements."""
    metrics = monitor.get_metrics()
    
    if max_duration and "duration" in metrics:
        avg_duration = sum(metrics["duration"]) / len(metrics["duration"])
        assert avg_duration <= max_duration, f"Average duration {avg_duration:.2f}s exceeds maximum {max_duration}s"
    
    if min_cache_ratio and "cache_hit_ratio" in metrics:
        avg_cache_ratio = sum(metrics["cache_hit_ratio"]) / len(metrics["cache_hit_ratio"])
        assert avg_cache_ratio >= min_cache_ratio, f"Average cache hit ratio {avg_cache_ratio:.2%} below minimum {min_cache_ratio:.2%}"
    
    if max_memory_usage and "memory_usage" in metrics:
        avg_memory = sum(metrics["memory_usage"]) / len(metrics["memory_usage"])
        assert avg_memory <= max_memory_usage, f"Average memory usage {avg_memory:.2f}MB exceeds maximum {max_memory_usage}MB"
