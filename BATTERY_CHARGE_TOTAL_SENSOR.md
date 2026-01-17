# Battery Charge Total Sensor - Implementierungsvorschlag

## 🎯 Anforderung

Sie möchten einen kombinierten Sensor, der die **gesamte Batterieladung** aus beiden Quellen (Grid + PV) anzeigt.

## 🔍 Aktuelle Situation

### **✅ Vorhandene Sensoren:**
- **Battery Charge from Grid** (Day/Month/Year/Total)
- **Battery Charge from PV** (Day/Month/Year/Total)
- **Battery Discharge Total** (berechnet, Day/Month/Year/Total)
- **Total Grid Consumption** (berechnet, Day/Month/Year/Total)

### **❌ Fehlender Sensor:**
- **Battery Charge Total** (Grid + PV kombiniert)

## 🔧 Implementierungsvorschlag

### **1. Neue Sensor Definition hinzufügen:**

```python
# In SENSOR_SETTINGS_DATA (nach Zeile 907)
PlenticoreSensorEntityDescription(
    module_id="_calc_",
    key="BatteryChargeTotal:Day",
    name="Battery Charge Total Day",
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    device_class=SensorDeviceClass.ENERGY,
    state_class=SensorStateClass.TOTAL_INCREASING,
    formatter="format_energy",
),
PlenticoreSensorEntityDescription(
    module_id="_calc_",
    key="BatteryChargeTotal:Month",
    name="Battery Charge Total Month",
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    device_class=SensorDeviceClass.ENERGY,
    state_class=SensorStateClass.TOTAL_INCREASING,
    formatter="format_energy",
),
PlenticoreSensorEntityDescription(
    module_id="_calc_",
    key="BatteryChargeTotal:Year",
    name="Battery Charge Total Year",
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    device_class=SensorDeviceClass.ENERGY,
    state_class=SensorStateClass.TOTAL_INCREASING,
    formatter="format_energy",
),
PlenticoreSensorEntityDescription(
    module_id="_calc_",
    key="BatteryChargeTotal:Total",
    name="Battery Charge Total Total",
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    device_class=SensorDeviceClass.ENERGY,
    state_class=SensorStateClass.TOTAL_INCREASING,
    formatter="format_energy",
),
```

### **2. Berechnungslogik hinzufügen:**

```python
# In PlenticoreCalculatedSensor.native_value (nach Zeile 1575)
elif "BatteryChargeTotal" in self.data_id:
    # Battery Charge from Grid + Battery Charge from PV
    charge_grid = self._get_sensor_value("scb:statistic:EnergyFlow", f"Statistic:EnergyChargeGrid:{period}")
    charge_pv = self._get_sensor_value("scb:statistic:EnergyFlow", f"Statistic:EnergyChargePv:{period}")
    
    # If any component is None, return None to avoid calculating partial sums
    if charge_grid is None or charge_pv is None:
        return None
    
    val_grid = float(charge_grid)
    val_pv = float(charge_pv)
    total_charge = val_grid + val_pv
    return self._formatter(str(total_charge))
```

## 📋 Übersicht aller Battery Sensoren

| Sensor | Typ | Zeitraum | Beschreibung |
|--------|------|----------|-------------|
| **Battery Charge from Grid** | Direkt | Day/Month/Year/Total | Batterieladung aus Netz |
| **Battery Charge from PV** | Direkt | Day/Month/Year/Total | Batterieladung aus PV |
| **Battery Charge Total** | **Berechnet** | Day/Month/Year/Total | **Gesamte Batterieladung** |
| **Battery Discharge Total** | Berechnet | Day/Month/Year/Total | Gesamte Batterieentladung |
| **Battery Efficiency** | Berechnet | Day/Month/Year/Total | Batterieeffizienz |

## 🎯 Nutzen des neuen Sensors

### **Vorteile:**
- ✅ **Einfache Überwachung** der gesamten Batterieladung
- ✅ **Konsistente Daten** für Energy Dashboard
- ✅ **Automatische Berechnung** aus Grid + PV
- ✅ **Alle Zeitperioden** verfügbar (Day/Month/Year/Total)

### **Anwendungsfälle:**
- **Energy Dashboard** - Gesamt-Batterieladung visualisieren
- **Automatisierungen** - Gesamtladung als Trigger verwenden
- **Statistiken** - Batterienutzung überwachen
- **Kostenanalyse** - Gesamte Ladekosten berechnen

## 🔧 Implementierungsschritte

1. **Sensor Definitionen** hinzufügen
2. **Berechnungslogik** implementieren
3. **Testen** mit echten Daten
4. **Dokumentation** aktualisieren

## 🚀 Ergebnis

**Nach der Implementierung hätten Sie einen vollständigen Satz an Battery Charge Sensoren:**

- ✅ **Direkte Sensoren** für Grid und PV
- ✅ **Kombinierter Sensor** für Gesamt-Ladung
- ✅ **Alle Zeitperioden** abgedeckt
- ✅ **Konsistente Berechnung** mit Fehlerbehandlung

**Das würde die Energy-Überwachung deutlich vereinfachen und vollständigere Analysen ermöglichen!** 🎉
