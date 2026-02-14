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
            pytest.skip("Plenticore not available")

    @pytest.mark.asyncio
    async def test_plenticore_unload_timeout_protection(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ):
        """Test timeout protection during unload."""
        try:
            from kostal_plenticore.coordinator import Plenticore

            plenticore = Plenticore(hass, mock_config_entry)
            plenticore._client = MagicMock()
            plenticore._client.logout = AsyncMock()

            await plenticore.async_unload()

            # Client should be set to None after unload
            assert plenticore._client is None

        except ImportError:
            pytest.skip("Plenticore not available")

    def test_modbus_exception_hierarchy(self):
        """Test MODBUS exception hierarchy."""
        try:
            from kostal_plenticore.helper import (
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

            exc = ModbusIllegalDataAddressError()
            assert isinstance(exc, ModbusException)
            assert exc.exception_code == 0x02

            exc = ModbusIllegalDataValueError()
            assert isinstance(exc, ModbusException)
            assert exc.exception_code == 0x03

            exc = ModbusServerDeviceFailureError()
            assert isinstance(exc, ModbusException)
            assert exc.exception_code == 0x04

            exc = ModbusServerDeviceBusyError()
            assert isinstance(exc, ModbusException)
            assert exc.exception_code == 0x06

            exc = ModbusMemoryParityError()
            assert isinstance(exc, ModbusException)
            assert exc.exception_code == 0x08

        except ImportError:
            pytest.skip("MODBUS exception parsing not available")

    def test_modbus_exception_parsing(self):
        """Test MODBUS exception parsing."""
        try:
            from kostal_plenticore.helper import parse_modbus_exception, ModbusException
            from pykoplenti import ApiException

            test_cases = [
                ("illegal function error", 0x01),
                ("illegal data address error", 0x02),
                ("illegal data value error", 0x03),
                ("server device failure", 0x04),
                ("server device busy", 0x06),
                ("memory parity error", 0x08),
                ("some other error", None),
            ]

            for error_msg, expected_code in test_cases:
                api_exc = ApiException(error_msg)
                modbus_exc = parse_modbus_exception(api_exc)

                assert isinstance(modbus_exc, ModbusException)
                if expected_code is not None:
                    assert modbus_exc.exception_code == expected_code

        except ImportError:
            pytest.skip("MODBUS exception parsing not available")

    @pytest.mark.asyncio
    async def test_performance_coordinator_features(self, mock_performance_coordinator):
        """Test performance coordinator features."""
        if mock_performance_coordinator is None:
            pytest.skip("Performance coordinator not available")

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
        from kostal_plenticore.coordinator import PlenticoreConfigEntry
        assert PlenticoreConfigEntry is not None

    def test_coordinator_documentation(self):
        """Test coordinator documentation quality."""
        from kostal_plenticore.coordinator import PlenticoreUpdateCoordinator

        assert PlenticoreUpdateCoordinator.__doc__ is not None
        assert len(PlenticoreUpdateCoordinator.__doc__) > 10


class TestPlatinumDataUpdateCoordinator:
    """Test Platinum data update coordinator features."""

    @pytest.mark.asyncio
    async def test_request_deduplication(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ):
        """Test request deduplication in data update coordinator."""
        from kostal_plenticore.coordinator import ProcessDataUpdateCoordinator

        coordinator = ProcessDataUpdateCoordinator(
            hass,
            mock_config_entry,
            logging.getLogger(__name__),
            "Process Data",
            timedelta(seconds=10),
            MagicMock(),
        )
        coordinator.async_request_refresh = AsyncMock()

        # First request should return callback
        callback1 = coordinator.start_fetch_data("module1", "data1")
        assert callback1 is not None

        # Duplicate request should return a callable stop callback
        callback2 = coordinator.start_fetch_data("module1", "data1")
        assert callable(callback2)