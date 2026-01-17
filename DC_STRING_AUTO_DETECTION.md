# DC String Auto-Detection für alle Kostal Plenticore Modelle

## 🎯 Funktionsweise für alle Konstellationen

### **Automatische Erkennung:**
Der Code erkennt automatisch die Anzahl der DC-Strings vom Wechselrichter:
- **G1/G2/G3**: Liest `Properties:StringCnt` vom Wechselrichter
- **Fallback**: Minimum 1 String, wenn Kommunikation fehlschlägt

### **Unterstützte Konstellationen:**

| Modell | DC-Strings | Automatisch erkannt | Beispiel |
|--------|------------|-------------------|---------|
| **G1** | 1-2 | ✅ | PV1, PV1+PV2 |
| **G2** | 1-3 | ✅ | PV1, PV1+PV2, PV1+PV2+PV3 |
| **G3** | 1-3 | ✅ | PV1, PV1+PV2, PV1+PV2+PV3 |

### **Dein Setup (G3 L 20 kW):**
```
✅ PV1 + PV2 = 2 Strings → dc_string_count = 2
✅ DC3 verfügbar → wird automatisch erstellt wenn dc_string_count = 3
✅ Batterie → immer verfügbar
```

### **Sensor-Erstellung:**
```python
# Automatische Generierung für alle verfügbaren Strings
for dc_num in range(1, dc_string_count + 1):
    # Erstellt: DC1 Power, DC1 Voltage, DC1 Current
    #           DC2 Power, DC2 Voltage, DC2 Current  
    #           DC3 Power, DC3 Voltage, DC3 Current (wenn vorhanden)
```

### **Log-Ausgaben:**
```
INFO: Discovered 2 DC strings on inverter
INFO: Added 6 DC sensors (2 Strings × 3 Metriken)
```

## 🔧 Konfiguration

### **Keine manuelle Anpassung nötig!**
Der Code funktioniert automatisch für:
- ✅ **G1 mit 1 String** → DC1 Sensoren
- ✅ **G1 mit 2 Strings** → DC1 + DC2 Sensoren  
- ✅ **G2 mit 1-3 Strings** → DC1 bis DC3 Sensoren
- ✅ **G3 mit 1-3 Strings** → DC1 bis DC3 Sensoren

### **Batterie-Integration:**
- ✅ **Immer verfügbar** (unabhängig von DC-String-Anzahl)
- ✅ **Alle Batterie-Sensoren** werden erstellt
- ✅ **Smart Battery Control** wenn unterstützt

## 🎉 Ergebnis

**Dein G3 L 20 kW funktioniert perfekt:**
1. **Automatische Erkennung** von PV1+PV2 (2 Strings)
2. **DC3 wird hinzugefügt** sobald 3. String erkannt wird
3. **Batterie funktioniert** unabhängig davon
4. **Keine Code-Anpassungen** erforderlich

**Einfach Home Assistant neu starten - der Code erkennt alles automatisch!**
