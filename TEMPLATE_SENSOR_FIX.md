# Template Sensor Fix für "unavailable" Fehler

## 🎯 Problem
Deine Template-Sensoren versuchen "unavailable" in float umzuwandeln, was fehlschlägt.

## 🔧 Lösung: Template-Sensoren korrigieren

### **Falsch (verursacht Fehler):**
```yaml
{% set home = states('sensor.wr_home_consumption_from_battery_total') %}
{% set grid = states('sensor.wr_energy_discharge_to_grid_total') %}
{% if home in ['unavailable', 'unknown', 'none'] or grid in ['unavailable', 'unknown', 'none'] %}
  unavailable
{% else %}
  {{ (home|float(0)) + (grid|float(0)) }}
{% endif %}
```

### **Korrekt (mit default value):**
```yaml
{% set home = states('sensor.wr_home_consumption_from_battery_total') | default(0) %}
{% set grid = states('sensor.wr_energy_discharge_to_grid_total') | default(0) %}
{% if home == 'unavailable' or grid == 'unavailable' %}
  unavailable
{% else %}
  {{ (home|float(0)) + (grid|float(0)) }}
{% endif %}
```

### **Oder noch besser (robuster):**
```yaml
{% set home = states('sensor.wr_home_consumption_from_battery_total') %}
{% set grid = states('sensor.wr_energy_discharge_to_grid_total') %}
{% if home in ['unavailable', 'unknown', 'none'] or grid in ['unavailable', 'unknown', 'none'] %}
  unavailable
{% else %}
  {{ (home|float(0) if home != 'unavailable' else 0) + (grid|float(0) if grid != 'unavailable' else 0) }}
{% endif %}
```

## 📋 Zu korrigierende Template-Sensoren

Basierend auf deinen Logs, diese Sensoren korrigieren:

1. **sensor.wr_home_consumption_from_battery_total**
2. **sensor.wr_home_usage_from_grid_pv_day** 
3. **sensor.wr_home_consumption_from_grid_day**
4. **sensor.wr_charge_from_grid_all**
5. **sensor.wr_home_consumption_from_grid_total**
6. **sensor.grid_to_battery_ac_energy_total**
7. **sensor.wr_battery_charge_from_grid_day**
8. **sensor.wr_battery_charge_from_grid_total**
9. **sensor.wr_battery_charge_from_grid_pv_day**
10. **sensor.wr_battery_charge_total_pv_grid**
11. **sensor.grid_to_battery_loss_total**
12. **sensor.battery_to_ac_loss_total**

## 🚀 Schnellste Lösung

**In Home Assistant Configuration.yaml:**
```yaml
template:
  - sensor:
      - name: "WR Home Consumption from Battery Total"
        unit_of_measurement: "kWh"
        device_class: energy
        state_class: total_increasing
        state: >
          {% set home = states('sensor.wr_home_consumption_from_battery_total') %}
          {% set grid = states('sensor.wr_energy_discharge_to_grid_total') %}
          {% if home in ['unavailable', 'unknown', 'none'] or grid in ['unavailable', 'unknown', 'none'] %}
            unavailable
          {% else %}
            {{ (home|float(0) if home != 'unavailable' else 0) + (grid|float(0) if grid != 'unavailable' else 0) }}
          {% endif %}
```

## ✅ Ergebnis

Nach der Korrektur:
- ✅ Keine `ValueError: could not convert string to float` mehr
- ✅ Template-Sensoren funktionieren auch wenn Quell-Sensoren unavailable sind
- ✅ Energy Dashboard zeigt korrekte Werte an
