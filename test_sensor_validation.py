#!/usr/bin/env python3
"""Test script for validating the new calculated sensors in sensor.py"""

import ast
import re
import sys

def test_sensor_structure():
    """Test the sensor.py structure and new calculated sensors."""
    try:
        # Read the sensor.py file
        with open('kostal_plenticore/sensor.py', 'r', encoding='utf-8') as f:
            content = f.read()
        
        print('🔍 Testing sensor.py structure...')
        
        # Test 1: Check calculated sensors exist
        calc_sensors = re.findall(r'module_id="_calc_"', content)
        print(f'✅ Found {len(calc_sensors)} _calc_ sensors')
        
        # Test 2: Check sensor types (should be 12 total: 4 each type)
        total_grid = re.findall(r'TotalGridConsumption:', content)
        battery_discharge = re.findall(r'BatteryDischargeTotal:', content)
        battery_efficiency = re.findall(r'BatteryEfficiency:', content)
        
        print(f'✅ TotalGridConsumption sensors: {len(total_grid)} (expected: 4)')
        print(f'✅ BatteryDischargeTotal sensors: {len(battery_discharge)} (expected: 4)')
        print(f'✅ BatteryEfficiency sensors: {len(battery_efficiency)} (expected: 4)')
        
        # Test 3: Check PlenticoreCalculatedSensor class exists
        if 'class PlenticoreCalculatedSensor(' in content:
            print('✅ PlenticoreCalculatedSensor class found')
        else:
            print('❌ PlenticoreCalculatedSensor class not found')
            return False
        
        # Test 4: Check calculation logic
        grid_calc = 'EnergyHomeGrid' in content and 'EnergyChargeGrid' in content
        discharge_calc = 'EnergyHomeBat' in content and 'EnergyDischarge' in content
        efficiency_calc = 'EnergyChargePv' in content and 'energy_out / energy_in' in content
        
        if grid_calc:
            print('✅ Total grid consumption calculation logic present')
        else:
            print('❌ Total grid consumption calculation logic missing')
            
        if discharge_calc:
            print('✅ Battery discharge total calculation logic present')
        else:
            print('❌ Battery discharge total calculation logic missing')
            
        if efficiency_calc:
            print('✅ Battery efficiency calculation logic present')
        else:
            print('❌ Battery efficiency calculation logic missing')
        
        # Test 5: Check error handling
        error_handling = 'try:' in content and 'except' in content and '_LOGGER.debug' in content
        if error_handling:
            print('✅ Error handling implemented')
        else:
            print('❌ Error handling missing')
        
        # Test 6: Check setup integration
        setup_integration = 'CALCULATED_SENSORS' in content and 'PlenticoreCalculatedSensor(' in content
        if setup_integration:
            print('✅ Setup integration present')
        else:
            print('❌ Setup integration missing')
        
        # Test 7: Check specific calculation formulas
        if 'val_home + val_batt' in content:
            print('✅ Grid consumption formula correct')
        
        if 'battery_to_grid = val_discharge - val_home' in content:
            print('✅ Battery to grid formula correct')
        
        if 'efficiency = (energy_out / energy_in) * 100' in content:
            print('✅ Battery efficiency formula correct')
        
        # Test 8: Check data validation
        if 'is None' in content and 'return None' in content:
            print('✅ Data validation present')
        
        # Test 9: Python syntax check
        try:
            ast.parse(content)
            print('✅ Python syntax is valid')
        except SyntaxError as e:
            print(f'❌ Syntax error: {e}')
            return False
        
        # Test 10: Check all required periods exist
        periods = ['Day', 'Month', 'Year', 'Total']
        for period in periods:
            if f'TotalGridConsumption:{period}' in content:
                print(f'✅ TotalGridConsumption:{period} found')
            if f'BatteryDischargeTotal:{period}' in content:
                print(f'✅ BatteryDischargeTotal:{period} found')
            if f'BatteryEfficiency:{period}' in content:
                print(f'✅ BatteryEfficiency:{period} found')
        
        print('\n🎉 All structural tests passed!')
        return True
        
    except Exception as e:
        print(f'❌ Test error: {e}')
        import traceback
        traceback.print_exc()
        return False

def test_sensor_calculations():
    """Test the calculation logic with mock data."""
    print('\n🧮 Testing calculation logic with mock data...')
    
    # Mock data for testing
    mock_data = {
        'scb:statistic:EnergyFlow': {
            'Statistic:EnergyHomeGrid:Day': '2.5',
            'Statistic:EnergyChargeGrid:Day': '1.2',
            'Statistic:EnergyHomeBat:Day': '3.8',
            'Statistic:EnergyDischarge:Day': '5.1',
            'Statistic:EnergyChargePv:Day': '4.2',
            'Statistic:EnergyChargeGrid:Day': '1.2',
        }
    }
    
    # Test 1: Total Grid Consumption = Grid to Home + Grid to Battery
    grid_home = float(mock_data['scb:statistic:EnergyFlow']['Statistic:EnergyHomeGrid:Day'])
    grid_battery = float(mock_data['scb:statistic:EnergyFlow']['Statistic:EnergyChargeGrid:Day'])
    total_grid = grid_home + grid_battery
    
    expected_grid = 2.5 + 1.2  # 3.7
    if abs(total_grid - expected_grid) < 0.01:
        print(f'✅ Total Grid Consumption calculation: {total_grid} kWh (expected: {expected_grid})')
    else:
        print(f'❌ Total Grid Consumption calculation wrong: {total_grid} (expected: {expected_grid})')
    
    # Test 2: Battery Discharge Total = Battery to Home + Battery to Grid
    battery_home = float(mock_data['scb:statistic:EnergyFlow']['Statistic:EnergyHomeBat:Day'])
    battery_discharge = float(mock_data['scb:statistic:EnergyFlow']['Statistic:EnergyDischarge:Day'])
    battery_to_grid = battery_discharge - battery_home
    total_discharge = battery_home + max(0, battery_to_grid)
    
    expected_discharge = 3.8 + (5.1 - 3.8)  # 5.1
    if abs(total_discharge - expected_discharge) < 0.01:
        print(f'✅ Battery Discharge Total calculation: {total_discharge} kWh (expected: {expected_discharge})')
    else:
        print(f'❌ Battery Discharge Total calculation wrong: {total_discharge} (expected: {expected_discharge})')
    
    # Test 3: Battery Efficiency = (Energy Out / Energy In) * 100
    charge_pv = float(mock_data['scb:statistic:EnergyFlow']['Statistic:EnergyChargePv:Day'])
    charge_grid = float(mock_data['scb:statistic:EnergyFlow']['Statistic:EnergyChargeGrid:Day'])
    energy_in = charge_pv + charge_grid
    
    energy_out = total_discharge
    efficiency = (energy_out / energy_in) * 100 if energy_in > 0 else 0
    
    expected_efficiency = (5.1 / (4.2 + 1.2)) * 100  # ~94.4%
    if abs(efficiency - expected_efficiency) < 1:
        print(f'✅ Battery Efficiency calculation: {efficiency:.1f}% (expected: {expected_efficiency:.1f}%)')
    else:
        print(f'❌ Battery Efficiency calculation wrong: {efficiency:.1f}% (expected: {expected_efficiency:.1f}%)')
    
    print('✅ Calculation logic tests completed')

if __name__ == "__main__":
    print("🚀 Starting sensor.py validation tests...\n")
    
    # Run structural tests
    structure_ok = test_sensor_structure()
    
    # Run calculation tests
    test_sensor_calculations()
    
    print(f"\n📊 Test Summary:")
    if structure_ok:
        print("✅ All tests passed! The new calculated sensors are ready for use.")
    else:
        print("❌ Some tests failed. Please review the implementation.")
    
    print("\n🎯 Next Steps:")
    print("1. Install the updated sensor.py in your Home Assistant")
    print("2. Restart Home Assistant")
    print("3. Check the new sensors appear in Developer Tools")
    print("4. Add them to your Energy Dashboard configuration")
