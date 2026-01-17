# Battery Charge Sensoren Übersicht

## ✅ Ja, wir haben beide Sensoren!

### **🔋 Battery Charge from Grid Sensoren:**

```python
# sensor.py Zeilen 727-760
PlenticoreSensorEntityDescription(
    module_id="scb:statistic:EnergyFlow",
    key="Statistic:EnergyChargeGrid:Day",
    name="Battery Charge from Grid Day",
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    device_class=SensorDeviceClass.ENERGY,
    state_class=SensorStateClass.TOTAL_INCREASING,
),
PlenticoreSensorEntityDescription(
    module_id="scb:statistic:EnergyFlow",
    key="Statistic:EnergyChargeGrid:Month",
    name="Battery Charge from Grid Month",
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    device_class=SensorDeviceClass.ENERGY,
    state_class=SensorStateClass.TOTAL_INCREASING,
),
PlenticoreSensorEntityDescription(
    module_id="scb:statistic:EnergyFlow",
    key="Statistic:EnergyChargeGrid:Year",
    name="Battery Charge from Grid Year",
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    device_class=SensorDeviceClass.ENERGY,
    state_class=SensorStateClass.TOTAL_INCREASING,
),
PlenticoreSensorEntityDescription(
    module_id="scb:statistic:EnergyFlow",
    key="Statistic:EnergyChargeGrid:Total",
    name="Battery Charge from Grid Total",
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    device_class=SensorDeviceClass.ENERGY,
    state_class=SensorStateClass.TOTAL_INCREASING,
),
```

### **☀️ Battery Charge from PV Sensoren:**

```python
# sensor.py Zeilen 763-796
PlenticoreSensorEntityDescription(
    module_id="scb:statistic:EnergyFlow",
    key="Statistic:EnergyChargePv:Day",
    name="Battery Charge from PV Day",
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    device_class=SensorDeviceClass.ENERGY,
    state_class=SensorStateClass.TOTAL_INCREASING,
),
PlenticoreSensorEntityDescription(
    module_id="scb:statistic:EnergyFlow",
    key="Statistic:EnergyChargePv:Month",
    name="Battery Charge from PV Month",
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    device_class=SensorDeviceClass.ENERGY,
    state_class=SensorStateClass.TOTAL_INCREASING,
),
PlenticoreSensorEntityDescription(
    module_id="scb:statistic:EnergyFlow",
    key="Statistic:EnergyChargePv:Year",
    name="Battery Charge from PV Year",
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    device_class=SensorDeviceClass.ENERGY,
    state_class=SensorStateClass.TOTAL_INCREASING,
),
PlenticoreSensorEntityDescription(
    module_id="scb:statistic:EnergyFlow",
    key="Statistic:EnergyChargePv:Total",
    name="Battery Charge from PV Total",
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    device_class=SensorDeviceClass.ENERGY,
    state_class=SensorStateClass.TOTAL_INCREASING,
),
```

## 📋 Übersicht aller Battery Charge Sensoren

| Sensor | Einheit | Zeitraum | Beschreibung |
|--------|---------|----------|-------------|
| **Battery Charge from Grid Day** | kWh | Tag | Batterieladung aus Netz am aktuellen Tag |
| **Battery Charge from Grid Month** | kWh | Monat | Batterieladung aus Netz im aktuellen Monat |
| **Battery Charge from Grid Year** | kWh | Jahr | Batterieladung aus Netz im aktuellen Jahr |
| **Battery Charge from Grid Total** | kWh | Gesamt | Gesamte Batterieladung aus Netz |
| **Battery Charge from PV Day** | kWh | Tag | Batterieladung aus PV am aktuellen Tag |
| **Battery Charge from PV Month** | kWh | Monat | Batterieladung aus PV im aktuellen Monat |
| **Battery Charge from PV Year** | kWh | Jahr | Batterieladung aus PV im aktuellen Jahr |
| **Battery Charge from PV Total** | kWh | Gesamt | Gesamte Batterieladung aus PV |

## 🔍 Zusätzliche PV Sensoren

Wir haben auch detaillierte PV Sensoren:

### **☀️ PV Energie Sensoren:**
- **Energy PV1/2/3 Day/Month/Year/Total** - Energie pro PV-String
- **Home Consumption from PV Day/Month/Year/Total** - PV-Energie für Home Consumption

### **🔋 Berechnete Sensoren:**
- **Battery Efficiency** - Berechnet aus Charge PV + Charge Grid
- **Total Grid Consumption** - Berechnet aus Grid Home + Grid Battery

## 🎯 Fazit

**Ja, wir haben beide Sensoren vollständig implementiert!**

- ✅ **Battery Charge from Grid** - 4 Sensoren (Day/Month/Year/Total)
- ✅ **Battery Charge from PV** - 4 Sensoren (Day/Month/Year/Total)
- ✅ **Zusätzliche PV Sensoren** für detaillierte Analyse
- ✅ **Berechnete Sensoren** für Efficiency und Total Consumption

**Alle Sensoren sind mit korrekten Einheiten (kWh), Device Class (ENERGY) und State Class (TOTAL_INCREASING) konfiguriert!** 🎉
