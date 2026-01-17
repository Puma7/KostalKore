# 🔧 Enhanced Shadow Management Detection Implementation

## **✅ Enhanced Debugging and Fallback Methods Implemented**

I've added comprehensive shadow management detection with multiple fallback approaches:

## **🔍 What the Enhanced Code Does:**

### **Method 1: Individual Queries**
- **Problem**: Some inverters don't support batch queries for DC string features
- **Solution**: Try each string individually instead of all at once
- **Log**: `Trying individual DC string feature queries...`

### **Method 2: Alternative Property Patterns**
- **Problem**: Different Kostal models use different property names
- **Solution**: Test multiple property name patterns:
  - `Properties:String%d:Features`
  - `DC:String%d:Features`
  - `Generator:String%dFeatures`
  - `Properties:String%dShadowManagement`
  - `DC:String%dShadowMgmt`
- **Log**: `Found working pattern: {pattern}`

### **Method 3: Debug Available Properties**
- **Problem**: We don't know what properties your inverter actually exposes
- **Solution**: Query all available settings and look for string-related ones
- **Log**: `String-related properties in {module}: {properties}`

## **📊 Enhanced Logging Output:**

### **What You'll See in Debug Mode:**
1. **Initial attempt**: `Attempting to get DC string features for [...]`
2. **500 error handling**: `trying alternative approaches`
3. **Individual queries**: `Trying individual DC string feature queries...`
4. **Pattern testing**: `Trying pattern: Properties:String%d:Features`
5. **Success detection**: `Found working pattern: {pattern}`
6. **Property discovery**: `String-related properties in devices:local: [...]`

## **🎯 Expected Results:**

### **If Your Inverter Supports Shadow Management:**
- ✅ **Method 1 or 2 will succeed**
- ✅ **Shadow management switches will appear**
- ✅ **Clear logs showing which method worked**

### **If API Issues Persist:**
- ✅ **Method 3 will show available properties**
- ✅ **We can identify the correct property names**
- ✅ **Can implement targeted fix**

## **🚀 Next Steps:**

1. **Copy updated files** to `/config/custom_components/kostal_plenticore/`
2. **Restart Home Assistant**
3. **Enable debug logging** to see detailed output:
   ```yaml
   logger:
     default: info
     logs:
       custom_components.kostal_plenticore: debug
   ```
4. **Check logs** for:
   - `Trying alternative approaches`
   - `Found working pattern:`
   - `String-related properties in`

## **🔍 What We'll Learn:**
- **Which property pattern works** for your inverter
- **Whether individual queries work** vs batch queries
- **What DC string properties are actually available**
- **Why the original 500 error occurred**

This comprehensive approach should identify the correct way to detect shadow management on your specific Kostal inverter model!
