# Kostal Plenticore Battery Features Implementation Roadmap

## 📋 Overview
This document outlines the step-by-step implementation of battery features from the Kostal MODBUS-TCP SunSpec interface, organized by safety priority and complexity.

## 🎯 Implementation Strategy
- **Safety First**: Start with read-only sensors, progress to controlled operations
- **Progressive Access**: Higher-risk features require elevated privileges
- **Comprehensive Testing**: Each phase validated before proceeding
- **User Feedback**: Gather user experience between phases

---

## 📊 Phase 1: Information Sensors (No Risk) 
**Timeline**: 1-2 days | **Priority**: HIGH | **Risk**: NONE

### ✅ Task 1.1: Battery Information Sensors
**Files to modify**: `sensor.py`, `helper.py`

#### Implementation Details:
```python
# Add to SENSOR_PROCESS_DATA in sensor.py
PlenticoreSensorEntityDescription(
    module_id="devices:local",
    key="Battery:WorkCapacity", 
    name="Battery Work Capacity",
    native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
    device_class=SensorDeviceClass.ENERGY,
    state_class=SensorStateClass.TOTAL_INCREASING,
    formatter="format_energy"
),
PlenticoreSensorEntityDescription(
    module_id="devices:local",
    key="Battery:SerialNumber",
    name="Battery Serial Number", 
    icon="mdi:barcode",
    formatter="format_string"
),
PlenticoreSensorEntityDescription(
    module_id="devices:local",
    key="Battery:ManagementMode",
    name="Battery Management Mode",
    icon="mdi:battery-charging",
    formatter="format_battery_management_mode"
),
PlenticoreSensorEntityDescription(
    module_id="devices:local", 
    key="Battery:SensorType",
    name="Battery Sensor Type",
    icon="mdi:sensor",
    formatter="format_sensor_type"
)
```

#### Helper Functions (helper.py):
```python
@staticmethod
def format_battery_management_mode(state: str) -> str:
    """Return readable battery management mode."""
    modes = {
        0x00: "No external battery management",
        0x01: "External management via digital I/O", 
        0x02: "External management via MODBUS"
    }
    try:
        return modes.get(int(state), f"Unknown mode: {state}")
    except (TypeError, ValueError):
        return state

@staticmethod  
def format_sensor_type(state: str) -> str:
    """Return readable sensor type."""
    sensors = {
        0x00: "SDM 630 (B+G E-Tech)",
        0x01: "B-Control EM-300 LR", 
        0x02: "Reserved",
        0x03: "KOSTAL Smart Energy Meter",
        0xFF: "No sensor"
    }
    try:
        return sensors.get(int(state), f"Unknown sensor: {state}")
    except (TypeError, ValueError):
        return state
```

#### Acceptance Criteria:
- [ ] All battery information sensors appear in HA
- [ ] Management mode shows human-readable text
- [ ] Sensor type displays correctly
- [ ] Work capacity shows in kWh
- [ ] Serial number displays correctly
- [ ] No errors in logs

---

### ✅ Task 1.2: Battery Power Monitoring Sensors
**Files to modify**: `sensor.py`

#### Implementation Details:
```python
PlenticoreSensorEntityDescription(
    module_id="devices:local",
    key="Battery:MaxChargePowerLimit",
    name="Battery Max Charge Power Limit",
    native_unit_of_measurement=UnitOfPower.WATT,
    device_class=SensorDeviceClass.POWER,
    state_class=SensorStateClass.MEASUREMENT,
    icon="mdi:battery-charging-limit",
    formatter="format_round"
),
PlenticoreSensorEntityDescription(
    module_id="devices:local",
    key="Battery:MaxDischargePowerLimit", 
    name="Battery Max Discharge Power Limit",
    native_unit_of_measurement=UnitOfPower.WATT,
    device_class=SensorDeviceClass.POWER,
    state_class=SensorStateClass.MEASUREMENT,
    icon="mdi:battery-discharging-limit",
    formatter="format_round"
)
```

#### Acceptance Criteria:
- [ ] Power limit sensors display current battery limits
- [ ] Values update in real-time
- [ ] Units display correctly (W)
- [ ] Icons are appropriate

---

## ⚡ Phase 2: Power Limit Controls (Medium Risk)
**Timeline**: 2-3 days | **Priority**: MEDIUM | **Risk**: MEDIUM

### ⚠️ Task 2.1: Battery Safety Framework
**Files to modify**: `coordinator.py`, `number.py`

#### Implementation Details:
```python
# Add to coordinator.py
class BatterySafetyValidator:
    """Comprehensive safety validation for battery operations."""
    
    @staticmethod
    async def validate_power_limit(value: float, limit_type: str) -> tuple[bool, str]:
        """Validate power limit against battery specifications."""
        # Get battery's own limits first
        battery_max_charge = await BatterySafetyValidator._get_battery_max_charge()
        battery_max_discharge = await BatterySafetyValidator._get_battery_max_discharge()
        
        if limit_type == "charge" and value > battery_max_charge:
            return False, f"Cannot set charge limit ({value}W) higher than battery maximum ({battery_max_charge}W)"
        elif limit_type == "discharge" and value > battery_max_discharge:
            return False, f"Cannot set discharge limit ({value}W) higher than battery maximum ({battery_max_discharge}W)"
        
        return True, "Valid"
    
    @staticmethod
    async def _get_battery_max_charge() -> float:
        """Get maximum charge power from battery."""
        # Implementation to read register 0x434
        pass
    
    @staticmethod 
    async def _get_battery_max_discharge() -> float:
        """Get maximum discharge power from battery.""" 
        # Implementation to read register 0x436
        pass
```

#### Acceptance Criteria:
- [ ] Safety validator class implemented
- [ ] Can read battery's own power limits
- [ ] Validation returns appropriate messages
- [ ] Unit tests for validation logic

---

### ⚠️ Task 2.2: Power Limit Control Entities
**Files to modify**: `number.py`

#### Implementation Details:
```python
# Add to NUMBER_SETTINGS_DATA
PlenticoreNumberEntityDescription(
    key="battery_max_charge_power_limit",
    entity_category=EntityCategory.CONFIG,
    entity_registry_enabled_default=False,
    icon="mdi:battery-charging-limit",
    name="Battery Max Charge Power Limit",
    native_unit_of_measurement=UnitOfPower.WATT,
    native_max_value=50000,  # Will be overridden by device data
    native_min_value=0,
    native_step=100,
    module_id="devices:local",
    data_id="Battery:MaxChargePowerLimitSetpoint",
    fmt_from="format_round",
    fmt_to="format_round_back",
),

PlenticoreNumberEntityDescription(
    key="battery_max_discharge_power_limit", 
    entity_category=EntityCategory.CONFIG,
    entity_registry_enabled_default=False,
    icon="mdi:battery-discharging-limit",
    name="Battery Max Discharge Power Limit",
    native_unit_of_measurement=UnitOfPower.WATT,
    native_max_value=50000,  # Will be overridden by device data
    native_min_value=0,
    native_step=100,
    module_id="devices:local",
    data_id="Battery:MaxDischargePowerLimitSetpoint",
    fmt_from="format_round", 
    fmt_to="format_round_back",
),
```

#### Custom Safety Classes:
```python
class PlenticoreBatteryPowerLimitNumber(PlenticoreDataNumber):
    """Number entity for battery power limits with safety validation."""
    
    async def async_set_native_value(self, value: float) -> None:
        """Set power limit with safety validation."""
        limit_type = "charge" if "charge" in self.data_id else "discharge"
        
        # Validate against battery specifications
        is_valid, message = await BatterySafetyValidator.validate_power_limit(value, limit_type)
        
        if not is_valid:
            _LOGGER.error("Battery power limit validation failed: %s", message)
            return
        
        _LOGGER.info("Setting battery %s power limit to %dW", limit_type, value)
        
        str_value = self._formatter_back(value)
        await self.coordinator.async_write_data(
            self.module_id, {self.data_id: str_value}
        )
        await self.coordinator.async_refresh()
```

#### Entity Creation Logic:
```python
# Update async_setup_entry to use custom class
if description.key in ["battery_max_charge_power_limit", "battery_max_discharge_power_limit"]:
    entities.append(
        PlenticoreBatteryPowerLimitNumber(
            settings_data_update_coordinator,
            entry.entry_id,
            entry.title,
            plenticore.device_info,
            description,
            setting_data,
        )
    )
```

#### Acceptance Criteria:
- [ ] Power limit controls appear in HA (disabled by default)
- [ ] Validation prevents setting limits above battery maximum
- [ ] Clear error messages for invalid attempts
- [ ] Controls successfully write valid values
- [ ] Real-time feedback of current limits

---

## 🚨 Phase 3: Advanced Battery Controls (High Risk)
**Timeline**: 3-5 days | **Priority**: LOW | **Risk**: HIGH

### 🛑 Task 3.1: Installer-Level Control Framework
**Files to modify**: `config_flow.py`, `coordinator.py`, `number.py`

#### Implementation Details:
```python
# Add to config_flow.py - installer privilege validation
class InstallerPrivilegeValidator:
    """Validate installer privileges for dangerous operations."""
    
    @staticmethod
    def has_installer_privileges(config_entry: ConfigEntry) -> bool:
        """Check if user has provided installer service code."""
        service_code = config_entry.data.get(CONF_SERVICE_CODE)
        return service_code is not None and len(service_code) > 0

# Add to coordinator.py
class BatteryControlValidator:
    """Advanced validation for battery control operations."""
    
    @staticmethod
    async def validate_charge_setpoint(value: float, mode: str) -> tuple[bool, str]:
        """Validate charge setpoint is safe."""
        # Check battery temperature
        # Check battery health  
        # Check grid conditions
        # Check system state
        return True, "Valid"
```

#### Acceptance Criteria:
- [ ] Installer privilege validation implemented
- [ ] Advanced control validator created
- [ ] Service code requirement enforced
- [ ] Safety checks for temperature, health, grid

---

### 🛑 Task 3.2: Battery Charge Control Setpoints
**Files to modify**: `number.py`

#### Implementation Details:
```python
# Advanced control entities (installer only)
PlenticoreNumberEntityDescription(
    key="battery_charge_power_ac_absolute",
    entity_category=EntityCategory.CONFIG,
    entity_registry_enabled_default=False,  # Hidden by default
    icon="mdi:battery-charging-100",
    name="Battery Charge Power (AC) - Absolute",
    native_unit_of_measurement=UnitOfPower.WATT,
    native_max_value=50000,
    native_min_value=-50000,  # Negative for charge
    native_step=100,
    module_id="devices:local",
    data_id="Battery:ChargePowerACAbsolute",
    fmt_from="format_round",
    fmt_to="format_round_back",
    installer_required=True,  # Custom field
),
```

#### Custom Installer-Only Class:
```python
class PlenticoreBatteryControlNumber(PlenticoreDataNumber):
    """Battery control entity requiring installer privileges."""
    
    entity_description: PlenticoreNumberEntityDescription
    
    async def async_set_native_value(self, value: float) -> None:
        """Set battery control with installer validation."""
        # Check installer privileges
        if not InstallerPrivilegeValidator.has_installer_privileges(self.coordinator.config_entry):
            _LOGGER.error("Installer privileges required for battery control operations")
            return
        
        # Advanced safety validation
        is_valid, message = await BatteryControlValidator.validate_charge_setpoint(
            value, self.data_id
        )
        
        if not is_valid:
            _LOGGER.error("Battery control validation failed: %s", message)
            return
        
        _LOGGER.warning("Installer setting battery control %s to %dW", self.data_id, value)
        
        # Proceed with operation
        str_value = self._formatter_back(value)
        await self.coordinator.async_write_data(
            self.module_id, {self.data_id: str_value}
        )
        await self.coordinator.async_refresh()
```

#### Acceptance Criteria:
- [ ] Installer controls hidden by default
- [ ] Service code requirement enforced
- [ ] Advanced safety validation active
- [ ] Clear logging of installer operations
- [ ] Emergency stop functionality available

---

## 📊 Phase 4: Battery Limitation Features (G3 Only)
**Timeline**: 2-3 days | **Priority**: LOW | **Risk**: MEDIUM

### 🔧 Task 4.1: Battery Limitation Controls
**Files to modify**: `number.py`, `coordinator.py`

#### Implementation Details:
```python
# Battery limitation for PLENTICORE G3 (SW 03.05.xxxxx+)
PlenticoreNumberEntityDescription(
    key="battery_max_charge_power",
    entity_category=EntityCategory.CONFIG,
    entity_registry_enabled_default=False,
    icon="mdi:battery-charging-limit",
    name="Battery Max Charge Power (G3)",
    native_unit_of_measurement=UnitOfPower.WATT,
    native_max_value=50000,
    native_min_value=0,
    native_step=100,
    module_id="devices:local",
    data_id="Battery:MaxChargePower",
    fmt_from="format_round",
    fmt_to="format_round_back",
    firmware_required="03.05.xxxxx",  # Custom validation
),
```

#### Firmware Validation:
```python
class FirmwareValidator:
    """Validate inverter firmware for feature compatibility."""
    
    @staticmethod
    def supports_battery_limitation(version: str) -> bool:
        """Check if firmware supports battery limitation."""
        # Parse version string and check against requirements
        return version.startswith("03.05") or version.startswith("03.06")
```

#### Acceptance Criteria:
- [ ] Firmware validation implemented
- [ ] G3 limitation controls available
- [ ] Fallback handling for older firmware
- [ ] Time-based limitation cycling

---

## 🧪 Testing & Validation Framework

### Unit Tests
- [ ] Safety validator tests
- [ ] Formatter function tests  
- [ ] Exception handling tests
- [ ] Privilege validation tests

### Integration Tests
- [ ] End-to-end sensor tests
- [ ] Control operation tests
- [ ] Error scenario tests
- [ ] Performance impact tests

### Safety Tests
- [ ] Invalid value rejection
- [ ] Privilege escalation tests
- [ ] Concurrent operation tests
- [ ] Emergency stop tests

---

## 📝 Documentation Updates

### User Documentation
- [ ] Update README.md with new features
- [ ] Add safety warnings
- [ ] Document privilege levels
- [ ] Provide troubleshooting guide

### Developer Documentation  
- [ ] Update AIdocumentation.md
- [ ] Document safety framework
- [ ] Add implementation examples
- [ ] Document testing procedures

---

## 🚀 Deployment Checklist

### Pre-deployment
- [ ] All phases completed and tested
- [ ] Documentation updated
- [ ] Backup procedures documented
- [ ] Rollback plan prepared

### Post-deployment
- [ ] Monitor error logs
- [ ] Gather user feedback
- [ ] Performance monitoring
- [ ] Security audit

---

## 📞 Support & Troubleshooting

### Common Issues
- [ ] MODBUS communication errors
- [ ] Privilege escalation problems  
- [ ] Safety validation failures
- [ ] Firmware compatibility issues

### Debug Information
- [ ] Enhanced logging levels
- [ ] Diagnostic endpoints
- [ ] Error code documentation
- [ ] Support contact information

---

## 🔄 Future Enhancements

### Advanced Features
- [ ] Predictive battery management
- [ ] Grid integration controls
- [ ] Machine learning optimization
- [ ] Mobile app integration

### Safety Improvements
- [ ] Real-time monitoring dashboard
- [ ] Automated safety responses
- [ ] Remote emergency shutdown
- [ ] Compliance reporting

---

## 📊 Progress Tracking

| Phase | Task | Status | Completed | Notes |
|-------|------|--------|-----------|-------|
| 1 | Battery Information Sensors | 🔄 | | |
| 1 | Battery Power Monitoring | ⏳ | | |
| 2 | Safety Framework | ⏳ | | |
| 2 | Power Limit Controls | ⏳ | | |
| 3 | Installer Framework | ⏳ | | |
| 3 | Advanced Controls | ⏳ | | |
| 4 | Battery Limitation | ⏳ | | |

**Legend**: ✅ Complete | 🔄 In Progress | ⏳ Not Started | ❌ Blocked

---

## 🎯 Success Metrics

### Technical Metrics
- [ ] Zero safety incidents
- [ ] < 1% error rate
- [ ] < 500ms response time
- [ ] 100% MODBUS exception handling

### User Metrics  
- [ ] Positive user feedback
- [ ] High adoption rate
- [ ] Low support tickets
- [ ] Good documentation reviews

---

*This roadmap is designed to ensure safe, incremental implementation of battery features while maintaining system reliability and user safety.*
