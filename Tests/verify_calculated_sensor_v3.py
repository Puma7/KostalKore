
import sys
import types
from unittest.mock import MagicMock
import os

# --- 1. Mock External Dependencies (HA, pykoplenti) ---

def mock_module(name):
    parts = name.split('.')
    parent = None
    for i in range(1, len(parts) + 1):
        mod_name = '.'.join(parts[:i])
        if mod_name not in sys.modules:
            m = types.ModuleType(mod_name)
            sys.modules[mod_name] = m
        else:
            m = sys.modules[mod_name]
        
        if parent:
            setattr(parent, parts[i-1], m)
        parent = m
    return m

# Mock Home Assistant
ha = mock_module("homeassistant")
ha_core = mock_module("homeassistant.core")
ha_core.HomeAssistant = MagicMock()
ha_core.CALLBACK_TYPE = MagicMock()
ha_const = mock_module("homeassistant.const")
ha_const.Platform = MagicMock()
ha_const.PERCENTAGE = "%"
ha_const.UnitOfEnergy = MagicMock()
ha_const.UnitOfEnergy.KILO_WATT_HOUR = "kWh"
ha_const.UnitOfPower = MagicMock()
ha_const.UnitOfElectricCurrent = MagicMock()
ha_const.UnitOfElectricPotential = MagicMock()
ha_const.EntityCategory = MagicMock()
ha_const.CONF_HOST = "host"
ha_const.CONF_PASSWORD = "password"
ha_const.EVENT_HOMEASSISTANT_STOP = "homeassistant_stop"
ha_exc = mock_module("homeassistant.exceptions")
ha_exc.ConfigEntryNotReady = Exception

ha_components = mock_module("homeassistant.components")
ha_sensor = mock_module("homeassistant.components.sensor")

# Mock Base Classes
class MockCoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.hass = MagicMock()
        self.platform = MagicMock()
        self._attr_unique_id = "test_id"
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

class MockSensorEntity:
    @property
    def available(self):
        return True
    @property
    def native_value(self):
        return None

ha_sensor.SensorEntity = MockSensorEntity
ha_sensor.SensorDeviceClass = MagicMock()
ha_sensor.SensorStateClass = MagicMock()
ha_sensor.SensorEntityDescription = MagicMock()

# Mock helpers
ha_helpers = mock_module("homeassistant.helpers")
ha_dr = mock_module("homeassistant.helpers.device_registry")
ha_dr.DeviceInfo = dict
ha_ep = mock_module("homeassistant.helpers.entity_platform")
ha_ep.AddConfigEntryEntitiesCallback = MagicMock()
mock_module("homeassistant.helpers.typing")
sys.modules["homeassistant.helpers.typing"].StateType = str
ha_event = mock_module("homeassistant.helpers.event")
ha_event.async_call_later = MagicMock()
ha_event.async_call_later = MagicMock()
ha_config_entries = mock_module("homeassistant.config_entries")
ha_config_entries.ConfigEntry = MagicMock()
ha_aiohttp_client = mock_module("homeassistant.helpers.aiohttp_client")
ha_aiohttp_client.async_get_clientsession = MagicMock()

ha_uc = mock_module("homeassistant.helpers.update_coordinator")
ha_uc.CoordinatorEntity = MockCoordinatorEntity

class MockDataUpdateCoordinator:
    def __init__(self, hass, logger, **kwargs):
        self.data = None
    @classmethod
    def __class_getitem__(cls, item):
        return cls

ha_uc.DataUpdateCoordinator = MockDataUpdateCoordinator
ha_uc.UpdateFailed = Exception

# Mock pykoplenti & aiohttp
pk = mock_module("pykoplenti")
pk.ApiException = Exception
pk.AuthenticationException = Exception
pk.ApiClient = MagicMock()
pk.ExtendedApiClient = MagicMock()

mock_module("aiohttp")
aio_exc = mock_module("aiohttp.client_exceptions")
aio_exc.ClientError = Exception

# --- 2. Add Project Path ---
# This allows 'kostal_plenticore' to be imported from filesystem
sys.path.append(os.path.abspath("c:/SynologyDrive/Eigene Dateien/Dokumente/###Onedrive/Software/Windsurf/Kostal"))

# --- 3. Import Sensor Module ---
try:
    print("Importing kostal_plenticore.sensor...")
    from kostal_plenticore.sensor import PlenticoreCalculatedSensor
    print("Import successful.")
except ImportError as e:
    print(f"Import failed: {e}")
    # Inspect sys.path
    print("sys.path:", sys.path)
    import traceback
    traceback.print_exc()
    sys.exit(1)
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# --- 4. Test Logic ---
def test_calculations():
    print("\n--- Running Tests ---\n")
    
    coordinator = MagicMock()
    # Mock data structure matching sensor.py expectations
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
    print("Test 1: TotalGridConsumption:Day")
    s1 = create_sensor("TotalGridConsumption:Day")
    val1 = s1.native_value
    print(f"  Result: {val1}")
    assert val1 == "150.0", f"Expected 150.0, got {val1}"

    # T2
    print("\nTest 2: BatteryDischargeTotal:Day")
    s2 = create_sensor("BatteryDischargeTotal:Day")
    val2 = s2.native_value
    print(f"  Result: {val2}")
    assert val2 == "40.0", f"Expected 40.0, got {val2}"
    
    # T3
    print("\nTest 3: BatteryEfficiency:Day")
    s3 = create_sensor("BatteryEfficiency:Day")
    val3 = s3.native_value
    print(f"  Result: {val3}")
    assert val3 == "16.0", f"Expected 16.0, got {val3}"
    
    print("\nALL TESTS PASSED")

if __name__ == "__main__":
    test_calculations()
