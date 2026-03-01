# Entity-Referenz: KOSTAL KORE (Kostal Plenticore)

Vollständige Dokumentation aller Entities, Werte, Einheiten und deren Bedeutung.
Hinweis: Verfügbarkeit einzelner Entities hängt von Inverter-Generation/Firmware sowie Zugriffsprofil (User/Installer) ab.

---

## 1. Modbus-Register (Monitoring)

### 1.1 Geräte-Informationen

| Entity | Register | Einheit | Beschreibung |
|---|---|---|---|
| `modbus_enable` | 2 | – | Modbus-Schnittstelle aktiviert (True/False). Muss im Inverter-WebUI eingeschaltet sein. |
| `unit_id` | 4 | – | Modbus-Geräteadresse (Standard: 71). Wird für die Kommunikation benötigt. |
| `byte_order` | 5 | – | Byte-Reihenfolge: 0 = Little-Endian (CDAB), 1 = Big-Endian (ABCD). Beeinflusst wie Float-Werte interpretiert werden. |
| `article_number` | 6 | – | Artikelnummer des Wechselrichters (z.B. "10541199"). |
| `serial_number` | 14 | – | Seriennummer des Wechselrichters. Eindeutige Identifikation. |
| `num_bidirectional` | 30 | – | Anzahl der bidirektionalen Wandler. Bei Hybrid-Invertern mit Batterie: 1. |
| `num_ac_phases` | 32 | – | Anzahl der AC-Phasen. Dreiphasig: 3. |
| `num_pv_strings` | 34 | – | Anzahl der PV-DC-Eingänge. Bestimmt wie viele DC-String-Sensoren erstellt werden. |
| `hw_version` | 36 | – | Hardware-Versionsnummer des Wechselrichters. |
| `sw_version_mc` | 38 | – | Software-Version des Hauptcontrollers (MC). |
| `sw_version_ioc` | 46 | – | Software-Version des I/O-Controllers (IOC). |
| `power_id` | 54 | – | Power-ID des Wechselrichters (interne Kennung). |
| `inverter_state` | 56 | – | Aktueller Betriebszustand: 0=Aus, 1=Init, 2=IsoMeas, 3=GridCheck, 4=StartUp, 6=Einspeisen, 7=Gedrosselt, 8=Extern Aus, 9=Update, 10=Standby, 11=GridSync, 12=GridPreCheck, 13=GridSwitchOff, 14=Überhitzung, 15=Shutdown, 16=DC-Spannung ungültig, 17=Notabschaltung |
| `sw_version` | 58 | – | Gesamt-Software-Version (UI/SW, z.B. "3.06.01.20869"). |
| `product_name` | 768 | – | Produktname (z.B. "PLENTICORE"). |
| `power_class` | 800 | – | Leistungsklasse (z.B. "L G3" für 20kW Generation 3). |
| `worktime` | 144 | s | Gesamte Betriebszeit des Wechselrichters in Sekunden seit Erstinbetriebnahme. |
| `inverter_max_power` | 531 | W | Maximale AC-Ausgangsleistung des Wechselrichters (z.B. 20000 für 20kW). Wird für dynamische Leistungsgrenzen verwendet. |

### 1.2 Leistungsmessung

| Entity | Register | Einheit | Beschreibung |
|---|---|---|---|
| `controller_temp` | 98 | °C | Temperatur der Controller-Platine (PCB). Normal: 30-60°C. Warnung ab 70°C. Inverter drosselt ab ca. 75°C automatisch. |
| `total_dc_power` | 100 | W | Gesamte DC-Eingangsleistung aller PV-Strings zusammen. Tagsüber positiv, nachts 0 oder leicht negativ. |
| `em_state` | 104 | – | Zustand des Energiemanagers: 0x00=Idle, 0x02=Notladung, 0x08=Wintermodus Stufe 1, 0x10=Wintermodus Stufe 2. |
| `home_from_battery` | 106 | W | Aktuelle Leistung die aus der Batterie ins Haus fließt. Nur bei Batterieentladung > 0. |
| `home_from_grid` | 108 | W | Aktuelle Leistung die aus dem Netz ins Haus fließt (Netzbezug). 0 bei Autarkie. |
| `home_from_pv` | 116 | W | Aktuelle Leistung die direkt von den PV-Modulen ins Haus fließt (Eigenverbrauch). |
| `isolation_resistance` | 120 | Ω | Isolationswiderstand zwischen DC-Seite und Erde. Sicher: >500kΩ. Kritisch: <100kΩ. Nachts/Standby: 0 oder Maximalwert (ungültig). Sinkender Trend kann auf Kabelbeschädigung, Feuchtigkeit oder Tierbiss hindeuten. |
| `power_limit_evu` | 122 | % | Leistungsbegrenzung durch den Netzbetreiber (EVU). 100% = keine Begrenzung. <100% = Abregelung aktiv. |
| `home_consumption_rate` | 124 | % | Eigenverbrauchsquote: Anteil der PV-Produktion der direkt verbraucht wird (nicht eingespeist). |
| `cos_phi` | 150 | – | Leistungsfaktor (cos φ) am Wechselrichter-Ausgang. 1.0 = rein Wirkleistung. <1.0 = Blindleistungsanteil. |
| `grid_frequency` | 152 | Hz | Netzfrequenz. Nennwert: 50.000 Hz. Normal: 49.8-50.2 Hz. Abweichung >±0.5 Hz deutet auf Netzstörung hin. |
| `total_ac_power` | 172 | W | Gesamte AC-Wirkleistung am Wechselrichter-Ausgang. Positiv = Einspeisung, negativ = Bezug. |
| `total_ac_reactive` | 174 | Var | Gesamte AC-Blindleistung. Relevant für Netzstabilität und cos-φ-Regelung. |
| `total_ac_apparent` | 178 | VA | Gesamte AC-Scheinleistung. Vektorielle Summe aus Wirk- und Blindleistung. |
| `inverter_gen_power` | 575 | W | Aktuelle Erzeugungsleistung des Wechselrichters (AC-seitig). |
| `total_dc_power_all` | 1066 | W | Summe der DC-Leistung aller PV-Eingänge (redundant zu total_dc_power). |

### 1.3 AC-Phasen (3-phasig)

| Entity | Register | Einheit | Beschreibung |
|---|---|---|---|
| `phase1_current` | 154 | A | Strom auf Phase L1. Normalbereich: 0-30A je nach Inverter-Leistung. |
| `phase1_power` | 156 | W | Wirkleistung auf Phase L1. Sollte bei symmetrischer Last ca. 1/3 der Gesamtleistung sein. |
| `phase1_voltage` | 158 | V | Spannung auf Phase L1. Nennwert: 230V. Normbereich nach EN 50160: 207-253V. |
| `phase2_current` | 160 | A | Strom auf Phase L2. |
| `phase2_power` | 162 | W | Wirkleistung auf Phase L2. |
| `phase2_voltage` | 164 | V | Spannung auf Phase L2. |
| `phase3_current` | 166 | A | Strom auf Phase L3. |
| `phase3_power` | 168 | W | Wirkleistung auf Phase L3. |
| `phase3_voltage` | 170 | V | Spannung auf Phase L3. |

### 1.4 DC-Strings (PV-Eingänge)

| Entity | Register | Einheit | Beschreibung |
|---|---|---|---|
| `dc1_current` | 258 | A | Strom von PV-String 1. Abhängig von Modulanzahl und Einstrahlung. |
| `dc1_power` | 260 | W | Leistung von PV-String 1. Sollte bei gleicher Modulbelegung ähnlich wie DC2 sein. Große Abweichung (>30%) deutet auf Verschattung, Verschmutzung oder Kabelfehler hin. |
| `dc1_voltage` | 266 | V | Spannung von PV-String 1. Abhängig von Modulanzahl, Temperatur und Einstrahlung. Nachts: Leerlaufspannung oder nahe 0. |
| `dc2_current` | 268 | A | Strom von PV-String 2. |
| `dc2_power` | 270 | W | Leistung von PV-String 2. |
| `dc2_voltage` | 276 | V | Spannung von PV-String 2. |
| `dc3_current` | 278 | A | Strom von PV-String 3. Nur wenn 3. Eingang belegt. |
| `dc3_power` | 280 | W | Leistung von PV-String 3. |
| `dc3_voltage` | 286 | V | Spannung von PV-String 3. |

### 1.5 Batterie

| Entity | Register | Einheit | Beschreibung |
|---|---|---|---|
| `battery_soc` | 514 | % | Ladezustand (State of Charge) der Batterie. 0% = leer, 100% = voll. |
| `battery_state_of_charge` | 210 | % | Ladezustand als Float-Wert (genauer als battery_soc). |
| `battery_temperature` | 214 | °C | Temperatur der Batterie. LFP optimal: <30°C. NMC optimal: <25°C. Warnung ab 45°C. Kritisch ab 55°C. Hohe Temperaturen beschleunigen die Alterung erheblich. |
| `battery_voltage` | 216 | V | Batteriespannung. Abhängig von Chemie und SoC. Bei Pyontech Force H typisch: 600-800V. |
| `battery_charge_current` | 190 | A | Ladestrom der Batterie. Positiv = Laden. |
| `battery_actual_current` | 200 | A | Aktueller Lade-/Entladestrom. Positiv = Laden, negativ = Entladen. |
| `battery_cycles` | 194 | – | Anzahl der vollständigen Lade-/Entladezyklen seit Erstinbetriebnahme. LFP-Batterien: >6000 Zyklen Lebensdauer. NMC: >3000 Zyklen. |
| `battery_gross_capacity` | 512 | Ah | Brutto-Kapazität der Batterie. **Hinweis**: Kann bei einigen Batterie-Typen (Pyontech) einen ungültigen Wert liefern. |
| `battery_work_capacity` | 1068 | Wh | Nutzbare Arbeitskapazität der Batterie in Wattstunden. Zuverlässiger als battery_gross_capacity. |
| `battery_type` | 588 | – | Batterietyp-Code: 0x0004=BYD, 0x0040=LG, 0x0200=Pyontech, 0x2000=VARTA, 0x1000=Dyness, 0x0008=BMZ, 0x0010=AXIstorage. |
| `battery_serial` | 1070 | – | Seriennummer der Batterie. |
| `battery_max_charge_hw` | 1076 | W | Maximale Ladeleistung die die Batterie-Hardware erlaubt. Vom BMS vorgegeben. |
| `battery_max_discharge_hw` | 1078 | W | Maximale Entladeleistung die die Batterie-Hardware erlaubt. |
| `battery_mgmt_mode` | 1080 | – | Batteriesteuerungsmodus: 0x00=Keine externe Steuerung (oder noch kein Modbus-Befehl empfangen), 0x01=Extern via Digital I/O, 0x02=Extern via MODBUS (aktiv). |
| `battery_cd_power` | 582 | W | Aktuelle Batterie-Lade/Entladeleistung. Positiv = Entladung, negativ = Ladung. |
| `sensor_type` | 1082 | – | Installierter Energiezähler-Typ: 0x00=SDM 630, 0x01=B-Control EM-300, 0x03=KOSTAL Smart Energy Meter (KSEM), 0xFF=Kein Sensor. |

### 1.6 Energie-Zähler (kumulativ)

| Entity | Register | Einheit | Beschreibung |
|---|---|---|---|
| `total_yield` | 320 | Wh | Gesamte erzeugte Energie seit Erstinbetriebnahme (Lifetime). |
| `daily_yield` | 322 | Wh | Erzeugte Energie heute (wird um Mitternacht zurückgesetzt). |
| `monthly_yield` | 326 | Wh | Erzeugte Energie diesen Monat. |
| `yearly_yield` | 324 | Wh | Erzeugte Energie dieses Jahr. |
| `generation_energy` | 577 | Wh | Gesamte Erzeugungsenergie (AC-seitig). |
| `total_home_battery` | 110 | Wh | Gesamter Hausverbrauch aus Batterie (kumulativ). |
| `total_home_grid` | 112 | Wh | Gesamter Hausverbrauch aus Netz (kumulativ). |
| `total_home_pv` | 114 | Wh | Gesamter Hausverbrauch aus PV (kumulativ). |
| `total_home_consumption` | 118 | Wh | Gesamter Hausverbrauch aus allen Quellen (Batterie + Netz + PV). |
| `total_dc_charge` | 1046 | Wh | Gesamte DC-seitige Ladeenergie (PV → Batterie). |
| `total_dc_discharge` | 1048 | Wh | Gesamte DC-seitige Entladeenergie (Batterie → Inverter). |
| `total_ac_charge` | 1050 | Wh | Gesamte AC-seitige Ladeenergie (Netz/PV → Batterie, AC-gemessen). |
| `total_ac_discharge` | 1052 | Wh | Gesamte AC-seitige Entladeenergie (Batterie → Netz). |
| `total_ac_charge_grid` | 1054 | Wh | Gesamte Ladeenergie aus dem Netz (Netzladung). |
| `total_dc_pv_energy` | 1056 | Wh | Gesamte DC-Energie aller PV-Eingänge (Summe PV1+PV2+PV3). |
| `total_dc_pv1` | 1058 | Wh | Gesamte DC-Energie von PV-String 1. |
| `total_dc_pv2` | 1060 | Wh | Gesamte DC-Energie von PV-String 2. |
| `total_dc_pv3` | 1062 | Wh | Gesamte DC-Energie von PV-String 3. |
| `total_ac_to_grid` | 1064 | Wh | Gesamte eingespeiste Energie ins Netz (kumulativ). |

### 1.7 Energiezähler (Powermeter / KSEM)

| Entity | Register | Einheit | Beschreibung |
|---|---|---|---|
| `pm_total_active` | 252 | W | Gesamte Wirkleistung am Energiezähler. Positiv = Netzbezug, negativ = Einspeisung. Bei Sensorposition 2 (Netzanschluss). |
| `pm_total_reactive` | 254 | Var | Gesamte Blindleistung am Energiezähler. |
| `pm_total_apparent` | 256 | VA | Gesamte Scheinleistung am Energiezähler. |
| `pm_cos_phi` | 218 | – | Leistungsfaktor am Energiezähler. |
| `pm_frequency` | 220 | Hz | Netzfrequenz gemessen am Energiezähler. |

---

## 2. Modbus-Steuerung (Schreibbare Register)

### 2.1 Batterie-Steuerung

| Entity | Register | Einheit | Bereich | Beschreibung |
|---|---|---|---|---|
| **Battery Charge Power (Modbus)** | 1034 | W | -MaxPower..+MaxPower | DC-seitige Batterie-Ladeleistung. **Negativ = Laden**, positiv = Entladen. Beispiel: -5000 = Laden mit 5kW. Erfordert "Extern über Protokoll (Modbus TCP)" im Inverter-WebUI. |
| **Battery Max Charge Limit (Modbus)** | 1038 | W | 0..MaxPower | Maximale Ladeleistung der Batterie begrenzen. Wert wird vom BMS-Limit (battery_max_charge_hw) nach oben begrenzt. |
| **Battery Max Discharge Limit (Modbus)** | 1040 | W | 0..MaxPower | Maximale Entladeleistung begrenzen. 0 = Entladung deaktiviert. |
| **Battery Min SoC (Modbus)** | 1042 | % | 5..100 | Minimaler Ladezustand. Batterie wird nicht unter diesen Wert entladen. 5% Minimum zum Schutz vor Tiefentladung. |
| **Battery Max SoC (Modbus)** | 1044 | % | 5..100 | Maximaler Ladezustand. Batterie wird nicht über diesen Wert geladen. |
| **Active Power Setpoint (Modbus)** | 533 | % | 1..100 | Wirkleistungsbegrenzung des Wechselrichters in Prozent. 100% = volle Leistung. **Minimum 1%** (0 würde Einspeisung deaktivieren). |
| **G3 Max Charge Power (Modbus)** | 1280 | W | 0..MaxPower | G3-spezifisch: Maximale Batterie-Ladeleistung. **Muss zyklisch geschrieben werden** (automatischer Keepalive alle fallback_time/2), sonst aktiviert der Inverter Fallback-Limits. |
| **G3 Max Discharge Power (Modbus)** | 1282 | W | 0..MaxPower | G3-spezifisch: Maximale Batterie-Entladeleistung. Gleiche zyklische Schreib-Anforderung wie G3 Max Charge. |

*MaxPower = dynamisch aus Register 531 gelesen (z.B. 20000W für PLENTICORE L G3)*

### 2.2 Netzregelung

| Entity | Register | Einheit | Bereich | Beschreibung |
|---|---|---|---|---|
| `reactive_power_setpoint` | 583 | % | -100..+100 | Blindleistungs-Sollwert in Prozent der Nennleistung. 0 = keine Blindleistung. Positiv = kapazitiv, negativ = induktiv. |
| `delta_cos_phi` | 585 | – | -32768..+32767 | Delta cos φ Sollwert. Negativ = untererregt, positiv = übererregt. Wertbereich -1.0..+1.0 abgebildet auf -32768..+32767. |
| `low_prio_active_power` | 832 | W | 0..65535 | Niedrig-priorisierter Wirkleistungs-Sollwert. Muss mit dem Scale Factor (Register 833) skaliert werden. |

---

## 3. Health Monitoring Entities

### 3.1 Gesundheits-Sensoren

| Entity | Einheit | Beschreibung |
|---|---|---|
| **Inverter Health Score** | % | Gesamt-Gesundheitsbewertung (0-100%). 100% = alles optimal. Sinkt bei Warnungen/Fehlern. Attribute: overall_health, error_rate, communication_reliability. |
| **Inverter Health Level** | – | Gesundheitsstufe als Text: "good", "info", "warning", "critical", "unknown". Icon ändert sich je nach Stufe (Schild grün/gelb/rot). |
| **Isolation Resistance (Health)** | Ω | Isolationswiderstand mit Trendanalyse. Attribute: trend (rising/stable/falling), min, max, avg. Sinkender Trend = mögliche Kabeldegradation. |
| **Controller Temperature (Health)** | °C | Controller-Temperatur mit Peak-Tracking. Attribut: peak (höchster gemessener Wert). Für Langzeit-Überwachung der Inverter-Belüftung. |
| **Battery Health (SoH Trend)** | % | Battery State of Health mit Degradations-Tracking. Attribute: soh_trend, cycles_current, cycles_total. Fallender Trend = Batterie altert. |
| **Error Rate (per hour)** | – | Fehlerrate pro Stunde. Attribute: recent_events (letzte 5 Events mit Kategorie und Level). >5/h = Kommunikationsproblem. |
| **Modbus Communication Reliability** | % | Erfolgsrate der Modbus-Abfragen. 100% = perfekt. <95% = Netzwerk-/Verbindungsproblem. |
| **DC String Imbalance** | % | Leistungsunterschied zwischen den DC-Strings. 0% = perfekt gleichmäßig. >30% = mögliche Verschattung, Verschmutzung oder Kabelfehler. Attribute: dc1/dc2/dc3_power. |
| **Phase Voltage Imbalance** | % | Spannungsunterschied zwischen den AC-Phasen. 0% = symmetrisch. >3% = mögliches Netzproblem. Attribute: phase1/2/3_voltage. |
| **Inverter State Changes** | – | Zähler für Zustandswechsel des Inverters (z.B. FeedIn → Standby → FeedIn). Häufige Wechsel deuten auf Instabilität hin. |

### 3.2 Warnungs-Binary-Sensoren

| Entity | ON wenn | Automatisierungsbeispiel |
|---|---|---|
| **Isolation Resistance Warning** | Isolationswiderstand < 500kΩ | Push-Nachricht: "Isolationswiderstand niedrig - DC-Kabel prüfen" |
| **Controller Overheat Warning** | Controller-Temp > 70°C | Benachrichtigung: "Inverter-Belüftung prüfen" |
| **Battery Health Warning** | Battery SoH < 80% | Langzeit-Alarm: "Batterie-Kapazität nachlassend" |
| **Battery Temperature Warning** | Batterie-Temp > 45°C | "Batterie-Belüftung prüfen" |
| **Grid Frequency Warning** | Frequenz ±0.5 Hz von 50 Hz | "Netzstörung erkannt" |
| **Phase 1/2/3 Voltage Warning** | Spannung < 195V oder > 255V | "Netzspannung Phase X auffällig" |
| **DC String Imbalance Warning** | Imbalance > 30% | "PV-Module/Kabel prüfen" |
| **High Error Rate Warning** | > 5 Fehler/Stunde | "Kommunikationsproblem prüfen" |
| **Active Inverter Errors** | Aktive Fehler > 0 | "Inverter-Fehlerspeicher prüfen" |

---

## 4. Brandschutz-Entities

| Entity | Typ | Beschreibung |
|---|---|---|
| **Fire Risk Level** | Sensor | Aktuelle Risikostufe: safe / monitor / elevated / high / emergency. Attribute: active_alerts mit Details und Handlungsempfehlungen. |
| **Active Safety Alerts** | Sensor | Anzahl aktiver Sicherheitswarnungen in der letzten Stunde. |
| **PV System Safety** | Binary Sensor | ON = System sicher, OFF = Sicherheitswarnung aktiv. Device Class: SAFETY. Für Automationen. Nachts (keine PV-Produktion): immer ON. |
| **Isolation Fault Danger** | Binary Sensor | ON wenn Isolationsfehler mit Risiko HIGH oder EMERGENCY erkannt. |
| **Battery Fire Risk** | Binary Sensor | ON wenn Batterie-Temperatur kritisch oder Spannungsanomalie bei hoher Temperatur erkannt. |
| **DC Cable Danger** | Binary Sensor | ON wenn DC-String-Daten auf möglichen Lichtbogen oder Kabelfehler hindeuten. |

---

## 5. Diagnose-Entities

| Entity | Werte | Beschreibung |
|---|---|---|
| **Diagnose: DC Solaranlage** | ok / hinweis / warnung / kritisch | Bewertet PV-Strings, MC4-Stecker, Kabelzustand. Attribut `action` enthält konkrete Handlungsempfehlung in Klartext. |
| **Diagnose: AC Netzanbindung** | ok / hinweis / warnung / kritisch | Bewertet Phasenspannung, Frequenz, Leistungsfaktor. Empfiehlt ggf. Netzversorger zu kontaktieren. |
| **Diagnose: Batterie** | ok / hinweis / warnung / kritisch | Bewertet Temperatur, SoH, Zyklen. Bei Kritisch: Evakuierungshinweis. Bei Hinweis: Aufstellort-Empfehlung. |
| **Diagnose: Wechselrichter** | ok / hinweis / warnung / kritisch | Bewertet Controller-Temperatur, Fehlerstatus, Kommunikation. Empfiehlt Belüftungsprüfung. |
| **Diagnose: Sicherheit** | ok / hinweis / warnung / kritisch | Gesamtsicherheitsbewertung: Isolation, Brandrisiko, Kabelfehler. Bei Kritisch: sofortiges Handeln empfohlen. |

---

## 6. Langlebigkeits-Entities

| Entity | Beschreibung |
|---|---|
| **Batterie Langlebigkeit** | Temperatur-Bewertung basierend auf erkannter Batteriechemie (LFP/NMC). Zeigt "Optimal", "Akzeptabel", "Erhöht" oder "Kritisch". Attribut `tips` enthält konkrete Empfehlungen zur Lebensdauerverlängerung. Attribut `chemistry` zeigt erkannte Chemie. |
| **Wechselrichter Langlebigkeit** | Bewertet Durchschnitts- und Spitzentemperatur des Controllers. Gibt Empfehlungen zu Belüftung und Montageort. |
| **PV-Anlage Langlebigkeit** | Überwacht DC-String-Balance und Isolationstrend. Gibt Empfehlungen zu Modulreinigung und Kabelwartung. |

---

## 7. Verwaltungs-Entities

| Entity | Typ | Beschreibung |
|---|---|---|
| **Import Legacy Plenticore Data** | Button | Schritt 1 der Legacy-Migration: Importiert alte Entry-Daten und migriert Registry-Bindings, ohne die Legacy-Entry sofort zu entfernen. |
| **Finalize Legacy Cleanup** | Button | Schritt 2 der Legacy-Migration: Entfernt verbleibende Legacy-Entities/-Geräteverknüpfungen und löscht anschließend die alte Legacy-Entry. |
| **Reset Modbus Registers** | Button | Setzt alle temporär deaktivierten Register zurück. Nach Firmware-Update oder Inverterwechsel drücken, damit neue Register erkannt werden. |
| **Run Modbus Diagnostics** | Button | Liest alle Register und erstellt einen ausführlichen Diagnosebericht als HA Persistent Notification. 100% read-only, kein Schreibzugriff. |

---

## Schwellwert-Referenz

### Temperatur-Schwellwerte nach Batteriechemie

| Stufe | LFP (BYD, Pyontech, VARTA) | NMC (LG, BMZ) | Unbekannt |
|---|---|---|---|
| **Optimal** | < 30°C | < 25°C | < 25°C |
| **Akzeptabel** | < 40°C | < 35°C | < 35°C |
| **Warnung** | > 50°C | > 45°C | > 45°C |
| **Kritisch** | > 60°C | > 55°C | > 55°C |

### Inverter-Temperatur

| Stufe | Controller PCB |
|---|---|
| **Info** | > 62°C |
| **Warnung** | > 70°C |
| **Kritisch** | > 80°C |

### Netz-Schwellwerte

| Parameter | Info | Warnung | Kritisch |
|---|---|---|---|
| Frequenz | ±0.3 Hz | ±0.5 Hz | ±1.0 Hz |
| Phasenspannung | 207-253V | 195-255V | 185-265V |

### Batterie-Lebensdauer

| Parameter | Info | Warnung | Kritisch |
|---|---|---|---|
| SoH | < 90% | < 80% | < 60% |
| Zyklen (LFP) | > 4000 | > 6000 | > 8000 |
| Zyklen (NMC) | > 3000 | > 4000 | > 6000 |

---

*Last Updated: 2026-03-01*
