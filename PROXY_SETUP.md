# Kostal Plenticore MQTT Proxy Bridge

## Architecture

```
                          ┌─── Home Assistant (HA entities)
                          │
Kostal Inverter ◄──Modbus TCP──► MQTT Proxy Bridge ◄──MQTT──► evcc
  (port 1502)          (exclusive)        │                    iobroker
                                          │                    Node-RED
                                          └──MQTT──►           any MQTT client
```

**Only this integration connects to the inverter via Modbus TCP.**
All external systems read data and send commands through MQTT.

## Setup

### 1. Prerequisites

- MQTT broker running (e.g. Mosquitto)
- MQTT integration configured in Home Assistant
- Modbus enabled in this integration (Settings > Devices > Kostal > Configure)
- MQTT Bridge enabled in the same configuration

### 2. Disconnect evcc from Modbus

In your evcc configuration, **remove** the direct Modbus connection:

```yaml
# REMOVE this:
# meters:
# - name: pv
#   type: template
#   template: kostal-plenticore
#   modbus: tcpip
#   ...
```

### 3. Configure evcc to use MQTT

Replace the direct Modbus meters with MQTT-based custom meters:

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

Replace `{SERIAL}` with your inverter's serial number (or the HA config entry ID).

### 4. Configure iobroker

Use the iobroker MQTT adapter to subscribe to:

```
kostal_plenticore/{SERIAL}/proxy/#     → simplified values
kostal_plenticore/{SERIAL}/modbus/#    → all register values
```

Write commands via:
```
kostal_plenticore/{SERIAL}/proxy/command/battery_charge    → set charge power (W)
kostal_plenticore/{SERIAL}/proxy/command/battery_min_soc   → set min SoC (%)
kostal_plenticore/{SERIAL}/proxy/command/battery_max_soc   → set max SoC (%)
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

## Troubleshooting

### evcc shows no data
- Check `kostal_plenticore/{SERIAL}/modbus/available` → should be `online`
- Verify MQTT broker is reachable from both HA and evcc
- Check HA logs for `MQTT proxy bridge started`

### Write commands are ignored
- Check HA logs for `Rate-limited` messages (wait 1 second between writes)
- Verify battery management mode is `External via MODBUS` in inverter web UI
- Check if register is in the protected list (modbus_enable, unit_id, byte_order)

### Values seem wrong
- Check endianness: auto-detection reads register 5 from the inverter
- Verify the inverter's Modbus byte order setting matches (little-endian CDAB is default)
