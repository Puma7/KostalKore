# 🔍 Shadow Management Detection in Progress

## **✅ Enhanced Detection is Working!**

The log shows our enhanced code is active:
```
Inverter API returned 500 error for DC string features - trying alternative approaches
```

## **📊 What to Look For Next:**

After this message, you should see one of these sequences:

### **Method 1: Individual Queries**
```
Trying individual DC string feature queries...
Successfully got feature Properties:String0Features: {...}
```

### **Method 2: Alternative Patterns** (if Method 1 fails)
```
Trying alternative approaches...
Trying pattern: Properties:String%d:Features
Pattern Properties:String%d:Features worked for string 1: {...}
Found working pattern: Properties:String%d:Features
```

### **Method 3: Debug Properties** (if both fail)
```
Attempting to debug available properties...
Available modules: ['devices:local', 'inverter']
String-related properties in devices:local: ['Properties:String0Features', 'Properties:String1Features']
```

## **🔧 Enable Debug Logging to See Details:**

Add this to your `configuration.yaml`:
```yaml
logger:
  default: info
  logs:
    custom_components.kostal_plenticore: debug
```

Then restart Home Assistant to see the detailed detection process.

## **🎯 Expected Outcomes:**

### **✅ Success Scenario:**
- Individual queries work OR
- Alternative pattern found OR  
- Debug shows correct property names
- **Result**: Shadow management switches appear

### **⚠️ Information Scenario:**
- All methods fail but debug shows available properties
- **Result**: We learn what properties your inverter actually uses
- **Next step**: Implement correct property names

## **📋 What to Share:**
Please share the logs that appear after the "trying alternative approaches" message. This will show us:
1. Which method worked
2. What property patterns your inverter uses  
3. Whether shadow management can be detected

The detection process is now running - we just need to see what it finds!
