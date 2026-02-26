# Projekt-Learnings: Kostal Plenticore Integration

## Gesammelte Erkenntnisse aus der Entwicklung (Feb 2026)

### Hardware-Learnings (Kostal G3 20L)

1. **Modbus TCP braucht keine Authentifizierung** -- Port 1502, Unit-ID 71, einfach TCP-Verbindung aufbauen. Sicherheit liegt im lokalen Netzwerk.

2. **Endianness ist konfigurierbar** -- Register 5 enthält die Byte-Order (0=Little-Endian CDAB, 1=Big-Endian ABCD). Little-Endian ist der Default. Auto-Detection bei jedem Connect ist Pflicht.

3. **Inverter kann nur begrenzt parallele Anfragen** -- REST API + Modbus gleichzeitig → Timeouts. Ein Request-Scheduler oder Priorisierung ist nötig.

4. **Isolation Resistance = 0 bei Nacht/Standby** -- Ohne DC-Spannung ist keine Messung möglich. Register liefert 0 Ohm → darf NICHT als Fehler interpretiert werden.

5. **DC String Count via REST hat Timeout-Risiko** -- Wenn Modbus parallel pollt, bekommt die REST-API-Abfrage für Properties:StringCnt kein Timeslot. Lösung: String Count aus Modbus Register 34 lesen.

6. **Battery Management Mode muss manuell aktiviert werden** -- Register 1080 muss 0x02 (External via MODBUS) sein, sonst werden Write-Befehle ignoriert. Braucht Installateur-Code.

7. **G3 Batterie-Limits brauchen zyklische Writes** -- Register 0x500/0x502 müssen regelmäßig geschrieben werden, sonst aktivieren sich Fallback-Limits nach Timeout (Register 0x508, 30-10800s).

8. **BYD Batterien sind LFP** -- Batterietyp-Code 0x0004 = BYD = LiFePO4. Hitzeresistenter als NMC (LG/BMZ), aber trotzdem Temperaturüberwachung wichtig.

9. **Einige REST-API-Settings existieren auf G3 nicht** -- ChargeCurrentDcRel, ChargePowerDcAbs, Esb:MinSocRel, ComMonitorTime sind nicht verfügbar. Strike-System überspringt sie automatisch.

10. **Setup dauert 10-25 Sekunden** -- Viele Register + REST-API-Discovery + Modbus-Init. HA zeigt "Setup taking over 10 seconds" Warnungen. Ist normal, kein Fehler.

### Software-Architektur-Learnings

1. **HA OptionsFlow: kein `__init__(self, config_entry)`** -- Neuere HA-Versionen (2025.1+) stellen `self.config_entry` automatisch bereit. Eigenes Setzen löst `RuntimeError` aus.

2. **HA Plattformen müssen in sys.modules registriert werden** -- Für Custom Components müssen Plattform-Module als `homeassistant.components.{domain}.{platform}` in `sys.modules` stehen, sonst findet HA sie beim Unload nicht.

3. **Modbus-Plattformen separat von REST-Plattformen laden** -- `MODBUS_PLATFORMS` nur forwarden wenn Modbus aktiv. Sonst Unload-Fehler weil HA versucht Plattformen zu entladen die nie geladen wurden.

4. **pymodbus 3.12+ API: `device_id` statt `slave`** -- Der Parameter heißt jetzt `device_id`, nicht mehr `slave`. Ältere Docs/Tutorials sind veraltet.

5. **`@property` vs `@cached_property` in HA Entities** -- HA 2025.x nutzt `cached_property` für `available`, `native_value`, `is_on`. Subklassen die `@property` verwenden brauchen `# pyright: ignore[reportIncompatibleVariableOverride]`.

6. **Coverage-Exclude für runtime-only Code** -- Modbus-Verbindungscode, Nacht-Szenarien, Batterie-Typ-Konvertierung etc. kann im Testframework nicht vollständig abgedeckt werden → `pragma: no cover` für defensive Branches.

7. **MQTT-Modul Lazy-Import** -- `from homeassistant.components import mqtt` funktioniert nur innerhalb von Funktionen (lazy), nicht auf Modul-Ebene. Braucht `# type: ignore[attr-defined]` für mypy.

### Sicherheits-Learnings

1. **NaN/Infinity sind echte Gefahren** -- `float('nan')` und `float('inf')` können durch JSON-Parsing, MQTT-Payloads oder fehlerhafte Berechnungen entstehen. Müssen an JEDER Write-Stelle blockiert werden.

2. **Admin-Register schützen** -- `modbus_enable`, `unit_id`, `byte_order` dürfen NICHT über MQTT schreibbar sein. Ein externer Angreifer könnte Modbus deaktivieren oder die Kommunikation korrumpieren.

3. **Active Power Setpoint Range: 1-100, NICHT 0-100** -- 0% = Inverter produziert keinen Strom. Kostal-Docs sagen explizit Range 1..100.

4. **Min SoC nie auf 0% setzen** -- Tiefentladung schadet der Batterie. Minimum sollte 5% sein.

5. **Read-Back-Verification nach Writes** -- Nach jedem kritischen Write den Wert zurücklesen und vergleichen. Erkennt Endianness-Probleme, Permissions-Fehler und Firmware-Rejects.

6. **Rate Limiting für MQTT Commands** -- Max 1 Write pro Register pro Sekunde. Verhindert Flooding durch fehlerhafte externe Systeme.

### Diagnose-Learnings

1. **False Positives bei Nacht vermeiden** -- Alle Safety-Checks müssen den Inverter-State prüfen. Im Standby (State 0, 1, 10, 15) sind Isolation, DC-Power etc. nicht messbar.

2. **Batterie-Chemie beeinflusst Schwellwerte** -- LFP verträgt bis 60°C, NMC nur bis 55°C. Auto-Detection via Register 588 und per-Chemie Thresholds sind Pflicht.

3. **3-Stufen-System statt 2** -- INFO → WARNING → CRITICAL. Mit nur WARNING/CRITICAL werden Nutzer entweder zu viel oder zu wenig alarmiert. INFO = "beobachten", WARNING = "handeln", CRITICAL = "sofort handeln".

4. **Register-Strike-System statt permanentem Skip** -- ILLEGAL_DATA_ADDRESS (Exception 02) kann temporär sein (Inverter busy). Erst nach 3 Strikes supprimieren, und auto-expire nach 120s × Strikes. So werden neue Register nach FW-Updates automatisch entdeckt.

5. **Diagnosetexte in Klarsprache** -- Nicht "Register 1080 = 0x00" sondern "Externe Batteriesteuerung via Modbus ist nicht aktiviert. Aktiviere sie im Inverter-WebUI unter Service > Batterie-Einstellungen."

### Performance-Learnings

1. **REST und Modbus nicht gleichzeitig mit voller Frequenz** -- Der Inverter hat begrenzte Verarbeitungskapazität. Wenn Modbus aktiv: REST auf 60-90s verlangsamen.

2. **Modbus Register einzeln lesen ist langsam** -- 90 Register × 50ms = 4.5s pro Poll-Zyklus. Ideal wäre Block-Read (zusammenhängende Register in einem Request), aber die Kostal-Register sind nicht zusammenhängend.

3. **Startup-Optimierung: Modbus Device Info vor REST Settings** -- Modbus Register 34 (String Count) ist sofort verfügbar, REST Properties:StringCnt kann bei Last timeoutsen.

### Projekt-Statistiken

| Metrik | Wert |
|---|---|
| Neue Dateien | 42 |
| Neue Codezeilen | ~8.500 |
| Tests | 348 |
| Test Coverage | 100% |
| mypy Errors | 0 (30 Dateien) |
| pyright Errors | 0 |
| Releases | v2.5.0 → v2.9.0 (5 Minor, 3 Patch) |
| Modbus Register | 90+ |
| HA Entities (Modbus) | 8 Number + 2 Button + 10 Health Sensor + 11 Binary Sensor + 5 Diagnose + 3 Longevity + 2 Fire Safety Sensor + 4 Fire Safety Binary |
| Safety Fixes | 5 CRITICAL + 5 HIGH |
