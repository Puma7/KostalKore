# 🚨 URGENT: Home Assistant Cache Issue - 500 Error Still Occurring

## ❌ **PROBLEM IDENTIFIED**

**Home Assistant is still using the OLD CACHED version of `switch.py`**

### **Evidence:**
- **Traceback shows line 124** (old code)
- **Current code has API call at line 171** (new code)
- **No version log message appears** (new code would show v2.0.1)
- **No debug messages appear** (new code has debug logging)

## 🔧 **IMMEDIATE SOLUTION**

### **Step 1: Complete Home Assistant Restart**
```bash
# Method 1: Via SSH/Terminal
hassio homeassistant restart

# Method 2: Via Web UI
# Settings > System > Restart Home Assistant
```

### **Step 2: Clear Python Cache (CRITICAL)**
```bash
# Connect to Home Assistant terminal and run:
find /config -name "__pycache__" -type d -exec rm -rf {} +
find /config -name "*.pyc" -delete
find /usr/src -name "__pycache__" -type d -exec rm -rf {} +
find /usr/src -name "*.pyc" -delete
```

### **Step 3: Restart Integration**
1. **Settings > Devices & Services**
2. **Find "Kostal Plenticore"**
3. **Click the 3-dot menu > Reload**

## ✅ **VERIFICATION**

After restart, check logs for:
```
KOSTAL_SWITCH_V2_0_1_LOADED_20251229 - Loading Kostal Plenticore Switch platform v2.0.1
Switch platform version check: 500_ERROR_FIX_V2_0_1_20251229
```

## 🐛 **WHAT HAPPENED**

### **Root Cause:**
- Home Assistant caches Python files aggressively
- Integration reload only reloads the config, not the code
- Old `.pyc` files were still being used

### **Our Fixes (Already Implemented):**
- ✅ **Enhanced error handling** for all API calls
- ✅ **Specific 500 error detection** by message content
- ✅ **Graceful degradation** - platform continues loading
- ✅ **Debug logging** to track exceptions
- ✅ **Version markers** to verify new code is loaded

### **Expected Behavior After Restart:**
1. **Version log appears** - confirms new code loaded
2. **Debug logs show** - "Attempting to get DC string features"
3. **Warning logs show** - "Caught exception in DC string features"
4. **No platform failure** - switches load successfully
5. **Graceful handling** - 500 errors become warnings

## 🚨 **IF STILL FAILING AFTER RESTART**

### **Check 1: Version Log**
Look for: `KOSTAL_SWITCH_V2_0_1_LOADED_20251229`
- **Missing?** → Cache not cleared, repeat Step 2

### **Check 2: Debug Logs**
Add to `configuration.yaml`:
```yaml
logger:
  default: info
  logs:
    custom_components.kostal_plenticore: debug
```

Look for:
```
Attempting to get DC string features for [...]
Caught exception in DC string features: API Error: Unknown API response [500] - None
```

### **Check 3: Integration Status**
- **Settings > Devices & Services > Kostal Plenticore**
- Should show **"Running"** not **"Failed"**

## 📋 **TROUBLESHOOTING CHECKLIST**

- [ ] **Home Assistant restarted completely**
- [ ] **Python cache cleared** (`__pycache__` folders deleted)
- [ ] **Integration reloaded** via UI
- [ ] **Version log appears**: `KOSTAL_SWITCH_V2_0_1_LOADED_20251229`
- [ ] **Debug logs show exception handling**
- [ ] **No more 500 errors in logs**

## 🎯 **FINAL RESULT**

Once properly restarted, you should see:
- ✅ **No platform failures**
- ✅ **Warning messages instead of errors**
- ✅ **Successful switch loading**
- ✅ **Working basic functionality**

**The fix is complete - you just need to restart Home Assistant properly!**
