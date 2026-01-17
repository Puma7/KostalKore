# Kostal Plenticore Home Assistant Integration - Development Guide

## Table of Contents
1. [Architecture Overview](#architecture-overview)
2. [REST API vs MODBUS TCP](#rest-api-vs-modbus-tcp)
3. [Adding New Battery Features](#adding-new-battery-features)
4. [Safety Considerations](#safety-considerations)
5. [Common Issues and Solutions](#common-issues-and-solutions)
6. [Code Patterns and Best Practices](#code-patterns-and-best-practices)
7. [Testing and Debugging](#testing-and-debugging)

---

## Architecture Overview

### Integration Structure

```
kostal_plenticore/
├── __init__.py          # Main entry point, sets up coordinator
├── coordinator.py       # Data update coordinators, API client management
├── sensor.py            # Read-only sensor entities (process data)
├── switch.py            # Binary control entities (on/off settings)
├── number.py            # Numeric control entities (adjustable settings)
├── select.py            # Selection entities (dropdown choices)
├── config_flow.py       # Configuration UI and connection testing
├── diagnostics.py       # Diagnostic information collection
├── helper.py            # Data formatting utilities
├── const.py             # Constants (domain, config keys)
├── const_ids.py         # Centralized module/data IDs
├── manifest.json        # Integration metadata
└── strings.json         # User-facing strings
```

### Data Flow

1. **Initialization**: `__init__.py` creates `Plenticore` instance
2. **Connection**: `coordinator.py` manages API client and authentication
3. **Data Updates**: Coordinators fetch data periodically (process data, settings)
4. **Entity Creation**: Platform files (`sensor.py`, `switch.py`, etc.) create entities
5. **User Interaction**: Entities read/write data through coordinators

### Key Components

- **Plenticore**: Main class managing API client and device info
- **DataUpdateCoordinator**: Base class for fetching and caching data
- **ProcessDataUpdateCoordinator**: Fetches real-time process data
- **SettingDataUpdateCoordinator**: Fetches and writes configuration settings
- **Entity Classes**: Home Assistant entities that expose data/controls

---

## REST API vs MODBUS TCP

### Important Distinction

**This integration uses the REST API, NOT direct MODBUS TCP.**

- **REST API**: HTTP-based API on port 80, provided by `pykoplenti` library (v1.5.0rc1)
- **MODBUS TCP**: Direct register access (not used in this integration)

### Why This Matters

1. **Feature Availability**: Not all MODBUS registers are exposed via REST API
2. **Naming Differences**: REST API uses different naming conventions
3. **Discovery**: REST API dynamically discovers available settings/process data
4. **Limitations**: Some MODBUS features may not be accessible via REST

### How to Check Available Features

The REST API provides discovery methods:

```python
# Get all available process data
available_process_data = await client.get_process_data()

# Get all available settings
available_settings = await client.get_settings()
```

**Diagnostic file** (`diagnostics.py`) automatically collects this information for troubleshooting.

### Mapping MODBUS to REST API

When adding features from MODBUS documentation:

1. **Check REST API first**: Use `get_settings()` or `get_process_data()` to see what's available
2. **Name mapping**: REST API uses different names (e.g., `Battery:ExternControl:AcPowerAbs` vs `Battery:ChargePowerACAbsolute`)
3. **Module structure**: REST API groups data by modules (`devices:local`, `devices:local:battery`, etc.)

---

## Adding New Battery Features

### Step-by-Step Process

#### 1. Identify the Feature

Check if the feature exists in the REST API:

```python
# In diagnostics.py or during setup
available_settings = await client.get_settings()
for module_id, settings in available_settings.items():
    for setting in settings:
        if "Battery" in setting.id:
            print(f"{module_id}/{setting.id} (access: {setting.access})")
```

#### 2. Determine Entity Type

- **Sensor**: Read-only data (process data) → `sensor.py`
- **Switch**: Binary on/off control → `switch.py`
- **Number**: Adjustable numeric value → `number.py`
- **Select**: Choice from predefined options → `select.py`

#### 3. Add Entity Description

**For Number entities** (`number.py`):

```python
PlenticoreNumberEntityDescription(
    key="unique_key_name",
    entity_category=EntityCategory.CONFIG,
    entity_registry_enabled_default=False,  # Hidden by default for advanced features
    icon="mdi:icon-name",
    name="User-Friendly Name",
    native_unit_of_measurement=UnitOfPower.WATT,  # or PERCENTAGE, etc.
    native_max_value=50000,
    native_min_value=0,
    native_step=100,
    module_id="devices:local",
    data_id="Battery:Actual:Setting:Name",  # Must match REST API exactly
    fmt_from="format_round",
    fmt_to="format_round_back",
),
```

**Important**: The `data_id` must match the REST API setting name exactly.

#### 4. Add Safety Validation (for controls)

For battery control entities, add validation in `async_set_native_value()`:

```python
async def async_set_native_value(self, value: float) -> None:
    """Set a new value."""
    if "Battery" in self.data_id:
        # Check installer service code for advanced controls
        if not ensure_installer_access(
            entry,
            requires_installer,
            self.module_id,
            self.data_id,
            "battery control",
        ):
            return
        
        # Validate value ranges
        if abs(value) > SAFE_LIMIT:
            _LOGGER.warning("Value exceeds safe limit")
            return
        
        # Log operation for audit
        _LOGGER.info("Setting battery control %s to %s", self.data_id, value)
```

#### 5. Test Discovery

The integration automatically:
- Checks if setting exists before creating entity
- Skips non-existent settings gracefully
- Logs which entities were created vs skipped
- Falls back to legacy battery IDs when firmware exposes typos

---

## Safety Considerations

### Critical Safety Features

#### 1. Installer Service Code Protection

Advanced battery controls require installer service code:

```python
advanced_controls = [
    "ChargePower",
    "ChargeCurrent",
    "MaxChargePower",
    "MaxDischargePower",
    "TimeUntilFallback",
]
requires_installer = any(control in self.data_id for control in advanced_controls)

if requires_installer and not entry.data.get(CONF_SERVICE_CODE):
    _LOGGER.warning("Installer service code required")
    return  # Block operation
```

#### 2. Value Range Validation

Always validate values before sending to inverter:

```python
# Power limits
if "Power" in self.data_id and abs(value) > 50000:
    _LOGGER.warning("Power setpoint exceeds safe limit (50000W)")
    return

# SoC limits
if "Soc" in self.data_id and (value < 5 or value > 100):
    _LOGGER.warning("SoC value out of safe range (5-100%)")
    return
```

#### 3. Audit Logging

Log all battery control operations:

```python
_LOGGER.info(
    "Setting battery control %s/%s to %s (user: %s)",
    self.module_id,
    self.data_id,
    value,
    user_type,  # "installer" or "user"
)
```

#### 4. Error Handling

Never let exceptions crash the platform:

```python
try:
    # Potentially failing operation
    result = await client.get_setting_values(...)
except (ApiException, ClientError, TimeoutError) as err:
    _LOGGER.warning("Operation failed: %s - continuing without feature", err)
    result = {}  # Safe fallback
```

### Safety Checklist

- [ ] Advanced controls require installer service code
- [ ] Value ranges are validated before sending
- [ ] All operations are logged for audit
- [ ] Errors are handled gracefully (no crashes)
- [ ] Entities are hidden by default (`entity_registry_enabled_default=False`)
- [ ] Min/max values match inverter specifications

---

## Common Issues and Solutions

### Issue 1: StopIteration Error

**Error**:
```
RuntimeError: coroutine raised StopIteration
```

**Cause**: Using `next()` with generator expression in async context.

**Solution**: Use explicit for loop instead:

```python
# ❌ BAD (raises StopIteration)
setting_data = next(
    sd for sd in available_settings_data[module_id]
    if data_id == sd.id
)

# ✅ GOOD (safe)
setting_data = None
for sd in available_settings_data[module_id]:
    if data_id == sd.id:
        setting_data = sd
        break

if setting_data is None:
    _LOGGER.warning("Setting not found")
    continue
```

### Issue 2: Entity Not Appearing

**Possible Causes**:

1. **Setting doesn't exist in REST API**: Check diagnostic file or logs
2. **Wrong `data_id` name**: Must match REST API exactly (case-sensitive)
3. **Cache issue**: Restart Home Assistant completely
4. **Legacy entity registry**: Old unique_id formats can create grey duplicates (select entities)

**Solution**:
```python
# Check what's actually available
available_settings = await client.get_settings()
# Log or print to see actual setting names
```

### Issue 3: API 500 Errors

**Error**:
```
API Error: Unknown API response [500] - None
```

**Cause**: Feature not supported on this inverter model/firmware.

**Solution**: Wrap in try-except and provide fallback:

```python
try:
    result = await client.get_setting_values(...)
except ApiException as err:
    if "Unknown API response [500]" in str(err):
        _LOGGER.info("Feature not supported on this inverter - skipping")
        result = {}  # Safe fallback
    else:
        raise
```

### Issue 4: Custom Component Not Recognized

**Symptoms**:
- Integration shows as built-in, not custom
- Changes not taking effect
- Traceback shows `/usr/src/homeassistant/...` instead of `/config/custom_components/...`

**Solution**:
1. Ensure `manifest.json` has `"version": "2.3.1"` (required for custom components)
2. Ensure folder name matches domain: `custom_components/kostal_plenticore/`
3. Clear Python cache: Delete `__pycache__` folders and `.pyc` files
4. **Full Home Assistant restart** (not just reload)

### Issue 5: Unsafe Data Access

**Error**:
```
KeyError: 'Battery:MinSoc'
```

**Cause**: Accessing `coordinator.data` without checking availability.

**Solution**: Always check `self.available` first:

```python
@property
def native_value(self) -> float | None:
    """Return the current value."""
    if not self.available:
        return None
    return self.coordinator.data[self.module_id][self.data_id]
```

### Issue 6: Duplicate or Grey Select Entities

**Symptoms**:
- `battery_charging_usage_mode` is grey and `_2` exists

**Cause**: Legacy select unique_id format (`entry_id + module_id`).

**Solution**:
- The integration migrates to `entry_id + module_id + key`
- If both exist, the new entry is removed and the old entity_id is preserved

---

## Code Patterns and Best Practices

### Pattern 1: Safe Entity Availability Check

```python
@property
def available(self) -> bool:
    """Return if entity is available."""
    return (
        super().available
        and self.coordinator.data is not None
        and self.module_id in self.coordinator.data
        and self.data_id in self.coordinator.data[self.module_id]
    )
```

### Pattern 6: Calculated Efficiency Sensors

Efficiency sensors are calculated in `sensor.py` under `_calc_`:

- **Battery Efficiency**: `(EnergyDischarge) / (EnergyChargePv + EnergyChargeGrid)`
- **Battery Efficiency PV Only**: `(EnergyDischarge) / (EnergyChargePv)` (only when grid charge is 0)
- **Grid → Battery Efficiency**: `(EnergyChargeGrid) / (EnergyChargeInvIn)`
- **Battery → Grid Efficiency**: `(EnergyDischargeGrid) / (EnergyDischarge)`

### Pattern 2: Graceful Feature Discovery

```python
# Try to get feature
try:
    feature_data = await client.get_setting_values(module_id, feature_id)
except (ApiException, ClientError, TimeoutError) as err:
    if "Unknown API response [500]" in str(err):
        _LOGGER.info("Feature not supported - using fallback")
        feature_data = {}
    else:
        _LOGGER.warning("Error getting feature: %s", err)
        feature_data = {}

# Continue with fallback if needed
if not feature_data:
    # Skip this feature, continue with others
    continue
```

### Pattern 3: Comprehensive Error Handling

```python
try:
    result = await operation()
except ApiException as err:
    modbus_err = parse_modbus_exception(err)
    _LOGGER.error("MODBUS error: %s", modbus_err.message)
    if isinstance(modbus_err, ModbusServerDeviceBusyError):
        _LOGGER.warning("Inverter busy, retry later")
    elif isinstance(modbus_err, ModbusIllegalDataValueError):
        _LOGGER.error("Invalid value provided")
except ClientError as err:
    _LOGGER.error("Network error: %s", err)
except TimeoutError as err:
    _LOGGER.error("Timeout: %s", err)
```

### Pattern 4: Entity Setup with Discovery

```python
async def async_setup_entry(...):
    # 1. Get available settings
    try:
        available_settings = await client.get_settings()
    except Exception as err:
        _LOGGER.error("Could not get settings: %s", err)
        available_settings = {}
    
    entities = []
    
    # 2. Iterate through desired entities
    for description in ENTITY_DESCRIPTIONS:
        # 3. Check if setting exists
        if (
            description.module_id not in available_settings
            or description.data_id not in (
                s.id for s in available_settings[description.module_id]
            )
        ):
            _LOGGER.debug("Skipping non-existing setting %s/%s",
                         description.module_id, description.data_id)
            continue
        
        # 4. Find setting data safely
        setting_data = None
        for sd in available_settings[description.module_id]:
            if description.data_id == sd.id:
                setting_data = sd
                break
        
        if setting_data is None:
            _LOGGER.warning("Setting data not found despite check")
            continue
        
        # 5. Create entity
        entities.append(EntityClass(coordinator, description, setting_data))
    
    # 6. Add all entities at once
    async_add_entities(entities)
```

### Pattern 5: MODBUS Exception Parsing

```python
def parse_modbus_exception(api_exception: ApiException) -> ModbusException:
    """Parse ApiException into specific MODBUS exceptions."""
    # See helper.py for the centralized implementation.
    ...
```

---

## Testing and Debugging

### Diagnostic Information

The integration provides comprehensive diagnostics:

1. **Home Assistant Diagnostics**: Settings → Integrations → Kostal Plenticore → Diagnostics
2. **Logs**: Check Home Assistant logs for:
   - `REST API discovered X battery settings`
   - `Battery number entities created: ...`
   - `Battery number entities NOT available: ...`

### Debugging Checklist

1. **Check Diagnostic File**: See what settings/process data are actually available
2. **Check Logs**: Look for warnings about skipped entities
3. **Verify Setting Names**: Compare `data_id` with actual REST API names
4. **Clear Cache**: Delete `__pycache__` and restart Home Assistant
5. **Check Manifest**: Ensure `version` field is present
6. **Verify Folder Structure**: Must be `custom_components/kostal_plenticore/`
7. **Check entity registry** for legacy unique_id migrations (select entities)

### Common Log Messages

**Good signs**:
```
INFO: REST API discovered 45 battery settings: ...
INFO: Battery number entities created: devices:local/Battery:MinSoc, ...
```

**Warnings (usually OK)**:
```
WARNING: Battery number entities NOT available on this inverter model (skipped): ...
DEBUG: Skipping non existing setting data devices:local/Battery:MaxSoc
```

**Errors (need attention)**:
```
ERROR: MODBUS error writing ... - Invalid value provided
ERROR: Could not get settings data for numbers: ...
```

### Testing New Features

1. **Add entity description** to appropriate platform file
2. **Restart Home Assistant** completely
3. **Check logs** for creation/skipping messages
4. **Check entity registry** to see if entity appears
5. **Test functionality** (read value, write value if applicable)
6. **Check diagnostic file** to verify REST API support

---

## Key Learnings Summary

### 1. REST API Discovery is Essential

- Always check what the REST API actually provides before adding features
- Use `get_settings()` and `get_process_data()` for discovery
- Don't assume MODBUS documentation maps directly to REST API

### 2. Safety First

- Advanced controls must require installer service code
- Always validate value ranges
- Log all control operations for audit
- Never let exceptions crash the platform

### 3. Error Handling is Critical

- Wrap all API calls in try-except blocks
- Provide safe fallbacks for unsupported features
- Parse MODBUS exceptions for better error messages
- Handle 500 errors gracefully (feature not supported)

### 4. Code Patterns Matter

- Use explicit loops instead of `next()` in async contexts
- Always check `available` before accessing `coordinator.data`
- Use defensive programming (check for None, empty dicts, etc.)
- Log operations for debugging and audit

### 5. Home Assistant Integration Requirements

- `manifest.json` must have `version` field for custom components
- Folder name must match domain (`kostal_plenticore`)
- Full restart required after code changes (not just reload)
- Clear Python cache when troubleshooting

---

## Resources

- **Kostal MODBUS Documentation**: `BA_KOSTAL_Interface_MODBUS-TCP_SunSpec_with_Control.md`
- **pykoplenti Library**: `pykoplenti-master/` folder
- **Home Assistant Integration Docs**: https://developers.home-assistant.io/
- **Diagnostic File**: Settings → Integrations → Kostal Plenticore → Diagnostics

---

## Version History

- **v2.1.0**: Added comprehensive battery features, improved error handling, safety validations
- **2026-01**: Added efficiency sensors and legacy select unique_id migration
- **v2.0.0**: Initial custom component version

---

*Last Updated: 2026-01-17*

