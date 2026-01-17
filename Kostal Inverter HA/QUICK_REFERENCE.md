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
    module_id="devices:local",
    data_id="Battery:Actual:RestApi:Name",  # ⚠️ Must match REST API exactly!
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
   if requires_installer and not entry.data.get(CONF_SERVICE_CODE):
       return  # Block operation
   ```

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
| Custom component not recognized | Add `"version": "2.1.0"` to `manifest.json`, restart HA |
| KeyError on data access | Check `self.available` before accessing `coordinator.data` |

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
        modbus_err = _parse_modbus_exception(err)
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

---

## 📝 File Locations

- **Number entities**: `number.py` → `NUMBER_SETTINGS_DATA`
- **Switch entities**: `switch.py` → `SWITCH_SETTINGS_DATA`
- **Sensor entities**: `sensor.py` → `SENSOR_SETTINGS_DATA`
- **Select entities**: `select.py` → `SELECT_SETTINGS_DATA`

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

## 🐛 Debug Checklist

1. ✅ Check diagnostic file
2. ✅ Check Home Assistant logs
3. ✅ Verify `data_id` matches REST API exactly
4. ✅ Clear `__pycache__` folders
5. ✅ Full Home Assistant restart
6. ✅ Check `manifest.json` has `version` field
7. ✅ Verify folder structure: `custom_components/kostal_plenticore/`

---

*For detailed information, see `DEVELOPMENT_GUIDE.md`*

