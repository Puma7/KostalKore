
import sys
import types
from unittest.mock import MagicMock

# Helper to create valid module mocks
def mock_module(name):
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod

# 1. Define Base Classes
class MockCoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.hass = MagicMock()
        self.platform = MagicMock()
        self._attr_unique_id = "test_unique_id"
        self._attr_name = "test_name"
        self._attr_device_info = {}

    @classmethod
    def __class_getitem__(cls, item):
        return cls

    @property
    def available(self):
        return True

    async def async_added_to_hass(self):
        pass

    async def async_will_remove_from_hass(self):
        pass

class MockSensorEntity:
    @property
    def available(self):
        return True
    
    @property
    def native_value(self):
        return None
    
    @property
    def state_class(self):
        return None
    
    @property
    def device_class(self):
        return None

# 2. Mock Home Assistant Modules Structure
mock_module("homeassistant")
mock_module("homeassistant.components")
ha_sensor = mock_module("homeassistant.components.sensor")
ha_sensor.SensorEntity = MockSensorEntity
ha_sensor.SensorDeviceClass = MagicMock()
ha_sensor.SensorStateClass = MagicMock()

mock_module("homeassistant.const")
mock_module("homeassistant.core")
mock_module("homeassistant.helpers")
mock_module("homeassistant.helpers.device_registry")
mock_module("homeassistant.helpers.entity_platform")
mock_module("homeassistant.helpers.typing")
ha_update_coordinator = mock_module("homeassistant.helpers.update_coordinator")
ha_update_coordinator.CoordinatorEntity = MockCoordinatorEntity
ha_update_coordinator.DataUpdateCoordinator = MagicMock() # Needed for inheritance check sometimes

# 3. Mock External Libs
mock_module("pykoplenti")
mock_module("aiohttp")
mock_module("aiohttp.client_exceptions")

# 4. Mock Internal Kostal Modules (to avoid importing them recursively)
mock_module("kostal_plenticore")
mock_module("kostal_plenticore.const")
kp_coordinator = mock_module("kostal_plenticore.coordinator")
kp_coordinator.ProcessDataUpdateCoordinator = MagicMock()
kp_coordinator.PlenticoreConfigEntry = MagicMock()
kp_coordinator.Plenticore = MagicMock()
kp_coordinator._parse_modbus_exception = MagicMock()

kp_helper = mock_module("kostal_plenticore.helper")

# Stub PlenticoreDataFormatter
class MockFormatter:
    @staticmethod
    def get_method(name):
        if name == "format_round":
            return lambda x: f"{float(x):.1f}" if x is not None else None
        elif name == "format_energy":
            return lambda x: f"{float(x):.1f}" if x is not None else None
        return lambda x: str(x) if x is not None else None

kp_helper.PlenticoreDataFormatter = MockFormatter

# 5. Add Project Path
import os
sys.path.append(os.path.abspath("c:/SynologyDrive/Eigene Dateien/Dokumente/###Onedrive/Software/Windsurf/Kostal"))

# 6. Import Sensor Module
try:
    print("Importing kostal_plenticore.sensor...")
    from kostal_plenticore.sensor import PlenticoreCalculatedSensor
    print("Import successful.")
except ImportError as e:
    print(f"Import failed: {e}")
    # Print traceback to see where exactly
    import traceback
    traceback.print_exc()
    sys.exit(1)
except AttributeError as e:
    print(f"Attribute Error during import: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# 7. Test Logic
def test_calculations():
    print("\n--- Running Tests ---\n")
    
    coordinator = MagicMock()
    coordinator.data = {
        "scb:statistic:EnergyFlow": {
            "Statistic:EnergyHomeGrid:Day": "100.0",
            "Statistic:EnergyChargeGrid:Day": "50.0",
            
            "Statistic:EnergyHomeBat:Day": "30.0",
            "Statistic:EnergyDischarge:Day": "40.0", 
            
            "Statistic:EnergyChargePv:Day": "200.0", 
        }
    }
    
    def create_sensor(key):
        desc = MagicMock()
        desc.key = key
        desc.module_id = "_calc_"
        desc.name = f"Test {key}"
        if "Efficiency" in key:
            desc.formatter = "format_round"
        else:
            desc.formatter = "format_energy"
        
        return PlenticoreCalculatedSensor(
            coordinator, desc, "id", "name", MagicMock()
        )

    # T1
    s1 = create_sensor("TotalGridConsumption:Day")
    val1 = s1.native_value
    print(f"TotalGridConsumption:Day = {val1}")
    assert val1 == "150.0", f"Expected 150.0, got {val1}"

    # T2
    s2 = create_sensor("BatteryDischargeTotal:Day")
    val2 = s2.native_value
    print(f"BatteryDischargeTotal:Day = {val2}")
    # Discharge (40) - Home (30) = 10 to grid. Total = 30 + 10 = 40.
    assert val2 == "40.0", f"Expected 40.0, got {val2}"
    
    # T3
    s3 = create_sensor("BatteryEfficiency:Day")
    val3 = s3.native_value
    print(f"BatteryEfficiency:Day = {val3}")
    # In: 200+50=250. Out: 40. Eff: 16%.
    assert val3 == "16.0", f"Expected 16.0, got {val3}"
    
    print("\nALL TESTS PASSED")

if __name__ == "__main__":
    test_calculations()
