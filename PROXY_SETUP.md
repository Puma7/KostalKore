# Kostal Plenticore Proxy – evcc & externe Systeme anbinden

## Architektur

```
                             ┌─── Home Assistant (HA entities)
                             │
Kostal Inverter ◄──Modbus TCP (exklusiv)──► Kostal Plenticore Integration
  (port 1502)                                  │
                                ┌──────────────┼──────────────┐
                                │              │              │
                          Modbus TCP       MQTT Bridge    HA Entities
                           Proxy              │
                          (port 5502)         │
                            │            ┌────┴────┐
                           evcc       iobroker  Node-RED
```

**Nur diese Integration verbindet sich per Modbus TCP mit dem Wechselrichter.**
Externe Systeme nutzen entweder den **Modbus TCP Proxy** oder die **MQTT Bridge**.

---

## Option 1: Modbus TCP Proxy (empfohlen für evcc)

Der Modbus TCP Proxy stellt alle Wechselrichter-Register auf einem lokalen TCP-Port
zur Verfügung. evcc kann damit das **eingebaute Kostal-Template** direkt verwenden –
keine Custom-Meter-Konfiguration nötig.

### 1. Voraussetzungen

- Modbus in der Integration aktiviert (Einstellungen → Geräte → Kostal → Konfigurieren)
- **Modbus-Proxy aktiviert** in der gleichen Konfiguration
- Proxy-Port: Standard 5502 (frei wählbar)

### 2. evcc konfigurieren

In `evcc.yaml` den Kostal-Wechselrichter auf den **Proxy-Port der HA-Maschine** zeigen
(nicht direkt auf den Wechselrichter!):

```yaml
meters:
- name: pv
  type: template
  template: kostal-plenticore-gen2
  usage: pv
  modbus: tcpip
  id: 71
  host: <HOME-ASSISTANT-IP>  # IP des HA-Servers, NICHT des Wechselrichters!
  port: 5502                  # Proxy-Port (Standard 5502)

- name: grid
  type: template
  template: kostal-plenticore-gen2
  usage: grid
  modbus: tcpip
  id: 71
  host: <HOME-ASSISTANT-IP>
  port: 5502

- name: battery
  type: template
  template: kostal-plenticore-gen2
  usage: battery
  modbus: tcpip
  id: 71
  host: <HOME-ASSISTANT-IP>
  port: 5502
```

**Wichtig:**
- `host` = IP-Adresse des Home-Assistant-Servers (z.B. `192.168.1.100`)
- `port` = Proxy-Port (Standard `5502`, konfigurierbar in den Integrationsoptionen)
- `id` = Modbus Unit-ID des Wechselrichters (Standard `71`)
- Die `template`-Namen hängen von deinem Wechselrichter ab. Für G2-Modelle: `kostal-plenticore-gen2`,
  für ältere G1: `kostal-plenticore`. Prüfe die aktuelle evcc-Dokumentation.

### 3. Batteriesteuerung über evcc

evcc kann über den Proxy auch Batterie-Register schreiben. Die relevanten Register:

| Register | Adresse | Beschreibung |
|----------|---------|-------------|
| `bat_charge_dc_abs_power` | 1034 | Lade/Entladeleistung setzen (W) |
| `bat_max_charge_limit` | 1038 | Maximale Ladeleistung begrenzen (W) |
| `bat_max_discharge_limit` | 1040 | Maximale Entladeleistung begrenzen (W) |
| `bat_min_soc` | 1042 | Mindest-SoC setzen (%) |
| `bat_max_soc` | 1044 | Maximum-SoC setzen (%) |
| `g3_max_charge` | 1280 | G3: Max. Ladeleistung (W) |
| `g3_max_discharge` | 1282 | G3: Max. Entladeleistung (W) |

### 4. So funktioniert der Proxy

- **Lese-Anfragen**: Werden aus dem Cache des Koordinators bedient (Daten alle 5s aktualisiert).
  Es wird KEINE zusätzliche Verbindung zum Wechselrichter aufgebaut.
- **Schreib-Anfragen**: Werden direkt über die bestehende Modbus-Verbindung an den Wechselrichter weitergeleitet.
- **Latenz**: Lesedaten sind max. 5 Sekunden alt (Polling-Intervall des Koordinators).

---

## Option 2: MQTT Bridge (für iobroker, Node-RED, Custom-evcc)

Die MQTT Bridge publiziert alle Modbus-Daten als MQTT-Topics und akzeptiert
Steuerungsbefehle über MQTT.

### 1. Voraussetzungen

- MQTT Broker (z.B. Mosquitto)
- MQTT Integration in Home Assistant konfiguriert
- Modbus aktiviert + MQTT Bridge aktiviert in den Integrationsoptionen

### 2. evcc mit MQTT konfigurieren (Alternative)

Falls kein Modbus-Proxy gewünscht ist:

```yaml
meters:
- name: pv
  type: custom
  power:
    source: mqtt
    topic: kostal_plenticore/{SERIAL}/proxy/pv_power
  energy:
    source: mqtt
    topic: kostal_plenticore/{SERIAL}/modbus/register/total_yield
    scale: 0.001  # Wh → kWh

- name: grid
  type: custom
  power:
    source: mqtt
    topic: kostal_plenticore/{SERIAL}/proxy/grid_power

- name: battery
  type: custom
  power:
    source: mqtt
    topic: kostal_plenticore/{SERIAL}/proxy/battery_power
  soc:
    source: mqtt
    topic: kostal_plenticore/{SERIAL}/proxy/battery_soc
  batterymode:
    source: watchdog
    timeout: 60s
    set:
      source: mqtt
      topic: kostal_plenticore/{SERIAL}/proxy/command/battery_charge
```

`{SERIAL}` durch die Seriennummer des Wechselrichters ersetzen.

### 3. iobroker konfigurieren

MQTT Adapter subscriben auf:

```
kostal_plenticore/{SERIAL}/proxy/#     → vereinfachte Werte
kostal_plenticore/{SERIAL}/modbus/#    → alle Register-Werte
```

Steuerbefehle schreiben:
```
kostal_plenticore/{SERIAL}/proxy/command/battery_charge    → Ladeleistung (W)
kostal_plenticore/{SERIAL}/proxy/command/battery_min_soc   → Min SoC (%)
kostal_plenticore/{SERIAL}/proxy/command/battery_max_soc   → Max SoC (%)
```

## MQTT Topic Reference

### Proxy Topics (simplified, for evcc/iobroker)

| Topic | Value | Unit | Update |
|---|---|---|---|
| `.../proxy/pv_power` | Total PV power | W | 5s |
| `.../proxy/grid_power` | Grid power (+import, -export) | W | 5s |
| `.../proxy/battery_power` | Battery power (+discharge, -charge) | W | 5s |
| `.../proxy/battery_soc` | Battery state of charge | % | 5s |
| `.../proxy/home_power` | Home consumption | W | 5s |
| `.../proxy/inverter_state` | Inverter state (text) | - | 5s |

### Proxy Command Topics (write)

| Topic | Value | Unit | Description |
|---|---|---|---|
| `.../proxy/command/battery_charge` | -5000 to +5000 | W | Negative=charge, positive=discharge |
| `.../proxy/command/battery_min_soc` | 5..100 | % | Minimum state of charge |
| `.../proxy/command/battery_max_soc` | 5..100 | % | Maximum state of charge |

### Full Register Topics (advanced)

| Topic | Description |
|---|---|
| `.../modbus/state` | Full JSON with all register values |
| `.../modbus/register/{name}` | Individual register value |
| `.../modbus/command/{name}` | Write to any writable register |
| `.../modbus/available` | `online` / `offline` |
| `.../modbus/config` | JSON metadata for discovery |

## Traffic Flow Control

| Feature | Implementation |
|---|---|
| **Rate limiting** | Max 1 write per register per second |
| **Command serialization** | Async lock prevents concurrent Modbus writes |
| **Source tracking** | Every command logged with origin (proxy/evcc, mqtt/command) |
| **Admin protection** | `modbus_enable`, `unit_id`, `byte_order` read-only via MQTT |
| **NaN/Infinity guard** | Rejected at MQTT, coordinator, and client layers |
| **Value validation** | Type check + numeric conversion before write |
| **Write-Arbitration** | Batterie-Register blockiert wenn SoC-Controller aktiv |

## Write-Arbitration (Multi-Client-Schutz)

Wenn der interne **SoC-Controller** aktiv ist (Ziel-SoC gesetzt), werden externe
Schreibzugriffe auf Batterie-Register **blockiert**:

```
evcc schreibt REG 1034 ──► Proxy
                            │
                     SoC Controller aktiv?
                            │
                       JA → Modbus Exception 0x06 (Server Device Busy)
                            + Log: "REJECTED write to reg 1034"
                            → evcc erkennt Fehler, retried später
                            │
                      NEIN → Write durchgeleitet ✅
```

**Betroffene Register:** 1034, 1038, 1040, 1042, 1044, 1280, 1282, 1284, 1286, 1288

**Lösung bei Konflikten:**
1. SoC-Controller auf 0 setzen (Automatik) → externe Writes wieder möglich
2. Oder: evcc-Template so konfigurieren, dass es den SoC-Controller via HA-API steuert

## Troubleshooting

### evcc zeigt keine Daten
- Proxy aktiv? → HA-Logs: `Modbus TCP proxy started on port 5502`
- evcc zeigt auf HA-IP:5502 (nicht auf Wechselrichter-IP:1502)?
- "not a SunSpec device" → Proxy leitet SunSpec-Register (40000+) weiter, prüfe Verbindung

### evcc Schreibfehler "Server Device Busy" (0x06)
- Der SoC-Controller ist aktiv → setze `number.XXX_battery_target_soc` auf 0
- Prüfe HA-Logs nach `REJECTED write` Meldungen

### MQTT Bridge zeigt keine Daten
- Prüfe ob MQTT Bridge in den Integrationsoptionen aktiviert ist
- `kostal_plenticore/{SERIAL}/modbus/available` → `online`?
- MQTT Broker erreichbar von HA und evcc?

### Batterie reagiert nicht auf Steuerbefehle
- Externe Batteriesteuerung aktiv? → WR-WebUI → Service → Batterie → "Extern über Protokoll"
- G3-Bug: REG 1080 meldet immer 0, Schreibtest: REG 1034 = 0 → Readback prüfen
- Vorzeichen korrekt? REG 1034: **NEGATIV** = Laden, **POSITIV** = Entladen
- Keepalive aktiv? Wert muss alle 15-30s neu geschrieben werden (Deadman-Switch)
