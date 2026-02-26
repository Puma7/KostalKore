# Kostal Plenticore Integration — Vollständiger Projektbericht

## Projektzusammenfassung

**Zeitraum:** 25.–26. Februar 2026 (eine Session)
**Ausgangsbasis:** v2.4.1 (REST API Integration, 138 Tests)
**Endstand:** v2.11.0 (Modbus + MQTT + Health + Safety + Diagnostics + Degradation, 368 Tests)

### Kennzahlen

| Metrik | Vorher (v2.4.1) | Nachher (v2.11.0) | Delta |
|---|---|---|---|
| Python-Dateien | 12 | 34 | +22 |
| Codezeilen | ~4.500 | ~13.400 | +8.900 |
| Test-Zeilen | ~2.500 | ~7.100 | +4.600 |
| Tests | 138 | 368 | +230 |
| Coverage | 100% | 100% | – |
| mypy Errors | 0 | 0 | – |
| pyright Errors | 28 → 0 | 0 | – |
| Releases | 1 | 20 | +19 |
| HA Entities (Modbus) | 0 | ~55 | +55 |
| Modbus Register | 0 | 117 | +117 |

---

## Release-Chronologie

### v2.5.0 — Modbus TCP + MQTT Proxy Bridge
**Das Fundament: Direkte Modbus-Kommunikation mit dem Inverter**

- **Modbus TCP Client** (`modbus_client.py`): Async-Client mit pymodbus, Endianness-Auto-Detection, Lock-basierte Serialisierung
- **Modbus Register Map** (`modbus_registers.py`): 117 Register aus offizieller Kostal-Dokumentation
- **Modbus Coordinator** (`modbus_coordinator.py`): HA DataUpdateCoordinator mit Dual-Speed-Polling (5s/30s)
- **MQTT Proxy Bridge** (`mqtt_bridge.py`): Publiziert alle Registerwerte via MQTT für evcc/iobroker
- **Battery Control Entities** (`modbus_number.py`): 8 Number-Entities für Ladeleistung, Limits, SoC
- **Options Flow**: GUI-Konfiguration für Modbus (Port, Unit-ID, Endianness, MQTT)
- **Safety Audit**: 5 CRITICAL + 5 HIGH Sicherheitsfixes
- **Proxy Topics**: Vereinfachte MQTT-Topics für evcc (`proxy/pv_power`, `proxy/battery_soc` etc.)

### v2.6.0 — Health Monitoring + Fire Safety
**Überwachung aller Systemparameter + Brandschutz-Frühwarnung**

- **Health Monitor** (`health_monitor.py`): 21 Parameter mit 3-Stufen-Schwellwerten (Info/Warning/Critical)
- **Fire Safety** (`fire_safety.py`): Erkennung von Isolationsfehlern, DC-Lichtbogen-Indikatoren, Batterie-Thermal-Runaway, Controller-Überhitzung
- **10 Health Sensoren**: Score, Level, Isolation, Temperatur, SoH, Error Rate, DC/Phase Imbalance
- **11 Binary Sensoren**: Warnungen für jeden überwachten Parameter
- **6 Fire Safety Entities**: Risk Level, Safety OK, Isolation/Battery/DC Cable Danger

### v2.7.0 — Smart Diagnostics Engine
**Pro-Bereich-Diagnosen mit Handlungsempfehlungen in Klarsprache**

- **5 Diagnose-Sensoren**: DC Solar, AC Netz, Batterie, Wechselrichter, Sicherheit
- Jeder zeigt: `status` (ok/hinweis/warnung/kritisch) + `title` + `detail` + `action`
- Handlungsempfehlungen in Deutsch: "Prüfe MC4-Stecker an String 3"
- INFO-Schwellwerte angehoben um Benachrichtigungs-Spam zu reduzieren

### v2.8.0 — Live Test Tool + Batterie-Chemie + Langlebigkeit
**Vorbereitung für den ersten Live-Test**

- **Live Test Tool** (`live_test.py`): Standalone-Diagnosetool, 100% read-only
- **Batterie-Chemie-Erkennung** (`battery_chemistry.py`): Auto-Detection LFP vs NMC via Register 588
- **Per-Chemie-Schwellwerte**: LFP verträgt mehr Hitze als NMC
- **Longevity Advisor** (`longevity_advisor.py`): Aufstellort-Empfehlungen, Zyklen-Tracking
- **3 Longevity Sensoren**: Batterie/Inverter/PV Langlebigkeit

### v2.8.1 — Diagnostics Button in HA
**"Run Modbus Diagnostics" direkt aus der HA-Oberfläche**

- Liest alle Register und erstellt Report als HA Persistent Notification
- JSON-Report in Entity-Attributen für Entwickler-Analyse
- Kein Terminal nötig

### v2.8.2 — Fix: DC2/DC3 Sensoren
**Erster Live-Test Bug: DC String Count Timeout**

- Problem: REST API Timeout beim String-Count weil Modbus parallel pollt
- Fix: String Count primär aus Modbus Register 34 lesen

### v2.8.3 — Fix: PV Safety bei Nacht
**Erster Live-Test Bug: False Positive "Unsicher" bei Standby**

- Problem: Isolation = 0 Ohm bei Standby → EMERGENCY
- Fix: Safety-Checks bei Inverter State Off/Standby überspringen

### v2.9.0 — REST/Modbus Smart Coexistence
**Performance-Optimierung: Weniger Last auf den Inverter**

- REST API Polling verlangsamt wenn Modbus aktiv (10s→60s, 30s→90s)
- Modbus übernimmt Echtzeit-Daten (5s)
- Keine Timeouts mehr

### v2.9.1 — ARCHITECTURE.md + LEARNINGS.md
**Dokumentation aller Erkenntnisse**

- ARCHITECTURE.md: Konzept für v3.0 Unified Coordinator
- LEARNINGS.md: 31 dokumentierte Erkenntnisse

### v2.9.2 — live_test.py verschoben
**Tool in Custom Components integriert**

### v2.9.3 — Fix: Battery Mgmt Mode
**Live-Test Erkenntnis: Register 1080 = 0 ist normal**

- Register zeigt 0 bis erster Modbus-Befehl gesendet wird
- WebUI kann trotzdem auf "Extern über Protokoll (Modbus TCP)" stehen

### v2.9.4 — Fix: Night Safety mit Batterieentladung
**Live-Test Bug: FeedIn-State nachts (Batterie entlädt)**

- Problem: Inverter ist nachts im State FeedIn (6), nicht Standby (10)
- Fix: Isolation/DC-Checks NUR wenn total_dc_power > 50W
- Stale-Alerts verfallen nachts nach 5min statt 1h

### v2.9.5 — ENTITY_REFERENCE.md
**Vollständige Entity-Dokumentation**

- 274 Zeilen Dokumentation für alle 55+ Entities
- Einheiten, Wertebereiche, Schwellwert-Tabellen

### v2.9.6 — Auto-Probe Modbus Write
**Automatischer Schreib-Test bei Integration-Start**

- Liest Min SoC, schreibt denselben Wert zurück, prüft Register 1080
- Beweist ob externe Steuerung aktiv ist (statt nur Konfiguration zu lesen)

### v2.10.0 — Push-Benachrichtigungen
**Notifications statt nur Logs**

- Zentrales Notification-System (`notifications.py`)
- Modbus Probe Ergebnis als Notification
- Sicherheitswarnungen sofort gepusht
- Diagnose-Änderungen automatisch benachrichtigt
- Automatisches Dismiss wenn Problem behoben

### v2.10.1 — Battery Temperature Sensor
**Fehlender Sensor für Diagramme**

- `Battery Temperature` als eigener Sensor (SensorDeviceClass.TEMPERATURE)
- Graph-fähig für HA History

### v2.10.2 — AC Frequency 2 Nachkommastellen
**49.99 Hz statt 50 Hz**

- Formatter von format_round auf format_float
- suggested_display_precision=2

### v2.10.3 — Autarky Statistics Fix
**HA Core Issue #162072**

- state_class=MEASUREMENT für Autarky/OwnConsumptionRate Day/Month/Year
- Ermöglicht Langzeit-Statistiken (vorher nur 10 Tage)

### v2.10.4 — DcCheck State
**HA Core PR #159679**

- Inverter-State 19 = "DcCheck" zur Map hinzugefügt

### v2.11.0 — Persistente Degradationsüberwachung
**Langzeit-Trend-Erkennung über Wochen und Monate**

- **DegradationTracker** (`degradation_tracker.py`): Tägliche Snapshots, persistent via RestoreEntity
- **8 Parameter getrackt**: Isolation, SoH, Kapazität, Bat-Temp, Ctrl-Temp, DC1/DC2-Peak, Tagesertrag
- **Lineare Regression**: Degradationsrate pro Monat
- **Baseline-Vergleich**: Erste 7 Tage vs aktuell
- **Automatische Alerts**: bei signifikanter Degradation
- **9 neue Sensor-Entities**: Trend + Alerts

---

## Architektur

```
┌─────────────────────────────────────────────────────────────┐
│                    Home Assistant                            │
│                                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────┐  │
│  │ REST API │  │ Modbus   │  │ Health   │  │ Fire      │  │
│  │ Coord.   │  │ Coord.   │  │ Monitor  │  │ Safety    │  │
│  │ (60-90s) │  │ (5-30s)  │  │ (21 Par.)│  │ (5 Checks)│  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └─────┬─────┘  │
│       │              │             │              │         │
│       │         ┌────┴─────────────┴──────────────┴──┐      │
│       │         │     Degradation Tracker            │      │
│       │         │     (365 Tage persistent)          │      │
│       │         └────┬───────────────────────────────┘      │
│       │              │                                      │
│  ┌────┴──────────────┴──────────────────────────────────┐   │
│  │              Entity Layer (~55 Entities)              │   │
│  │  Sensors | Numbers | Switches | Binary | Buttons     │   │
│  └──────────────────────────┬───────────────────────────┘   │
│                             │                               │
│  ┌──────────────────────────┴───────────────────────────┐   │
│  │              Notification System                      │   │
│  │  Persistent Notifications + Diagnose-Engine          │   │
│  └──────────────────────────┬───────────────────────────┘   │
│                             │                               │
│                    MQTT Proxy Bridge                         │
│               (evcc / iobroker / Node-RED)                   │
└─────────────────────────────────────────────────────────────┘
```

---

## Dateien-Übersicht

### Neue Dateien (22 Python-Module)

| Datei | Zeilen | Zweck |
|---|---|---|
| `modbus_client.py` | 586 | Async Modbus TCP Client mit Retry + Fehlerklassifikation |
| `modbus_registers.py` | 332 | 117 Register-Definitionen aus Kostal-Dokumentation |
| `modbus_coordinator.py` | 205 | HA DataUpdateCoordinator für Modbus-Polling |
| `modbus_number.py` | 362 | Battery Control Number-Entities |
| `modbus_button.py` | 240 | Reset + Diagnostics Buttons |
| `mqtt_bridge.py` | 401 | MQTT Proxy mit Rate Limiting + Proxy Topics |
| `health_monitor.py` | 470 | 21-Parameter Health Monitoring |
| `health_sensor.py` | 328 | Health Sensor-Entities |
| `health_binary_sensor.py` | 270 | Health Warning Binary-Sensoren |
| `fire_safety.py` | 396 | Brandschutz-Frühwarnung |
| `fire_safety_entities.py` | 208 | Fire Safety Sensor/Binary-Entities |
| `diagnostics_engine.py` | 408 | Smart Diagnostics pro Bereich |
| `diagnostic_entities.py` | 141 | Diagnose-Sensor-Entities |
| `battery_chemistry.py` | 151 | LFP/NMC Erkennung + Schwellwerte |
| `longevity_advisor.py` | 211 | Langlebigkeits-Empfehlungen |
| `longevity_entities.py` | 104 | Longevity Sensor-Entities |
| `degradation_tracker.py` | 330 | Persistente Degradations-Überwachung |
| `degradation_entities.py` | 147 | Degradation Sensor-Entities mit RestoreEntity |
| `notifications.py` | 133 | Zentrales Push-Notification-System |
| `button.py` | 39 | Button-Plattform |
| `binary_sensor.py` | 47 | Binary-Sensor-Plattform |
| `live_test.py` | 349 | Standalone Diagnose-Tool |

### Neue Test-Dateien (10)

| Datei | Tests | Zweck |
|---|---|---|
| `test_modbus_client.py` | 46 | Encoding/Decoding, Verbindung, Fehlerklassifikation |
| `test_modbus_registers.py` | 20 | Register-Map Integrität |
| `test_mqtt_bridge.py` | 26 | MQTT Pub/Sub, Rate Limiting, Proxy Commands |
| `test_modbus_integration.py` | 12 | Options Flow, Modbus Init/Unload |
| `test_health_monitor.py` | 34 | Parameter-Tracking, Schwellwerte, Scoring |
| `test_fire_safety.py` | 24 | Brandschutz-Checks, Nacht-Szenarien |
| `test_diagnostics_engine.py` | 22 | Per-Bereich-Diagnosen |
| `test_battery_chemistry.py` | 15 | Chemie-Erkennung, Schwellwerte |
| `test_longevity_advisor.py` | 11 | Langlebigkeits-Tipps |
| `test_degradation_tracker.py` | 18 | Persistenz, Regression, Alerts |

### Dokumentation (6 Dateien)

| Datei | Inhalt |
|---|---|
| `ENTITY_REFERENCE.md` | Vollständige Dokumentation aller ~55 Entities |
| `ARCHITECTURE.md` | Konzept für v3.0 Unified Coordinator |
| `LEARNINGS.md` | 31 Erkenntnisse aus dem Projekt |
| `PROXY_SETUP.md` | evcc/iobroker MQTT-Konfiguration |
| `PROJECT_REPORT.md` | Dieser Bericht |
| `CHANGELOG.md` | Vollständige Release-History |

---

## Erkenntnisse aus dem Live-Test (G3 20L)

### Hardware-Fakten bestätigt
- **Inverter**: PLENTICORE L G3, 20kW, FW 3.06.01.20869
- **Batterie**: Pylontech Force H (LFP), 35.7 kWh, 47 Zyklen
- **PV**: 2 aktive Strings (DC3 physisch vorhanden aber nicht belegt)
- **Modbus**: Port 1502, Unit-ID 71, Little-Endian (CDAB)
- **Isolation**: 65.5 MΩ (hervorragend)
- **94 Register erfolgreich gelesen, 0 Fehler**

### Bugs durch Live-Test entdeckt und gefixt
1. **DC2/DC3 nicht verfügbar**: REST API Timeout wegen Modbus-Konkurrenz → Fix: Modbus Register 34
2. **PV Safety "Unsicher" bei Nacht**: Inverter in FeedIn (6) statt Standby (10) → Fix: DC Power Check
3. **Battery Mgmt Mode Warning**: Register 1080=0 ist normal → Fix: Auto-Probe
4. **Battery Temperature Sensor fehlte**: Nicht als Entity exponiert → Fix: Hinzugefügt
5. **AC Frequency gerundet**: 50 Hz statt 49.99 Hz → Fix: format_float

### Sicherheitsmaßnahmen implementiert
- NaN/Infinity-Schutz an 3 Stellen (Entity, Coordinator, Client)
- Admin-Register (modbus_enable, unit_id, byte_order) MQTT-geschützt
- Rate Limiting: max 1 Write/Register/Sekunde
- Read-Back-Verification nach kritischen Writes
- Active Power Setpoint min=1 (nicht 0, das den Inverter deaktivieren würde)
- Min SoC Floor bei 5% (Tiefentladungsschutz)
- G3 Cyclic Keepalive für Battery Limits
- 5 Retries bei Busy mit 2s Backoff
- Strike-System statt permanentem Register-Skip

---

## Nächste Schritte (v3.0 Konzept)

Dokumentiert in `ARCHITECTURE.md`:
1. **Unified Data Coordinator**: Ein Koordinator statt vier separate
2. **Request Scheduler**: Serialisiert alle Anfragen an den Inverter
3. **Entity-Abstraktionsschicht**: Automatische Quellenwahl (Modbus/REST)
4. **Saisonbereinigte Degradation**: Winter/Sommer-Unterschiede berücksichtigen
5. **Degradation-Dashboard**: HA Lovelace Card mit Trend-Graphen
