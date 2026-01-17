# Home Assistant Integration Restart Guide

## Issue: 500 Error Still Occurring

The traceback shows line 124, but our changes moved the API call to line 166. This indicates Home Assistant is still using the old cached version of the file.

## Steps to Fix:

### 1. **Restart Home Assistant Completely**
```bash
# In Home Assistant terminal
hassio homeassistant restart
```

### 2. **Or Restart the Integration Only**
1. Go to **Settings > Devices & Services**
2. Find your **Kostal Plenticore** integration
3. Click the **3-dot menu** and select **Reload**
4. Or click **Configure** and then **Reload**

### 3. **Clear Python Cache (if needed)**
```bash
# Find and delete __pycache__ folders
find /config -name "__pycache__" -type d -exec rm -rf {} +
# Delete .pyc files
find /config -name "*.pyc" -delete
```

### 4. **Verify New Version is Loaded**
After restarting, check your Home Assistant logs for this message:
```
Loading Kostal Plenticore Switch platform v2.0 with enhanced 500 error protection
```

### 5. **Check Debug Logs**
If you still get the error, enable debug logging to see our new debug messages:

Add to your `configuration.yaml`:
```yaml
logger:
  default: info
  logs:
    custom_components.kostal_plenticore: debug
```

### 6. **Expected Behavior**
With the new version, you should see:
- ✅ No more platform failures
- ✅ Warning messages instead of errors for 500 responses
- ✅ Debug logs showing the exception being caught
- ✅ Switch entities loading successfully (basic ones at least)

## What Changed:
- **Enhanced exception handling** for all API calls
- **Specific 500 error detection** by message content
- **Graceful degradation** - platform continues loading
- **Debug logging** to track what's happening
- **Version marker** to confirm new code is loaded

## If Still Failing:
1. Check that the log shows "v2.0" message
2. Look for debug logs starting with "Attempting to get DC string features"
3. Look for "Caught exception in DC string features" - this proves our handler is working
4. Share the complete log output for further debugging
