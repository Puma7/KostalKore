# Kostal Plenticore Integration - Quick Reference

## 🚀 Quick Start: Adding a New Battery Feature

### 1. Check if Feature Exists
```python
# In Home Assistant, check diagnostics:
# Settings → Integrations → Kostal Plenticore → Diagnostics
# Look for "available_settings_data" → "devices:local"
```

### 2. Add to number.py (for numeric controls)
```python
from kostal_plenticore.const_ids import ModuleId, SettingId
from kostal_plenticore.helper import ensure_installer_access

PlenticoreNumberEntityDescription(
    key="battery_feature_name",
    entity_category=EntityCategory.CONFIG,
    entity_registry_enabled_default=False,
    icon="mdi:battery",
    name="Battery Feature Name",
    native_unit_of_measurement=PERCENTAGE,  # or UnitOfPower.WATT
    native_max_value=100,
    native_min_value=0,
    native_step=1,
    module_id=ModuleId.DEVICES_LOCAL,
    data_id=SettingId.BATTERY_MIN_SOC,  # ⚠️ Must match REST API exactly!
    fmt_from="format_round",
    fmt_to="format_round_back",
),
```

### 3. Restart Home Assistant
- Full restart (not reload)
- Check logs for: `Battery number entities created: ...`

---

## ⚠️ Critical Safety Rules

1. **Advanced controls require installer service code**
   ```python
   if not ensure_installer_access(entry, requires_installer, self.module_id, self.data_id, "battery control"):
       return  # Block operation
   ```
   - Missing service code triggers a Home Assistant repair issue (`installer_required`).

2. **Always validate values**
   ```python
   if abs(value) > SAFE_LIMIT:
       _LOGGER.warning("Value exceeds safe limit")
       return
   ```

3. **Never use `next()` in async code**
   ```python
   # ❌ BAD
   setting_data = next(sd for sd in settings if ...)
   
   # ✅ GOOD
   setting_data = None
   for sd in settings:
       if ...:
           setting_data = sd
           break
   ```

4. **Always check availability**
   ```python
   if not self.available:
       return None
   return self.coordinator.data[...]
   ```

---

## 🔍 Troubleshooting

| Problem | Solution |
|---------|----------|
| Entity not appearing | Check diagnostic file, verify `data_id` matches REST API |
| StopIteration error | Replace `next()` with explicit for loop |
| API 500 error | Feature not supported - wrap in try-except, provide fallback |
| Custom component not recognized | Ensure `manifest.json` has a `version`, restart HA |
| KeyError on data access | Check `self.available` before accessing `coordinator.data` |
| Grey select + _2 entity | Legacy select unique_id; migration should remap to `entry_id + module_id + key` |

---

## 📋 Common Patterns

### Safe Entity Setup
```python
# 1. Get available settings
available_settings = await client.get_settings()

# 2. Check if exists
if data_id not in (s.id for s in available_settings[module_id]):
    continue  # Skip

# 3. Find safely
setting_data = None
for sd in available_settings[module_id]:
    if sd.id == data_id:
        setting_data = sd
        break

# 4. Create entity
if setting_data:
    entities.append(Entity(coordinator, description, setting_data))
```

### Error Handling
```python
try:
    result = await client.operation()
except ApiException as err:
    if "Unknown API response [500]" in str(err):
        _LOGGER.info("Feature not supported - skipping")
        result = {}  # Fallback
    else:
        modbus_err = parse_modbus_exception(err)
        _LOGGER.error("Error: %s", modbus_err.message)
        result = {}
```

---

## 🎯 Key Differences: REST API vs MODBUS

| Aspect | REST API (This Integration) | MODBUS TCP |
|--------|----------------------------|------------|
| Protocol | HTTP on port 80 | TCP on port 502 |
| Library | `pykoplenti` | Direct register access |
| Discovery | Dynamic (`get_settings()`) | Static (documentation) |
| Naming | `Battery:ExternControl:AcPowerAbs` | Register 0x500 |
| Availability | Subset of MODBUS features | All documented features |

**Important**: Not all MODBUS features are available via REST API!
**G3 Battery Limitation**: REST uses `Battery:Limit:Charge_P` and `Battery:Limit:Discharge_P` (fallback via `Battery:Limit:Fallback*`). These are re-applied cyclically to avoid fallback.

---

## 📝 File Locations

- **Number entities**: `number.py` → `NUMBER_SETTINGS_DATA`
- **Switch entities**: `switch.py` → `SWITCH_SETTINGS_DATA`
- **Sensor entities**: `sensor.py` → `SENSOR_PROCESS_DATA` + `_calc_`
- **Select entities**: `select.py` → `SELECT_SETTINGS_DATA`

---

## 🎛️ Control Entity List

### Numbers
- Battery min SoC
- Battery min Home Consumption
- Battery Charge Power (AC) Absolute
- Battery Charge Current (DC) Relative
- Battery Charge Power (AC) Relative
- Battery Charge Current (DC) Absolute
- Battery Charge Power (DC) Absolute
- Battery Charge Power (DC) Relative
- Battery Max Charge Power (G3)
- Battery Max Discharge Power (G3)
- Battery Max Charge Power Fallback (G3)
- Battery Max Discharge Power Fallback (G3)
- Battery Time Until Fallback (G3)
- Battery External Control Max Charge Power Absolute
- Battery External Control Max Discharge Power Absolute
- Battery External Control Max SoC Relative
- Battery External Control Min SoC Relative
- Battery ESB Minimum SoC
- Battery ESB Start SoC
- Battery Winter Minimum SoC
- Battery Winter Start Month
- Battery Winter End Month
- Battery Minimum Grid Feed-in
- Battery Communication Monitor Time
- Energy Management Battery Control Power Offset
- Energy Management Limit Grid Supply
- Energy Management Smart Control Fallback Max Time
- Timed Battery Charge Grid Power
- Timed Battery Charge SoC
- Timed Battery Charge Weekend Grid Power
- Timed Battery Charge Weekend SoC
- Active Power Gradient Mode
- Active Power Gradient Mode Low Priority
- Active Power PT1 Tau
- Active Power PT1 Low Priority Tau
- Active Power Ramp Time
- Active Power Limit Grid Supply
- Active Power P
- Active Power P Fine
- Inverter Active Power Limitation
- Inverter Active Power Consumption Limitation
- Reactive Power Fix Cos Phi Delta
- Reactive Power Fix Q
- Reactive Power Ext Ctrl Fix Cos Phi Delta
- Reactive Power Ext Ctrl Fix Q
- Reactive Power Ext Ctrl Settling Time
- Reactive Power Power Limit Input Priority High Mode
- LVRT K Factor
- HVRT K Factor
- LVRT Threshold
- HVRT Threshold
- LVRT Lower Voltage
- HVRT Upper Voltage
- LVRT/HVRT Gradient and Time
- Power of Frequency Nominal Frequency
- Power of Frequency Reduction Start Frequency
- Power of Frequency Reduction End Frequency
- Power of Frequency Delay Reaction Time
- Power of Voltage Reduction Start Voltage
- Power of Voltage Reduction End Voltage
- Power of Voltage Settling Time
- Digital Out 1 Power Control On Threshold
- Digital Out 1 Power Control Off Threshold
- Digital Out 1 Power Control Delay Time
- Digital Out 1 Power Control Stable Time
- Digital Out 1 Power Control Run Time
- Digital Out Battery Discharge SoC
- Digital Out Output Enable SoC

### Switches
- Battery Strategy
- Battery Manual Charge
- Battery Close Separator
- Battery Communication Monitor
- Battery Dynamic SoC
- Battery Mode Home Consumption
- Battery Smart Control
- Battery Time Control
- AC Storage
- Battery Disable Discharge
- Smart Control Fallback
- Active Power Gradient Mode
- Active Power PT1 Mode
- Inverter Active Power Consumption Limitation
- HVRT Enable
- LVRT Enable
- Power of Frequency Enable
- Power of Voltage Enable
- SPD Enable
- ESB Sleep Mode Allowed
- Generator Swap Detection
- Reactive Power QOfUP Hold On Voltage Return
- Power of Frequency Hold Power On Decrease
- Power of Frequency Increase Enable
- Power of Voltage Hold Power On Decrease
- LVRT/HVRT Add Reactive Power Before Fault
- LVRT/HVRT Fill Up With Active Current
- Digital Out 1 External Control
- Digital Out 2 External Control
- Digital Out 3 External Control
- Digital Out 4 External Control
- Power Average Enable
- Power Average Ramp After Power Reduction

### Selects
- Battery Charging / Usage mode

---

## 🔐 Safety Checklist

- [ ] Advanced controls require installer service code
- [ ] Value ranges validated before sending
- [ ] All operations logged for audit
- [ ] Errors handled gracefully (no crashes)
- [ ] Entities hidden by default
- [ ] Min/max values match inverter specs

---

## 📊 Diagnostic Commands

```python
# In Home Assistant Python console or diagnostics.py:
available_settings = await client.get_settings()
for module_id, settings in available_settings.items():
    for setting in settings:
        if "Battery" in setting.id:
            print(f"{module_id}/{setting.id} (access: {setting.access})")
```

---

## ⚡ Example Service Calls

### Battery Limit (G3)
```yaml
service: number.set_value
target:
  entity_id: number.scb_battery_max_charge_power_g3
data:
  value: 10000
```

### Battery Charging / Usage Mode
```yaml
service: select.select_option
target:
  entity_id: select.scb_battery_charging_usage_mode
data:
  option: Battery:SmartBatteryControl:Enable
```

---

## 🐛 Debug Checklist

1. ✅ Check diagnostic file
2. ✅ Check Home Assistant logs
3. ✅ Verify `data_id` matches REST API exactly
4. ✅ Clear `__pycache__` folders
5. ✅ Full Home Assistant restart
6. ✅ Check `manifest.json` has `version` field
7. ✅ Verify folder structure: `custom_components/kostal_plenticore/`

---

## 📈 Efficiency Sensors (Calculated)

These are created under `_calc_` in `sensor.py`:

- **Battery Efficiency**: `(EnergyDischarge) / (EnergyChargePv + EnergyChargeGrid)`
- **Battery Efficiency PV Only**: `(EnergyDischarge) / (EnergyChargePv)` when grid charge is 0
- **Grid → Battery Efficiency**: `(EnergyChargeGrid) / (EnergyChargeInvIn)`
- **Battery → Grid Efficiency**: `(EnergyDischargeGrid) / (EnergyDischarge)`

---

## 🔋 Modbus Battery Charge/Discharge Control

### Prerequisites

1. **Enable Modbus** in the integration options (Settings → Integrations → Kostal Plenticore → Configure → Enable Modbus)
2. **Battery Management Mode** must be set to allow external control on the inverter itself (register 1080). Check this in the Modbus diagnostic report (`battery_mgmt_mode`).

### Available Control Entities (via Modbus)

When Modbus is enabled, these `number` entities are created automatically:

| Entity | Register | Unit | Description |
|--------|----------|------|-------------|
| **Battery Charge Power (Modbus)** | 1034 | W | Set absolute DC charge/discharge power. Positive = charge, negative = discharge. |
| **Battery Max Charge Limit (Modbus)** | 1038 | W | Maximum allowed charge power. |
| **Battery Max Discharge Limit (Modbus)** | 1040 | W | Maximum allowed discharge power. |
| **Battery Min SoC (Modbus)** | 1042 | % | Minimum state of charge (battery won't discharge below this). |
| **Battery Max SoC (Modbus)** | 1044 | % | Maximum state of charge (battery won't charge above this). |
| **G3 Max Battery Charge Power (Modbus)** | 1280 | W | G3-specific max charge power limit (with fallback timer). |
| **G3 Max Battery Discharge Power (Modbus)** | 1282 | W | G3-specific max discharge power limit (with fallback timer). |

### How to Control Charging/Discharging

**Method 1: Direct power control (Register 1034)**
- Use `number.set_value` on "Battery Charge Power (Modbus)"
- **Positive value** → force charge at specified power (e.g., 2000 W)
- **Negative value** → force discharge at specified power (e.g., -2000 W)
- **Zero** → normal automatic operation

**Method 2: G3 power limits (Registers 1280/1282)** — recommended for G3 inverters
- Set `G3 Max Battery Charge Power` to limit how fast the battery charges
- Set `G3 Max Battery Discharge Power` to limit how fast it discharges
- These registers have a **fallback timer** (register 1288): after the timer expires, the inverter reverts to fallback limits (registers 1284/1286). The integration refreshes these automatically.

**Method 3: Charge/discharge limits (Registers 1038/1040)**
- Set `Battery Max Charge Limit` and `Battery Max Discharge Limit` to cap power
- Useful for protecting battery longevity or grid feed-in limits

### Example: Home Assistant Automation

```yaml
# Charge battery at 3000W from grid (e.g., during cheap electricity)
service: number.set_value
target:
  entity_id: number.YOUR_INVERTER_battery_charge_power_modbus
data:
  value: 3000

# Discharge battery at 2000W (e.g., during peak prices)
service: number.set_value
target:
  entity_id: number.YOUR_INVERTER_battery_charge_power_modbus
data:
  value: -2000

# Return to automatic mode
service: number.set_value
target:
  entity_id: number.YOUR_INVERTER_battery_charge_power_modbus
data:
  value: 0
```

### Read-only Battery Status (Modbus Sensors)

| Sensor | Register | Unit | Description |
|--------|----------|------|-------------|
| Battery SoC | 514 | % | Current state of charge |
| Battery Temperature | 214 | °C | Current battery temperature |
| Battery Voltage | 216 | V | Current battery voltage |
| Battery Charge/Discharge Power | 582 | W | Actual power (+ = charging, - = discharging) |
| Battery Cycles | 194 | cycles | Total charge/discharge cycles |
| Battery Max Charge (HW) | 1076 | W | Hardware-reported max charge power |
| Battery Max Discharge (HW) | 1078 | W | Hardware-reported max discharge power |

---

*For detailed information, see `DEVELOPMENT_GUIDE.md`*

