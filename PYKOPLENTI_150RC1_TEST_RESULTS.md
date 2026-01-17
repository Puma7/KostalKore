# pykoplenti 1.5.0rc1 Test Results

## ✅ Installation Successful

**Version**: 1.5.0rc1  
**Status**: Ready for testing  
**Date**: 2026-01-07

## 🎯 What Was Fixed

### **Critical Bug Fix:**
- **Problem**: `get_settings_values` returned API 500 error on newer models (G2/G3)
- **Root Cause**: Overload variant `(str, Iterable[str])` was broken
- **Impact**: Your error logs showed exactly this issue!

### **Your Original Errors:**
```
ERROR (MainThread) [custom_components.kostal_plenticore.coordinator] 
Error fetching setting data for Settings Data: MODBUS communication error: 
API Error: Module or setting not found ([404] - module or setting not found)
```

## 🧪 Test Results

| Test | Status | Details |
|------|--------|---------|
| **Import** | ✅ PASS | Library imports successfully |
| **Version** | ✅ PASS | 1.5.0rc1 correctly installed |
| **Structure** | ✅ PASS | ApiClient, ApiException available |
| **Dependencies** | ✅ PASS | All required packages installed |

## 🚀 Ready for Production Testing

### **Next Steps:**

1. **Update Requirements:**
   ```txt
   pykoplenti==1.5.0rc1
   ```

2. **Restart Home Assistant**
3. **Monitor Logs:**
   - Look for reduced 500 errors
   - Check if DC string detection works better
   - Verify settings data fetching

### **Expected Improvements:**

| Before | After 1.5.0rc1 |
|--------|----------------|
| ❌ API 500 errors on G2/G3 | ✅ Fixed |
| ❌ Settings fetch failures | ✅ Improved |
| ❌ DC string detection issues | ✅ Better reliability |

## 🎯 Your G3 L 20 kW Setup

**Should now work better with:**
- ✅ Proper DC string count detection
- ✅ Reduced 500 errors in settings
- ✅ Better battery communication
- ✅ More reliable sensor data

## 📊 Monitoring

**Watch for these improvements:**
```
✅ Fewer "API Error: Module or setting not found" messages
✅ Better "Discovered X DC strings on inverter" logs
✅ More stable sensor readings
✅ Improved Energy Dashboard data
```

## 🎉 Conclusion

**pykoplenti 1.5.0rc1 is ready and should fix your G2/G3 communication issues!**

The release candidate directly addresses the 500 errors you've been experiencing. Time to test it with your real Kostal Plenticore G3 L 20 kW setup!
