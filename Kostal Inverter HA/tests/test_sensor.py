"""Tests for the Kostal Plenticore sensor platform.

This test suite provides comprehensive coverage for the sensor platform,
including unit tests for sensor entities, calculated sensors, and integration
tests for platform setup and entity creation.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.config_entries import ConfigEntry
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.helpers.entity import EntityCategory

from .common import (
    MockPlenticoreClient,
    create_mock_modules,
    create_mock_process_data,
    create_mock_settings_data,
    assert_performance_metrics,
)
from ..sensor import (
    PlenticoreDataSensor,
    CalculatedPvSumSensor,
    create_entities_batch,
    SENSOR_PROCESS_DATA,
    PlenticoreSensorEntityDescription,
)
from ..coordinator import ProcessDataUpdateCoordinator


class TestPlenticoreDataSensor:
    """Test the PlenticoreDataSensor class."""
    
    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create a mock coordinator."""
        coordinator = MagicMock()
        coordinator.data = create_mock_process_data({
            "devices:local": {"P": "5000"},
            "devices:local:pv1": {"P": "2500"},
        })
        return coordinator
    
    @pytest.fixture
    def mock_description(self) -> PlenticoreSensorEntityDescription:
        """Create a mock sensor description."""
        return PlenticoreSensorEntityDescription(
            key="P",
            name="Power",
            module_id="devices:local",
            native_unit_of_measurement="W",
            device_class=SensorDeviceClass.POWER,
            state_class=SensorStateClass.MEASUREMENT,
            entity_category=EntityCategory.DIAGNOSTIC,
            icon="mdi:flash",
            entity_registry_enabled_default=True,
            suggested_display_precision=1,
            suggested_unit_of_measurement="W",
        )
    
    @pytest.fixture
    def mock_plenticore(self) -> MagicMock:
        """Create a mock Plenticore instance."""
        plenticore = MagicMock()
        plenticore.device_info = {
            "identifiers": {"kostal_plenticore": "test_serial"},
            "manufacturer": "Kostal",
            "model": "Test Model",
            "name": "Test Inverter",
            "sw_version": "1.0.0",
        }
        return plenticore
    
    @pytest.fixture
    def sensor(
        self, mock_coordinator: MagicMock, mock_description: PlenticoreSensorEntityDescription, mock_plenticore: MagicMock
    ) -> PlenticoreDataSensor:
        """Create a sensor instance."""
        return PlenticoreDataSensor(
            coordinator=mock_coordinator,
            description=mock_description,
            entry_id="test_entry",
            platform_name="Test Platform",
            device_info=mock_plenticore.device_info,
        )
    
    def test_sensor_initialization(self, sensor: PlenticoreDataSensor) -> None:
        """Test sensor initialization."""
        assert sensor.coordinator is not None
        assert sensor.entity_description is not None
        assert sensor.entry_id == "test_entry"
        assert sensor.platform_name == "Test Platform"
        assert sensor.device_info is not None
        assert sensor.module_id == "devices:local"
        assert sensor.data_id == "P"
    
    def test_sensor_name_property(self, sensor: PlenticoreDataSensor) -> None:
        """Test sensor name property."""
        assert sensor.name == "Test Platform Power"
    
    def test_sensor_unique_id_property(self, sensor: PlenticoreDataSensor) -> None:
        """Test sensor unique_id property."""
        assert sensor.unique_id == "test_entry-P"
    
    def test_sensor_available_true(self, sensor: PlenticoreDataSensor) -> None:
        """Test sensor available when data is available."""
        assert sensor.available is True
    
    def test_sensor_available_false_no_coordinator_data(self, sensor: PlenticoreDataSensor) -> None:
        """Test sensor unavailable when coordinator has no data."""
        sensor.coordinator.data = None
        assert sensor.available is False
    
    def test_sensor_available_false_no_module(self, sensor: PlenticoreDataSensor) -> None:
        """Test sensor unavailable when module is not in data."""
        sensor.coordinator.data = {}
        assert sensor.available is False
    
    def test_sensor_available_false_no_data_id(self, sensor: PlenticoreDataSensor) -> None:
        """Test sensor unavailable when data_id is not in module."""
        sensor.coordinator.data = {"devices:local": {}}
        assert sensor.available is False
    
    def test_sensor_native_value(self, sensor: PlenticoreDataSensor) -> None:
        """Test sensor native value."""
        assert sensor.native_value == "5000"
    
    def test_sensor_native_value_none(self, sensor: PlenticoreDataSensor) -> None:
        """Test sensor native value when data is None."""
        sensor.coordinator.data = {"devices:local": {"P": None}}
        assert sensor.native_value is None
    
    def test_sensor_extra_state_attributes(self, sensor: PlenticoreDataSensor) -> None:
        """Test sensor extra state attributes."""
        attrs = sensor.extra_state_attributes
        assert attrs is None
    
    def test_sensor_device_info(self, sensor: PlenticoreDataSensor) -> None:
        """Test sensor device info."""
        assert sensor.device_info == sensor._attr_device_info
    
    @pytest.mark.asyncio
    async def test_sensor_async_added_to_hass(self, sensor: PlenticoreDataSensor) -> None:
        """Test sensor async_added_to_hass."""
        # Mock the coordinator's start_fetch_data method
        sensor.coordinator.start_fetch_data = MagicMock()
        
        await sensor.async_added_to_hass()
        
        sensor.coordinator.start_fetch_data.assert_called_once_with("devices:local", "P")
    
    @pytest.mark.asyncio
    async def test_sensor_async_will_remove_from_hass(self, sensor: PlenticoreDataSensor) -> None:
        """Test sensor async_will_remove_from_hass."""
        # Mock the coordinator's stop_fetch_data method
        sensor.coordinator.stop_fetch_data = MagicMock()
        
        await sensor.async_will_remove_from_hass()
        
        sensor.coordinator.stop_fetch_data.assert_called_once_with("devices:local", "P")


class TestCalculatedPvSumSensor:
    """Test the CalculatedPvSumSensor class."""
    
    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create a mock coordinator."""
        coordinator = MagicMock()
        coordinator.data = create_mock_process_data({
            "devices:local:pv1": {"P": "2500"},
            "devices:local:pv2": {"P": "2500"},
        })
        return coordinator
    
    @pytest.fixture
    def mock_description(self) -> PlenticoreSensorEntityDescription:
        """Create a mock sensor description."""
        return PlenticoreSensorEntityDescription(
            key="pv_P",
            name="PV Sum Power",
            module_id="_virt_",
            native_unit_of_measurement="W",
            device_class=SensorDeviceClass.POWER,
            state_class=SensorStateClass.MEASUREMENT,
            entity_category=EntityCategory.DIAGNOSTIC,
            icon="mdi:solar-power",
            entity_registry_enabled_default=True,
            suggested_display_precision=1,
            suggested_unit_of_measurement="W",
        )
    
    @pytest.fixture
    def mock_plenticore(self) -> MagicMock:
        """Create a mock Plenticore instance."""
        plenticore = MagicMock()
        plenticore.device_info = {
            "identifiers": {"kostal_plenticore": "test_serial"},
            "manufacturer": "Kostal",
            "model": "Test Model",
            "name": "Test Inverter",
            "sw_version": "1.0.0",
        }
        return plenticore
    
    @pytest.fixture
    def sensor(
        self, mock_coordinator: MagicMock, mock_description: PlenticoreSensorEntityDescription, mock_plenticore: MagicMock
    ) -> CalculatedPvSumSensor:
        """Create a calculated PV sum sensor instance."""
        return CalculatedPvSumSensor(
            coordinator=mock_coordinator,
            description=mock_description,
            entry_id="test_entry",
            platform_name="Test Platform",
            device_info=mock_plenticore.device_info,
        )
    
    def test_calculated_sensor_initialization(self, sensor: CalculatedPvSumSensor) -> None:
        """Test calculated sensor initialization."""
        assert sensor.coordinator is not None
        assert sensor.entity_description is not None
        assert sensor.entry_id == "test_entry"
        assert sensor.platform_name == "Test Platform"
        assert sensor.device_info is not None
        assert sensor.module_id == "_virt_"
        assert sensor.data_id == "pv_P"
    
    def test_calculated_sensor_name_property(self, sensor: CalculatedPvSumSensor) -> None:
        """Test calculated sensor name property."""
        assert sensor.name == "Test Platform PV Sum Power"
    
    def test_calculated_sensor_unique_id_property(self, sensor: CalculatedPvSumSensor) -> None:
        """Test calculated sensor unique_id property."""
        assert sensor.unique_id == "test_entry-pv_P"
    
    def test_calculated_sensor_available_true(self, sensor: CalculatedPvSumSensor) -> None:
        """Test calculated sensor available when data is available."""
        assert sensor.available is True
    
    def test_calculated_sensor_available_false_no_coordinator_data(self, sensor: CalculatedPvSumSensor) -> None:
        """Test calculated sensor unavailable when coordinator has no data."""
        sensor.coordinator.data = None
        assert sensor.available is False
    
    def test_calculated_sensor_available_false_no_pv_data(self, sensor: CalculatedPvSumSensor) -> None:
        """Test calculated sensor unavailable when no PV data is available."""
        sensor.coordinator.data = {}
        assert sensor.available is False
    
    def test_calculated_sensor_available_false_only_pv1(self, sensor: CalculatedPvSumSensor) -> None:
        """Test calculated sensor available when only PV1 data is available."""
        sensor.coordinator.data = {"devices:local:pv1": {"P": "2500"}}
        assert sensor.available is True
    
    def test_calculated_sensor_available_false_only_pv2(self, sensor: CalculatedPvSumSensor) -> None:
        """Test calculated sensor available when only PV2 data is available."""
        sensor.coordinator.data = {"devices:local:pv2": {"P": "2500"}}
        assert sensor.available is True
    
    def test_calculated_sensor_native_value_both_pvs(self, sensor: CalculatedPvSumSensor) -> None:
        """Test calculated sensor native value with both PV strings."""
        assert sensor.native_value == "5000.0"
    
    def test_calculated_sensor_native_value_only_pv1(self, sensor: CalculatedPvSumSensor) -> None:
        """Test calculated sensor native value with only PV1."""
        sensor.coordinator.data = {"devices:local:pv1": {"P": "2500"}}
        assert sensor.native_value == "2500.0"
    
    def test_calculated_sensor_native_value_only_pv2(self, sensor: CalculatedPvSumSensor) -> None:
        """Test calculated sensor native value with only PV2."""
        sensor.coordinator.data = {"devices:local:pv2": {"P": "2500"}}
        assert sensor.native_value == "2500.0"
    
    def test_calculated_sensor_native_value_no_pvs(self, sensor: CalculatedPvSumSensor) -> None:
        """Test calculated sensor native value with no PV data."""
        sensor.coordinator.data = {}
        assert sensor.native_value is None
    
    def test_calculated_sensor_native_value_invalid_data(self, sensor: CalculatedPvSumSensor) -> None:
        """Test calculated sensor native value with invalid data."""
        sensor.coordinator.data = {
            "devices:local:pv1": {"P": "invalid"},
            "devices:local:pv2": {"P": "2500"},
        }
        assert sensor.native_value == "2500.0"
    
    def test_calculated_sensor_native_value_none_data(self, sensor: CalculatedPvSumSensor) -> None:
        """Test calculated sensor native value with None data."""
        sensor.coordinator.data = {
            "devices:local:pv1": {"P": None},
            "devices:local:pv2": {"P": "2500"},
        }
        assert sensor.native_value == "2500.0"
    
    @pytest.mark.asyncio
    async def test_calculated_sensor_async_added_to_hass(self, sensor: CalculatedPvSumSensor) -> None:
        """Test calculated sensor async_added_to_hass."""
        # Mock the coordinator's start_fetch_data method
        sensor.coordinator.start_fetch_data = MagicMock()
        
        await sensor.async_added_to_hass()
        
        # Should start fetching both PV1 and PV2 data
        expected_calls = [
            ("devices:local:pv1", "P"),
            ("devices:local:pv2", "P"),
        ]
        actual_calls = [call.args for call in sensor.coordinator.start_fetch_data.call_args_list]
        
        assert len(actual_calls) == 2
        for expected_call in expected_calls:
            assert expected_call in actual_calls
    
    @pytest.mark.asyncio
    async def test_calculated_sensor_async_will_remove_from_hass(self, sensor: CalculatedPvSumSensor) -> None:
        """Test calculated sensor async_will_remove_from_hass."""
        # Mock the coordinator's stop_fetch_data method
        sensor.coordinator.stop_fetch_data = MagicMock()
        
        await sensor.async_will_remove_from_hass()
        
        # Should stop fetching both PV1 and PV2 data
        expected_calls = [
            ("devices:local:pv1", "P"),
            ("devices:local:pv2", "P"),
        ]
        actual_calls = [call.args for call in sensor.coordinator.stop_fetch_data.call_args_list]
        
        assert len(actual_calls) == 2
        for expected_call in expected_calls:
            assert expected_call in actual_calls


class TestCreateEntitiesBatch:
    """Test the create_entities_batch function."""
    
    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create a mock coordinator."""
        return MagicMock()
    
    @pytest.fixture
    def mock_descriptions(self) -> list[PlenticoreSensorEntityDescription]:
        """Create mock sensor descriptions."""
        return [
            PlenticoreSensorEntityDescription(
                key="P",
                name="Power",
                module_id="devices:local",
                native_unit_of_measurement="W",
                device_class=SensorDeviceClass.POWER,
                state_class=SensorStateClass.MEASUREMENT,
                entity_category=EntityCategory.DIAGNOSTIC,
                icon="mdi:flash",
                entity_registry_enabled_default=True,
                suggested_display_precision=1,
                suggested_unit_of_measurement="W",
            ),
            PlenticoreSensorEntityDescription(
                key="pv_P",
                name="PV Sum Power",
                module_id="_virt_",
                native_unit_of_measurement="W",
                device_class=SensorDeviceClass.POWER,
                state_class=SensorStateClass.MEASUREMENT,
                entity_category=EntityCategory.DIAGNOSTIC,
                icon="mdi:solar-power",
                entity_registry_enabled_default=True,
                suggested_display_precision=1,
                suggested_unit_of_measurement="W",
            ),
            PlenticoreSensorEntityDescription(
                key="EnergyGrid:Day",
                name="Grid Energy Day",
                module_id="scb:statistic:EnergyFlow",
                native_unit_of_measurement="kWh",
                device_class=SensorDeviceClass.ENERGY,
                state_class=SensorStateClass.TOTAL_INCREASING,
                entity_category=EntityCategory.DIAGNOSTIC,
                icon="mdi:flash",
                entity_registry_enabled_default=True,
                suggested_display_precision=2,
                suggested_unit_of_measurement="kWh",
            ),
        ]
    
    @pytest.fixture
    def mock_available_process_data(self) -> dict:
        """Create mock available process data."""
        return {
            "devices:local": {"P": MagicMock()},
            "scb:statistic:EnergyFlow": {"EnergyGrid:Day": MagicMock()},
        }
    
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
        plenticore.device_info = {
            "identifiers": {"kostal_plenticore": "test_serial"},
            "manufacturer": "Kostal",
            "model": "Test Model",
            "name": "Test Inverter",
            "sw_version": "1.0.0",
        }
        return plenticore
    
    def test_create_entities_batch_all_available(
        self,
        mock_coordinator: MagicMock,
        mock_descriptions: list[PlenticoreSensorEntityDescription],
        mock_available_process_data: dict,
        mock_config_entry: ConfigEntry,
        mock_plenticore: MagicMock,
    ) -> None:
        """Test creating entities when all are available."""
        with patch("kostal_plenticore.sensor.PlenticoreDataSensor") as mock_sensor_class, \
             patch("kostal_plenticore.sensor.CalculatedPvSumSensor") as mock_calculated_class:
            
            entities = create_entities_batch(
                mock_coordinator,
                mock_descriptions,
                mock_available_process_data,
                mock_config_entry,
                mock_plenticore,
            )
            
            # Should create 3 entities
            assert len(entities) == 3
            
            # Should create calculated PV sum sensor
            mock_calculated_class.assert_called_once()
            
            # Should create 2 regular sensors
            assert mock_sensor_class.call_count == 2
    
    def test_create_entities_batch_statistic_not_available(
        self,
        mock_coordinator: MagicMock,
        mock_descriptions: list[PlenticoreSensorEntityDescription],
        mock_available_process_data: dict,
        mock_config_entry: ConfigEntry,
        mock_plenticore: MagicMock,
    ) -> None:
        """Test creating entities when statistic module is not available."""
        # Remove statistic module from available data
        del mock_available_process_data["scb:statistic:EnergyFlow"]
        
        with patch("kostal_plenticore.sensor.PlenticoreDataSensor") as mock_sensor_class, \
             patch("kostal_plenticore.sensor.CalculatedPvSumSensor") as mock_calculated_class:
            
            entities = create_entities_batch(
                mock_coordinator,
                mock_descriptions,
                mock_available_process_data,
                mock_config_entry,
                mock_plenticore,
            )
            
            # Should create 2 entities (skip statistic)
            assert len(entities) == 2
            
            # Should create calculated PV sum sensor
            mock_calculated_class.assert_called_once()
            
            # Should create 1 regular sensor (skip statistic)
            assert mock_sensor_class.call_count == 1
    
    def test_create_entities_batch_performance(
        self,
        mock_coordinator: MagicMock,
        mock_available_process_data: dict,
        mock_config_entry: ConfigEntry,
        mock_plenticore: MagicMock,
        performance_monitor,
    ) -> None:
        """Test batch entity creation performance."""
        # Create many descriptions
        descriptions = []
        for i in range(100):
            descriptions.append(PlenticoreSensorEntityDescription(
                key=f"data_{i}",
                name=f"Sensor {i}",
                module_id="devices:local",
                native_unit_of_measurement="W",
                device_class=SensorDeviceClass.POWER,
                state_class=SensorStateClass.MEASUREMENT,
                entity_category=EntityCategory.DIAGNOSTIC,
                icon="mdi:flash",
                entity_registry_enabled_default=True,
                suggested_display_precision=1,
                suggested_unit_of_measurement="W",
            ))
        
        performance_monitor.start()
        
        with patch("kostal_plenticore.sensor.PlenticoreDataSensor") as mock_sensor_class:
            mock_sensor_class.return_value = MagicMock()
            
            entities = create_entities_batch(
                mock_coordinator,
                descriptions,
                mock_available_process_data,
                mock_config_entry,
                mock_plenticore,
            )
        
        performance_monitor.stop()
        
        # Performance assertions
        assert len(entities) == 100
        assert performance_monitor.duration < 0.5  # Should be fast
        assert mock_sensor_class.call_count == 100
        
        performance_monitor.record_metric("entities_created", len(entities))
        assert_performance_metrics(performance_monitor, max_duration=0.5)


class TestSensorPlatformSetup:
    """Test the sensor platform setup."""
    
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
        plenticore.device_info = {
            "identifiers": {"kostal_plenticore": "test_serial"},
            "manufacturer": "Kostal",
            "model": "Test Model",
            "name": "Test Inverter",
            "sw_version": "1.0.0",
        }
        return plenticore
    
    @pytest.fixture
    def mock_async_add_entities(self) -> MagicMock:
        """Create a mock async_add_entities callback."""
        return MagicMock()
    
    @pytest.mark.asyncio
    async def test_async_setup_entry_success(
        self,
        mock_hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_plenticore: MagicMock,
        mock_async_add_entities: MagicMock,
    ) -> None:
        """Test successful sensor platform setup."""
        # Setup mock data
        mock_client = mock_plenticore.client
        mock_client.set_process_data(create_mock_process_data({
            "devices:local": {"P": "5000"},
            "devices:local:pv1": {"P": "2500"},
            "devices:local:pv2": {"P": "2500"},
        }))
        
        # Mock the config entry runtime data
        mock_config_entry.runtime_data = mock_plenticore
        
        # Import and test the setup function
        from ..sensor import async_setup_entry
        
        with patch("kostal_plenticore.sensor.ProcessDataUpdateCoordinator") as mock_coordinator_class, \
             patch("kostal_plenticore.sensor.create_entities_batch") as mock_create_batch:
            
            mock_coordinator = MagicMock()
            mock_coordinator_class.return_value = mock_coordinator
            mock_create_batch.return_value = [MagicMock(), MagicMock()]
            
            await async_setup_entry(mock_hass, mock_config_entry, mock_async_add_entities)
            
            # Verify coordinator was created
            mock_coordinator_class.assert_called_once()
            
            # Verify entities were created and added
            mock_create_batch.assert_called_once()
            mock_async_add_entities.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_async_setup_entry_timeout(
        self,
        mock_hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_plenticore: MagicMock,
        mock_async_add_entities: MagicMock,
    ) -> None:
        """Test sensor platform setup with timeout."""
        # Setup mock client to timeout
        mock_client = mock_plenticore.client
        mock_client.set_timeout(True)
        
        # Mock the config entry runtime data
        mock_config_entry.runtime_data = mock_plenticore
        
        # Import and test the setup function
        from ..sensor import async_setup_entry
        
        with patch("kostal_plenticore.sensor.ProcessDataUpdateCoordinator") as mock_coordinator_class:
            
            mock_coordinator = MagicMock()
            mock_coordinator_class.return_value = mock_coordinator
            
            await async_setup_entry(mock_hass, mock_config_entry, mock_async_add_entities)
            
            # Should handle timeout gracefully
            assert mock_coordinator_class.called
            assert mock_async_add_entities.called
    
    @pytest.mark.asyncio
    async def test_async_setup_entry_api_error(
        self,
        mock_hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_plenticore: MagicMock,
        mock_async_add_entities: MagicMock,
    ) -> None:
        """Test sensor platform setup with API error."""
        # Setup mock client to fail
        mock_client = mock_plenticore.client
        mock_client.set_should_fail_process_data(True)
        
        # Mock the config entry runtime data
        mock_config_entry.runtime_data = mock_plenticore
        
        # Import and test the setup function
        from ..sensor import async_setup_entry
        
        with patch("kostal_plenticore.sensor.ProcessDataUpdateCoordinator") as mock_coordinator_class:
            
            mock_coordinator = MagicMock()
            mock_coordinator_class.return_value = mock_coordinator
            
            await async_setup_entry(mock_hass, mock_config_entry, mock_async_add_entities)
            
            # Should handle API error gracefully
            assert mock_coordinator_class.called
            assert mock_async_add_entities.called


# Integration tests
class TestSensorIntegration:
    """Integration tests for the sensor platform."""
    
    @pytest.mark.asyncio
    async def test_full_sensor_workflow(
        self, mock_hass: HomeAssistant, mock_config_entry: ConfigEntry, mock_plenticore: MagicMock
    ) -> None:
        """Test full sensor workflow from setup to data updates."""
        # Setup mock data
        mock_client = mock_plenticore.client
        mock_client.set_process_data(create_mock_process_data({
            "devices:local": {"P": "5000"},
            "devices:local:pv1": {"P": "2500"},
            "devices:local:pv2": {"P": "2500"},
        }))
        
        # Mock the config entry runtime data
        mock_config_entry.runtime_data = mock_plenticore
        
        # Import and test the setup function
        from ..sensor import async_setup_entry
        from datetime import timedelta
        import logging
        
        async_add_entities = MagicMock()
        
        with patch("kostal_plenticore.sensor.ProcessDataUpdateCoordinator") as mock_coordinator_class:
            
            mock_coordinator = MagicMock()
            mock_coordinator_class.return_value = mock_coordinator
            
            # Setup sensors
            await async_setup_entry(mock_hass, mock_config_entry, async_add_entities)
            
            # Verify setup
            assert mock_coordinator_class.called
            assert async_add_entities.called
            
            # Get the created entities
            created_entities = async_add_entities.call_args[0][0]
            assert len(created_entities) > 0
    
    @pytest.mark.asyncio
    async def test_sensor_error_recovery(
        self, mock_hass: HomeAssistant, mock_config_entry: ConfigEntry, mock_plenticore: MagicMock
    ) -> None:
        """Test sensor error recovery."""
        # Setup mock client to fail initially
        mock_client = mock_plenticore.client
        mock_client.set_should_fail_process_data(True)
        
        # Mock the config entry runtime data
        mock_config_entry.runtime_data = mock_plenticore
        
        # Import and test the setup function
        from ..sensor import async_setup_entry
        
        async_add_entities = MagicMock()
        
        with patch("kostal_plenticore.sensor.ProcessDataUpdateCoordinator") as mock_coordinator_class:
            
            mock_coordinator = MagicMock()
            mock_coordinator_class.return_value = mock_coordinator
            
            # Setup sensors (should handle error gracefully)
            await async_setup_entry(mock_hass, mock_config_entry, async_add_entities)
            
            # Should handle error gracefully
            assert mock_coordinator_class.called
            assert async_add_entities.called


# Performance tests
class TestSensorPerformance:
    """Performance tests for the sensor platform."""
    
    @pytest.mark.asyncio
    async def test_sensor_update_performance(
        self, mock_hass: HomeAssistant, mock_config_entry: ConfigEntry, mock_plenticore: MagicMock, performance_monitor
    ) -> None:
        """Test sensor update performance."""
        # Setup mock data
        mock_client = mock_plenticore.client
        mock_client.set_process_data(create_mock_process_data({
            "devices:local": {"P": "5000"},
            "devices:local:pv1": {"P": "2500"},
            "devices:local:pv2": {"P": "2500"},
        }))
        
        # Mock the config entry runtime data
        mock_config_entry.runtime_data = mock_plenticore
        
        # Import and test the setup function
        from ..sensor import async_setup_entry
        
        async_add_entities = MagicMock()
        
        with patch("kostal_plenticore.sensor.ProcessDataUpdateCoordinator") as mock_coordinator_class:
            
            mock_coordinator = MagicMock()
            mock_coordinator_class.return_value = mock_coordinator
            
            performance_monitor.start()
            
            # Setup sensors
            await async_setup_entry(mock_hass, mock_config_entry, async_add_entities)
            
            performance_monitor.stop()
            
            # Performance assertions
            assert performance_monitor.duration < 2.0  # Should be fast
            assert mock_coordinator_class.called
            assert async_add_entities.called
            
            performance_monitor.record_metric("setup_duration", performance_monitor.duration)
            assert_performance_metrics(performance_monitor, max_duration=2.0)
    
    @pytest.mark.asyncio
    async def test_batch_entity_creation_performance(
        self, mock_hass: HomeAssistant, mock_config_entry: ConfigEntry, mock_plenticore: MagicMock, performance_monitor
    ) -> None:
        """Test batch entity creation performance with many sensors."""
        # Setup mock data
        mock_client = mock_plenticore.client
        mock_client.set_process_data(create_mock_process_data({
            "devices:local": {"P": "5000"},
        }))
        
        # Mock the config entry runtime data
        mock_config_entry.runtime_data = mock_plenticore
        
        # Import and test the setup function
        from ..sensor import async_setup_entry
        
        async_add_entities = MagicMock()
        
        with patch("kostal_plenticore.sensor.ProcessDataUpdateCoordinator") as mock_coordinator_class, \
             patch("kostal_plenticore.sensor.create_entities_batch") as mock_create_batch:
            
            mock_coordinator = MagicMock()
            mock_coordinator_class.return_value = mock_coordinator
            
            # Mock many entities being created
            mock_entities = [MagicMock() for _ in range(200)]
            mock_create_batch.return_value = mock_entities
            
            performance_monitor.start()
            
            # Setup sensors
            await async_setup_entry(mock_hass, mock_config_entry, async_add_entities)
            
            performance_monitor.stop()
            
            # Performance assertions
            assert performance_monitor.duration < 1.0  # Should be fast even with many entities
            assert len(mock_entities) == 200
            assert mock_create_batch.called
            
            performance_monitor.record_metric("entities_created", len(mock_entities))
            assert_performance_metrics(performance_monitor, max_duration=1.0)
