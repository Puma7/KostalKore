
import sys
from unittest.mock import MagicMock

# Define proper base classes for mocking
class MockCoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.hass = MagicMock()
        self.platform = MagicMock()
    
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

# Mock Home Assistant modules
sys.modules["homeassistant"] = MagicMock()
sys.modules["homeassistant.components"] = MagicMock()
sys.modules["homeassistant.components.sensor"] = MagicMock()
sys.modules["homeassistant.components.sensor"].SensorEntity = MockSensorEntity
sys.modules["homeassistant.components.sensor"].SensorDeviceClass = MagicMock()
sys.modules["homeassistant.components.sensor"].SensorStateClass = MagicMock()

sys.modules["homeassistant.const"] = MagicMock()
sys.modules["homeassistant.core"] = MagicMock()
sys.modules["homeassistant.helpers"] = MagicMock()
sys.modules["homeassistant.helpers.device_registry"] = MagicMock()
sys.modules["homeassistant.helpers.entity_platform"] = MagicMock()
sys.modules["homeassistant.helpers.typing"] = MagicMock()
sys.modules["homeassistant.helpers.update_coordinator"] = MagicMock()
sys.modules["homeassistant.helpers.update_coordinator"].CoordinatorEntity = MockCoordinatorEntity

# Mock pykoplenti
sys.modules["pykoplenti"] = MagicMock()
sys.modules["aiohttp"] = MagicMock()
sys.modules["aiohttp.client_exceptions"] = MagicMock()

# Mock internal Kostal modules
sys.modules["kostal_plenticore.const"] = MagicMock()
sys.modules["kostal_plenticore.coordinator"] = MagicMock()
sys.modules["kostal_plenticore.helper"] = MagicMock()

# --- Mocking PlenticoreDataFormatter ---
class MockFormatter:
    @staticmethod
    def get_method(name):
        # Determine behavior based on formatter name
        if name == "format_round":
            return lambda x: f"{float(x):.1f}" if x is not None else None
        elif name == "format_energy":
            return lambda x: f"{float(x):.1f}" if x is not None else None
        # Default fallback
        return lambda x: str(x) if x is not None else None

sys.modules["kostal_plenticore.helper"].PlenticoreDataFormatter = MockFormatter

# --- Add Project Path ---
import os
sys.path.append(os.path.abspath("c:/SynologyDrive/Eigene Dateien/Dokumente/###Onedrive/Software/Windsurf/Kostal"))

# Import the class under test
try:
    from kostal_plenticore.sensor import PlenticoreCalculatedSensor
    print("Import successful!")
except ImportError as e:
    print(f"Import failed: {e}")
    sys.exit(1)

def test_calculations():
    print("Testing PlenticoreCalculatedSensor logic...\n")
    
    # Setup Coordinator Data
    # 3 Periods: Day, Month, Year, Total to check if logic works generic
    # Using 'Day' for test
    coordinator = MagicMock()
    coordinator.data = {
        "scb:statistic:EnergyFlow": {
            "Statistic:EnergyHomeGrid:Day": "100.0",
            "Statistic:EnergyChargeGrid:Day": "50.0",
            
            "Statistic:EnergyHomeBat:Day": "30.0",
            "Statistic:EnergyDischarge:Day": "40.0", # Total Discharge
            
            "Statistic:EnergyChargePv:Day": "200.0", 
            
            # Additional for cross check
            "Statistic:EnergyHomeGrid:Month": "3000.0",
        }
    }
    
    # Helper to create instance
    def create_sensor(key):
        desc = MagicMock()
        desc.key = key
        desc.module_id = "_calc_"
        desc.name = f"Test {key}"
        # Set formatter string that corresponds to our MockFormatter logic
        if "Efficiency" in key:
            desc.formatter = "format_round"
        else:
            desc.formatter = "format_energy"
        
        return PlenticoreCalculatedSensor(
            coordinator, desc, "piko_entry", "scb", MagicMock()
        )

    # --- Test 1: TotalGridConsumption:Day ---
    # Formula: GridToHome + GridToBattery
    # Values: 100.0 + 50.0 = 150.0
    print("Test 1: TotalGridConsumption:Day")
    s1 = create_sensor("TotalGridConsumption:Day")
    val1 = s1.native_value
    print(f"  Result: {val1}")
    if val1 == "150.0":
         print("  -> PASS")
    else:
         print(f"  -> FAIL (Expected '150.0')")
         exit(1)

    # --- Test 2: BatteryDischargeTotal:Day ---
    # Formula: BatteryToHome + BatteryToGrid
    # BatteryToGrid = TotalDischarge - BatteryToHome = 40.0 - 30.0 = 10.0
    # Result = 30.0 + 10.0 = 40.0 (Matches TotalDischarge when efficiency is ignored for this sum?) 
    # Wait, the logic is: total_discharge = val_home + max(0, battery_to_grid)
    # total_discharge = 30 + (40-30) = 40.
    # This seems redundant if it just equals TotalDischarge?
    # No, it ensures we don't count negative grid discharge if data is wonky.
    print("\nTest 2: BatteryDischargeTotal:Day")
    s2 = create_sensor("BatteryDischargeTotal:Day")
    val2 = s2.native_value
    print(f"  Result: {val2}")
    if val2 == "40.0":
         print("  -> PASS")
    else:
         print(f"  -> FAIL (Expected '40.0')")
         exit(1)
         
    # --- Test 3: BatteryEfficiency:Day ---
    # Energy In = ChargePv + ChargeGrid = 200.0 + 50.0 = 250.0
    # Energy Out = BatteryDischargeTotal = 40.0
    # Efficiency = (40.0 / 250.0) * 100 = 16.0%
    print("\nTest 3: BatteryEfficiency:Day")
    s3 = create_sensor("BatteryEfficiency:Day")
    val3 = s3.native_value
    print(f"  Result: {val3}")
    if val3 == "16.0":
         print("  -> PASS")
    else:
         print(f"  -> FAIL (Expected '16.0')")
         exit(1)
         
    # --- Test 4: Missing Data (Graceful Failure) ---
    print("\nTest 4: Missing Data")
    coordinator.data = {} # Empty data
    s4 = create_sensor("TotalGridConsumption:Day")
    val4 = s4.native_value
    print(f"  Result: {val4}")
    if val4 is None:
        print("  -> PASS")
    else:
        print(f"  -> FAIL (Expected None)")
        exit(1)

if __name__ == "__main__":
    test_calculations()
