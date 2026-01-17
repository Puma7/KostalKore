# Kostal Plenticore Solar Inverter Home Assistant Integration

A custom Home Assistant integration for monitoring and controlling Kostal Plenticore solar inverters through their local API interface.

## Overview

This integration provides comprehensive monitoring and control capabilities for Kostal Plenticore solar inverters, allowing you to:
- Monitor real-time power production and consumption
- Track energy generation statistics
- Control inverter settings and operating modes
- Access detailed inverter status and diagnostic information

## Features

### 📊 **Sensors**
- **Power Monitoring**: Real-time AC/DC power measurements, voltage, current
- **Energy Tracking**: Daily, monthly, and total energy production statistics
- **Inverter Status**: Operating state, temperature, and health indicators
- **Grid Information**: Grid frequency, power factor, and grid connection status

### 🧮 **Calculated Sensors (New)**
- **Total Grid Consumption**: Smart calculation of `Grid to Home + Grid to Battery`
- **Total Battery Discharge**: Comprehensive `Battery to Home + Battery to Grid` tracking
- **Battery Efficiency**: Real-time efficiency monitoring (`Output / Input`) in %
- **Dashboard Ready**: Fully compatible with Home Assistant Energy Dashboard

### 🎛️ **Controls**
- **Number Entities**: Adjustable power limits, charge/discharge rates
- **Select Entities**: Operating mode selection (e.g., battery management modes)
- **Switch Entities**: Enable/disable various inverter functions
- **G3 Battery Limitation**: Max charge/discharge power limits are re-applied cyclically to keep them active (per vendor requirement)
- **Entity Naming**: Entities now use device-based naming for a cleaner UI (see `kostal_plenticore/QUICK_REFERENCE.md` for full lists)

### 🔧 **Diagnostics**
- Comprehensive diagnostic data for troubleshooting
- Redacted sensitive information for privacy
- Integration version and API status information

## Supported Devices

- Kostal Plenticore Solar Inverters
- Compatible with firmware versions supporting local API access
- Requires network connectivity to the inverter

## Prerequisites

### Hardware Requirements
- Kostal Plenticore inverter with network connectivity
- Local network access to the inverter's web interface

### Software Requirements
- Home Assistant 2023.1 or newer
- Python package: `pykoplenti==1.3.0` (automatically installed)

### Network Requirements
- Inverter must be accessible on your local network
- TCP port 80 (HTTP) must be open to the inverter
- No firewall blocking between Home Assistant and inverter

## Installation

### Method 1: HACS (Recommended)
1. Open HACS in Home Assistant
2. Navigate to Integrations
3. Click "Explore & Download Repositories"
4. Search for "Kostal Plenticore"
5. Click "Download" and restart Home Assistant

### Method 2: Manual Installation
1. Copy the `kostal_plenticore` folder to your `config/custom_components` directory
2. Restart Home Assistant
3. The integration will be available for configuration

## Configuration

### Initial Setup
1. In Home Assistant, go to **Settings > Devices & Services**
2. Click **+ Add Integration**
3. Search for "Kostal Plenticore Solar Inverter"
4. Enter the required information:
   - **Host**: IP address or hostname of your inverter
   - **Password**: Inverter web interface password
   - **Service Code**: (Optional) Service code for advanced features

### Configuration Parameters
- **Host**: The network address of your Kostal inverter (e.g., `192.168.1.100`)
- **Password**: Password for accessing the inverter's web interface
- **Service Code**: Optional service code for accessing advanced settings (typically used by installers)

## Integration Architecture

### File Structure
```
kostal_plenticore/
├── __init__.py          # Integration entry point and setup
├── manifest.json        # Integration metadata and dependencies
├── config_flow.py       # Configuration flow and user interface
├── const.py            # Constants and configuration keys
├── coordinator.py       # API client and data coordination
├── sensor.py           # Power and energy sensors
├── number.py           # Numeric controls and settings
├── select.py           # Dropdown selections for modes
├── switch.py           # Toggle controls for functions
├── helper.py           # Utility functions and data formatters
├── diagnostics.py      # Diagnostic data collection
└── strings.json        # Localization strings
```

### Key Components

#### **coordinator.py**
- Manages API communication with the inverter
- Handles data updates and caching
- Provides device information and connection management
- Implements multiple coordinator types for different data sources

#### **sensor.py**
- Defines all sensor entities for monitoring
- Includes power, energy, voltage, current, and status sensors
- Implements proper device classes and units
- Handles data formatting and state management

#### **config_flow.py**
- Provides user-friendly setup interface
- Validates connection credentials
- Handles reconfiguration of existing entries
- Implements error handling and user feedback

## Available Entities

### Power & Energy Sensors
- `Inverter AC Power`: Current AC power output (W)
- `Inverter DC Power`: Current DC power input (W)
- `Total Energy Produced`: Lifetime energy production (kWh)
- `Daily Energy Produced`: Today's energy production (kWh)
- `Grid Frequency`: Current grid frequency (Hz)

### Status Sensors
- `Inverter State`: Current operating state
- `Inverter Temperature`: Internal temperature (°C)
- `Battery Level`: Battery charge level (%) [if applicable]

### Control Entities
- `Power Limit`: Maximum power output setting
- `Operating Mode`: Inverter operating mode selection
- `Battery Management`: Battery charging/discharging controls

## Inverter States

The integration provides human-readable inverter states:

| State Code | Description |
|------------|-------------|
| 0 | Off |
| 1 | Initializing |
| 2 | Insulation Measurement |
| 3 | Grid Check |
| 4 | Startup |
| 6 | Feeding In |
| 7 | Throttled |
| 8 | External Switch Off |
| 9 | Update |
| 10 | Standby |
| 11 | Grid Synchronization |
| 12 | Grid Pre-Check |
| 13 | Grid Switch Off |
| 14 | Overheating |

## Troubleshooting

### Connection Issues
1. **Cannot Connect**: Verify the inverter's IP address and network connectivity
2. **Invalid Auth**: Check the password for the inverter's web interface
3. **Timeout Error**: Ensure the inverter is responsive and not in maintenance mode

### Data Not Updating
1. Check the inverter's API status in the web interface
2. Verify network stability between Home Assistant and inverter
3. Review Home Assistant logs for API errors

### Performance Issues
1. Reduce polling frequency if experiencing slow response
2. Check network bandwidth and latency to the inverter
3. Monitor Home Assistant system resources

## Example Automations

### Limit Battery Charge Power (G3)
```yaml
service: number.set_value
target:
  entity_id: number.scb_battery_max_charge_power_g3
data:
  value: 10000
```

### Set Battery Charging / Usage Mode
```yaml
service: select.select_option
target:
  entity_id: select.scb_battery_charging_usage_mode
data:
  option: Battery:SmartBatteryControl:Enable
```

## Energy Dashboard Tips
- Use **Total Increasings** (e.g., battery charge/discharge totals) for Energy Dashboard entities.
- If a sensor stays `unavailable`, check if the inverter exposes the matching REST data ID.

## Debugging

Enable debug logging to troubleshoot issues:

```yaml
logger:
  default: info
  logs:
    pykoplenti: debug
    custom_components.kostal_plenticore: debug
```

## Security Considerations

- The integration stores the inverter password in Home Assistant's configuration
- Use a strong, unique password for your inverter
- Ensure your local network is secure
- Consider network segmentation for sensitive devices

## API Documentation

This integration uses the Kostal Plenticore local API. For detailed API information:

### Technical Specifications
- **Protocol**: MODBUS-TCP with SunSpec Standard Compliance
- **Default Port**: TCP 1502 (MODBUS) and TCP 80 (Web API)
- **Default Unit-ID**: 71 (modifiable)
- **Data Models**: SunSpec Modbus models for solar inverters
- **Control Functions**: Advanced inverter control via MODBUS registers

### Supported Inverter Models
- **PIKO/PLENTICORE G1**: UI 01.30+
- **PLENTICORE G2**: SW 02.15.xxxxx+
- **PLENTICORE G3**: SW 3.06.00.xxxxx+
- **PLENTICORE MP G3**: SW 3.06.00.xxxxx+

### SunSpec Models Implemented
- **Model 1**: Common Model (Address 40003)
- **Model 103**: Three Phase Inverter (Address 40071)
- **Model 113**: Three Phase Inverter, float (Address 40123)
- **Model 120**: Nameplate (Address 40185)
- **Model 123**: Immediate Controls (Address 40213)
- **Model 160**: Multiple MPPT (Address 40239)
- **Model 2031**: Wye-Connect Three Phase (abcn) Meter (Address 40309)
- **Model 802**: Battery Base Model (Address 40416)
- **Model 65535**: End Model (Address 40480)

### Key MODBUS Registers
#### **Device Information**
- **Address 2**: MODBUS Enable (R/W)
- **Address 4**: MODBUS Unit-ID (R/W)
- **Address 14**: Inverter serial number (RO)
- **Address 38**: Inverter state (RO)
- **Address 56**: Overall software version (RO)

#### **Power Measurements**
- **Address 100**: Total DC power (W)
- **Address 172**: Total AC active power (W)
- **Address 252**: Total active power (powermeter) (W)
- **Addresses 258-286**: DC1-DC3 current, power, voltage
- **Addresses 320-326**: Total, daily, yearly, monthly yield (Wh)

#### **Battery Data**
- **Address 514**: Battery actual SOC (%)
- **Address 210**: Act. state of charge (%)
- **Address 214**: Battery temperature (°C)
- **Address 216**: Battery voltage (V)
- **Address 200**: Battery gross capacity (Ah)

#### **Control Registers**
- **Address 533**: Active Power Setpoint (%) (R/W)
- **Address 583**: Reactive Power Setpoint (%) (R/W)
- **Address 585**: Delta-cos φ Setpoint (R/W)
- **Addresses 1024-1044**: Battery management controls (R/W)

### Data Formats
- **U16**: Unsigned 16-bit integer (1 register)
- **U32**: Unsigned 32-bit integer (2 registers)
- **S16**: Signed 16-bit integer (1 register)
- **S32**: Signed 32-bit integer (2 registers)
- **Float**: IEEE 754 floating point (2 registers)
- **String**: Character data (variable length)

### MODBUS Function Codes
- **0x03**: Read Holding Registers
- **0x06**: Write Single Register
- **0x10**: Write Multiple Registers

### Inverter States
The inverter supports the following operational states:
- **0**: Off
- **1**: Init
- **2**: IsoMEas (Insulation Measurement)
- **3**: GridCheck
- **4**: StartUp
- **6**: FeedIn
- **7**: Throttled
- **8**: ExtSwitchOff
- **9**: Update
- **10**: Standby
- **11**: GridSync
- **12**: GridPreCheck
- **13**: GridSwitchOff
- **14**: Overheating
- **15**: Shutdown
- **16**: ImproperDcVoltage
- **17**: ESB (Emergency Shutdown)
- **18**: Unknown

### Energy Manager States
Internal energy flow management states:
- **0x00**: Idle
- **0x02**: Emergency Battery Charge
- **0x08**: Winter Mode Step 1
- **0x10**: Winter Mode Step 2

### Battery Types Supported
The integration supports various battery manufacturers:
- **0x0002**: PIKO Battery Li
- **0x0004**: BYD
- **0x0008**: BMZ
- **0x0010**: AXIstorage Li SH
- **0x0040**: LG
- **0x0200**: Pyontech Force H
- **0x0400**: AXIstorage Li SV
- **0x1000**: Dyness Tower / TowerPro
- **0x2000**: VARTA.wall
- **0x4000**: ZYC

### Documentation Reference
- Refer to the `BA_KOSTAL_Interface_MODBUS-TCP_SunSpec_with_Control.pdf` for complete register mapping
- The API follows SunSpec standards for Modbus-TCP communication
- Additional control functions may require installer-level access and service code

### Integration Implementation
- This integration uses the `pykoplenti` library which abstracts MODBUS-TCP communication
- Direct MODBUS access available for advanced users
- All SunSpec standard data points are exposed through Home Assistant entities

## Contributing

Contributions are welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## Version History

- **Current**: Based on pykoplenti v1.3.0
- **Compatibility**: Home Assistant 2023.1+
- **API Support**: Kostal Plenticore local API

## Support

For issues and questions:
1. Check the troubleshooting section above
2. Review Home Assistant logs for error messages
3. Create an issue in the integration's repository
4. Provide diagnostic data when reporting issues

## License

This integration follows the same license as Home Assistant core components.

---

**Note**: This integration communicates directly with your Kostal inverter over the local network. Ensure your network configuration maintains security and reliability for optimal performance.
