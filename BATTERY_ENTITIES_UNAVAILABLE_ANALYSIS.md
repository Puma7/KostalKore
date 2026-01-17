# Analyse: Warum Battery Entities nicht verfügbar sind

## 🔍 Problem

Die folgenden Battery-Entities sind nicht verfügbar:
- `Battery:MinSocRel` (Battery min SoC)
- `Battery:MinHomeConsumption` (Battery min Home Consumption)

## 🎯 Mögliche Ursachen

### **1. Inverter-Modell-spezifische Verfügbarkeit**

**Mögliche Gründe:**
- **Firmware-Version** - Ältere Versionen unterstützen diese Einstellungen nicht
- **Hardware-Version** - Ältere Inverter-Modelle haben diese Features nicht
- **Battery-Modell** - Manche Batterien unterstützen bestimmte Einstellungen nicht
- **Konfiguration** - Einstellungen sind deaktiviert oder nicht konfiguriert

### **2. Berechtigungsprobleme**

**Mögliche Gründe:**
- **Installateur-Zugriff** erforderlich für erweiterte Batterie-Einstellungen
- **Service-Code** hat nicht ausreichende Berechtigungen
- **Modul-Lock** - Einstellungen sind durch Hersteller gesperrt

### **3. API-Verfügbarkeit**

**Mögliche Gründe:**
- **Modul nicht geladen** - Batterie-Modul ist nicht aktiv
- **Kommunikationsfehler** - Verbindung zum Batterie-Modul fehlerhaft
- **Initialisierung** - Einstellungen werden erst nach vollständiger Initialisierung verfügbar

## 🔧 Diagnose-Methoden

### **1. Settings-Check Script**

Verwenden Sie das `check_settings.py` Script:

```bash
python check_settings.py
```

**Erwartete Ausgabe:**
```
--- Kostal Plenticore Settings Check ---
Connecting to 192.168.1.250...
Authenticating...
Successfully authenticated.
Fetching settings...

Found X settings modules.

--- Battery Settings Availability ---
[OK] Battery:MinSocRel is available.
[MISSING] Battery:MinHomeConsumption is NOT available.
```

### **2. API-Debugging**

**Manuelle API-Abfrage:**
```python
# Direkte API-Abfrage
settings = await client.get_settings()
battery_settings = settings.get('devices:local', [])

# Alle verfügbaren Battery-Einstellungen auflisten
for setting in battery_settings:
    if 'Battery' in setting.id:
        print(f"Available: {setting.id} (access: {getattr(setting, 'access', 'unknown')})")
```

### **3. Browser-Debugging**

**Web-Interface prüfen:**
1. Öffnen Sie das Kostal Web-Interface
2. Navigieren Sie zu "Einstellungen" → "Batterie"
3. Prüfen Sie ob die Einstellungen sichtbar sind
4. Notieren Sie die genauen Namen und Berechtigungen

## 📋 Häufige Szenarien

### **Szenario 1: Firmware zu alt**
```
Lösung: Firmware-Update durchführen
Erwartung: Nach Update sind Einstellungen verfügbar
```

### **Szenario 2: Berechtigungen unzureichend**
```
Lösung: Installateur-Service-Code verwenden
Erwartung: Einstellungen werden sichtbar
```

### **Szenario 3: Batterie-Modell nicht unterstützt**
```
Lösung: Batterie-Kompatibilität prüfen
Erwartung: Manche Einstellungen sind bewusst nicht verfügbar
```

### **Szenario 4: Konfiguration deaktiviert**
```
Lösung: Batterie-Management im Web-Interface aktivieren
Erwartung: Einstellungen werden verfügbar
```

## 🔧 Empfohlene Vorgehensweise

### **Schritt 1: Diagnose durchführen**
1. **Settings-Check Script** ausführen
2. **Web-Interface** prüfen
3. **Firmware-Version** notieren

### **Schritt 2: Berechtigungen prüfen**
1. **Installateur-Code** verwenden
2. **Zugriffsrechte** im Web-Interface prüfen
3. **Modul-Status** überprüfen

### **Schritt 3: Kompatibilität prüfen**
1. **Inverter-Modell** notieren
2. **Batterie-Modell** prüfen
3. **Firmware-Version** vergleichen

### **Schritt 4: Alternative Lösungen**
1. **Ähnliche Einstellungen** verwenden (falls vorhanden)
2. **Workarounds** implementieren
3. **Hersteller-Support** kontaktieren

## 🎯 Spezifische Analyse der Entities

### **Battery:MinSocRel**
- **Bedeutung**: Minimaler State of Charge (SoC) der Batterie
- **Typ**: Prozentwert (0-100%)
- **Normalerweise verfügbar**: Ja, bei den meisten modernen Invertern
- **Mögliche Gründe für Fehlen**: 
  - Alte Firmware
  - Batterie-Management deaktiviert
  - Berechtigungsprobleme

### **Battery:MinHomeConsumption**
- **Bedeutung**: Minimaler Home-Verbrauch für Batterie-Entladung
- **Typ**: Leistungswert (Watt)
- **Normalerweise verfügbar**: Nur bei erweiterten Batterie-Management
- **Mögliche Gründe für Fehlen**:
  - Erweitertes Management nicht aktiviert
  - Firmware-Version zu alt
  - Spezielle Konfiguration erforderlich

## 🚀 Sofortmaßnahmen

### **1. Diagnose durchführen**
```bash
# Settings prüfen
python check_settings.py

# Ergebnisse dokumentieren
```

### **2. Web-Interface prüfen**
- Batterie-Einstellungen aufrufen
- Verfügbarkeit prüfen
- Berechtigungen notieren

### **3. Logs analysieren**
- Home Assistant Logs nach Fehlermeldungen durchsuchen
- API-Logs auf Kommunikationsfehler prüfen

### **4. Force-Create Logik testen**
Die bereits implementierte `FORCE_CREATE_KEYS` Logik sollte diese Entities erstellen, auch wenn sie im API nicht sichtbar sind.

## 📋 Fazit

**Die Nichtverfügbarkeit dieser Entities ist meist auf eine der folgenden Ursachen zurückzuführen:**

1. **Firmware/Modell-Kompatibilität** (häufigste Ursache)
2. **Berechtigungsprobleme** (zweithäufigste Ursache)
3. **Konfiguration** (seltenste Ursache)

**Die Diagnose mit dem `check_settings.py` Script wird dringend empfohlen, um die genaue Ursache zu identifizieren.** 🎯
