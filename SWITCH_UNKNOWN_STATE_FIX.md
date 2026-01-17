# Switch.py Unknown State Fix - Zusammenfassung

## ✅ Implementierte Verbesserung

### **Problem:**
Der "Manual Charge" Switch wurde nach HA-Neustart als "ausgeschaltet" angezeigt, was zu unbeabsichtigten Aktionen führen konnte.

### **🔧 Lösung:**
Die `is_on` Property gibt jetzt `None` während der Initialisierungsphase zurück, was in Home Assistant als "unbekannt" angezeigt wird.

### **📝 Code Änderungen:**

#### **PlenticoreDataSwitch.is_on Property:**
```python
# Vorher:
@property
def is_on(self) -> bool:
    """Return true if device is on."""
    if not self.available or self.coordinator.data is None:
        return False  # ❌ Zeigte "ausgeschaltet" an
    
# Nachher:
@property
def is_on(self) -> bool:
    """Return true if device is on."""
    if not self.available or self.coordinator.data is None:
        return None  # ✅ Zeigt "unbekannt" an
```

#### **PlenticoreShadowMgmtSwitch.is_on Property:**
```python
# Vorher:
@property
def is_on(self) -> bool:
    """Return true if shadow management is on."""
    return (self._get_shadow_mgmt_value() & self._mask) != 0

# Nachher:
@property
def is_on(self) -> bool:
    """Return true if shadow management is on."""
    if not self.available or self.coordinator.data is None:
        return None  # ✅ Zeigt "unbekannt" an
    return (self._get_shadow_mgmt_value() & self._mask) != 0
```

## 🎯 Verhalten nach der Änderung

### **Vorher (Problematisch):**
1. **HA startet** → Switch Entity erstellt
2. **Keine Daten verfügbar** → `is_on()` gibt `False` zurück
3. **HA zeigt "ausgeschaltet"** an
4. **Nutzer könnte versehentlich ausschalten**
5. **Nach 30s** → Echter Status synchronisiert

### **Nachher (Verbessert):**
1. **HA startet** → Switch Entity erstellt
2. **Keine Daten verfügbar** → `is_on()` gibt `None` zurück
3. **HA zeigt "unbekannt"** an
4. **Nutzer kann keine Aktion auslösen**
5. **Nach 30s** → Echter Status synchronisiert

## 📋 Vorteile der Änderung

### **🔒 Sicherheit:**
- ✅ **Keine unbeabsichtigten Aktionen** während Initialisierung
- ✅ **Klare Kommunikation** des Status ("unbekannt" statt falscher Status)
- ✅ **Verhindert versehentliches Ausschalten** von wichtigen Funktionen

### **👤 User Experience:**
- ✅ **Klarer Status** während Startup
- ✅ **Keine Verwirrung** über falschen Status
- ✅ **Intuitives Verhalten** - Switch erst bedienbar wenn Daten da

### **🛡️ Robustheit:**
- ✅ **Bessere Fehlerbehandlung** während Initialisierung
- ✅ **Consistentes Verhalten** für alle Switch-Typen
- ✅ **Keine Race Conditions** mehr

## 🎯 Technische Details

### **Home Assistant State Handling:**
- `None` → "unbekannt" (grau, nicht bedienbar)
- `True` → "eingeschaltet" (grün, bedienbar)
- `False` → "ausgeschaltet" (rot, bedienbar)

### **Timing:**
- **0-30s nach HA-Start**: "unbekannt"
- **Nach 30s**: Echter Status vom Inverter

### **Betroffene Switches:**
- ✅ **Battery Manual Charge**
- ✅ **Alle Shadow Management Switches**
- ✅ **Alle anderen Switches** (falls vorhanden)

## 🚀 Ergebnis

**Die Switches zeigen jetzt korrekt "unbekannt" während der Initialisierung an, was unbeabsichtigte Aktionen verhindert und die User Experience verbessert!** 🎉
