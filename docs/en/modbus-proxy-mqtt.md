# KOSTAL KORE Proxy – Connecting evcc & external systems

**English** · [Deutsch](../de/modbus-proxy-mqtt.md)

## Architecture

```
                             ┌─── Home Assistant (HA entities)
                             │
Kostal Inverter ◄─Modbus TCP (exclusive)──► KOSTAL KORE Integration
  (port 1502)                                  │
                                ┌──────────────┼──────────────┐
                                │              │              │
                          Modbus TCP       MQTT Bridge    HA Entities
                           Proxy              │
                          (port 5502)         │
                            │            ┌────┴────┐
                           evcc       iobroker  Node-RED
```

**Only this integration connects to the inverter via Modbus TCP.**
External systems use either the **Modbus TCP Proxy** or the **MQTT Bridge**.

---

## Option 1: Modbus TCP Proxy (recommended for evcc)

The Modbus TCP Proxy makes all inverter registers available on a local TCP port.
This lets evcc use the **built-in Kostal template** directly –
no custom meter configuration needed.

### 1. Prerequisites

- Modbus enabled in the integration (Settings → Devices → Kostal → Configure)
- **Modbus proxy enabled** in the same configuration
- Proxy port: default 5502 (freely selectable)
- Proxy bind address: default `127.0.0.1` (recommended). Modbus TCP has no client
  authentication: every client that reaches the port can read and write. Keep the
  proxy on loopback, or protect a LAN address with a firewall that only admits
  trusted clients.
- Writes to the battery-control registers additionally require a stored
  installer/service code; this check does not cover other registers.

### 2. Configure evcc

In `evcc.yaml`, point the Kostal inverter at the **proxy port of the HA machine**
(not directly at the inverter!):

```yaml
meters:
- name: pv
  type: template
  template: kostal-plenticore-gen2
  usage: pv
  modbus: tcpip
  id: 71
  host: <HOME-ASSISTANT-IP>  # IP of the HA server, NOT of the inverter!
  port: 5502                  # proxy port (default 5502)

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

**Important:**
- `host` = IP address of the Home Assistant server (e.g. `192.168.1.100`)
- `port` = proxy port (default `5502`, configurable in the integration options)
- `id` = Modbus unit ID of the inverter (default `71`)
- **Template name:** For **all** generations (G1/G2/G3), use `kostal-plenticore-gen2` – this
  template covers G1/G2/G3. The former name `kostal-plenticore` is still accepted by evcc
  (mapped to `kostal-plenticore-gen2` via `covers:`, see evcc PR #30854), but should no longer
  be used for new configurations. If in doubt, check the current evcc documentation.
- **`endianness` (byte order):** The `endianness` parameter in the evcc template must match the
  inverter byte order that KORE detects automatically (default `little` =
  `float32s`). Since evcc PR #30862, a mismatch affects not only the battery registers but also
  the **PV energy** (Reg 1056) – if energy values are wrong, check this first.

### 3. Battery control via evcc

evcc can also write battery registers through the proxy. The relevant registers:

| Register | Address | Description |
|----------|---------|-------------|
| `bat_charge_dc_abs_power` | 1034 | Set charge/discharge power (W) |
| `bat_max_charge_limit` | 1038 | Limit maximum charge power (W) |
| `bat_max_discharge_limit` | 1040 | Limit maximum discharge power (W) |
| `bat_min_soc` | 1042 | Set minimum SoC (%) |
| `bat_max_soc` | 1044 | Set maximum SoC (%) |
| `g3_max_charge` | 1280 | G3: Max. charge power (W) |
| `g3_max_discharge` | 1282 | G3: Max. discharge power (W) |

> ⚠️ **evcc `batteryMode` (gen2):** The `kostal-plenticore-gen2` template controls the battery via
> `batteryMode` (modes `normal`/`hold`/`charge`/`holdcharge`) and does so by **cyclically** (watchdog)
> writing the power setpoint registers **1034** (charge/discharge power), **1038** (`=0` blocks charging) and
> **1040** (`=0` blocks discharging). These are exactly the registers KORE drives internally.
> Therefore, do **not** run evcc `batteryMode` **and** an internal KORE controller
> (SoC controller, GridGuard/Grid Feed-In Optimizer, Block Battery Charging) at the same time – otherwise
> both compete for registers 1034/1038 and KORE rejects the evcc writes with `0x06` (Server Device Busy).
> Rule of thumb: use exactly **one** battery controller: evcc `batteryMode`, a KORE
> controller or the inverter's own battery control.
> KORE does not coordinate with control logic inside the inverter. While KORE or evcc
> controls the battery, switch off *Smart AC Charge*, time-controlled or dynamic-tariff
> charging and EEBus control in the inverter.
> See "Write arbitration" below.

**`batteryMode` register mapping (evcc gen2):**

| evcc mode | Register | Value | Effect |
|-----------|----------|-------|--------|
| `normal` | 1034 | up to evcc 0.315: `0`; from **0.316** no write anymore (evcc PR #32396) | back to automatic |
| `charge` | 1034 | `-maxchargepower` (W, **negative = charge**) | grid charging at full power |
| `discharge` (from evcc 0.316) | 1034 | `+maxdischargepower` (W, **positive = discharge**) | forced discharge/feed-in (evcc PR #32396) |
| `hold` | 1040 | `0` | blocks **discharging** only, PV charging remains allowed (evcc PR #26169) |
| `holdcharge` | 1038 | `0` | blocks **charging** only (evcc PR #30853) |

> ⚠️ **evcc ≥ 0.316:** Because `normal` no longer resets register 1034, a previously
> written charge/discharge setpoint can remain active until a new write or the inverter's
> fallback logic replaces it.
> After a mode change, check the 1034 value before a KORE controller takes over the battery.
>
> **Feed-in limitation (evcc 0.311.1, PR #31487):** evcc writes `WMaxLimPct` (SunSpec register
> 40217, FC06 only). KORE's entity "Active Power Setpoint (Modbus)" (register 533) limits the same
> AC output power. Use only one of the two paths; otherwise the most recently written value applies.

**Notes for the evcc configuration:**
- In the evcc template, set **`maxchargepower` (watts)** – **not** the deprecated `maxchargerate` (%).
  Without `maxchargepower`, current evcc reports `ErrNotAvailable` when charging; older/faulty
  evcc versions instead charged at only ~100 W (evcc PR #26515 / #27161).
- **Minimum evcc version:** Force charge (#26515) and "charging during hold" (#26169) require a
  current evcc build. On outdated versions, charging may stay blocked in hold mode
  or the charge power may be wrong.

### 4. How the proxy works

- **Read requests**: Served from the coordinator's cache (data refreshed every 5s).
  NO additional connection to the inverter is opened.
- **Write requests**: Forwarded directly to the inverter over the existing Modbus connection.
- **Latency**: Read data is at most 5 seconds old (coordinator polling interval).

---

## Option 2: MQTT Bridge (for iobroker, Node-RED, custom evcc)

The MQTT Bridge publishes all Modbus data as MQTT topics and accepts
control commands via MQTT.

### 1. Prerequisites

- MQTT broker (e.g. Mosquitto)
- MQTT integration configured in Home Assistant
- Modbus enabled + MQTT Bridge enabled in the integration options
- Battery write access requires a stored installer/service code

### 2. Configure evcc with MQTT (alternative)

If no Modbus proxy is wanted:

```yaml
meters:
- name: pv
  type: custom
  power:
    source: mqtt
    topic: kostal_kore/{SERIAL}/proxy/pv_power
  energy:
    source: mqtt
    topic: kostal_kore/{SERIAL}/modbus/register/total_yield
    scale: 0.001  # Wh → kWh

- name: grid
  type: custom
  power:
    source: mqtt
    topic: kostal_kore/{SERIAL}/proxy/grid_power

- name: battery
  type: custom
  power:
    source: mqtt
    topic: kostal_kore/{SERIAL}/proxy/battery_power
  soc:
    source: mqtt
    topic: kostal_kore/{SERIAL}/proxy/battery_soc
  batterymode:
    source: watchdog
    timeout: 60s
    set:
      source: mqtt
      topic: kostal_kore/{SERIAL}/proxy/command/battery_charge
```

Replace `{SERIAL}` with the inverter's serial number.

### 3. Configure iobroker

Subscribe the MQTT adapter to:

```
kostal_kore/{SERIAL}/proxy/#     → simplified values
kostal_kore/{SERIAL}/modbus/#    → all register values
```

Write control commands:
```
kostal_kore/{SERIAL}/proxy/command/battery_charge    → charge power (W)
kostal_kore/{SERIAL}/proxy/command/battery_min_soc   → Min SoC (%)
kostal_kore/{SERIAL}/proxy/command/battery_max_soc   → Max SoC (%)
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
| `.../proxy/command/battery_charge` | any finite number (KORE applies no range check; the inverter limits it) | W | Negative=charge, positive=discharge |
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
| **Installer protection** | Battery write registers (words 1026–1045, 1280–1289) only with service code |
| **NaN/Infinity guard** | Rejected at MQTT, coordinator, and client layers |
| **Value validation** | Type check + numeric conversion before write |
| **Write arbitration** | Battery registers blocked when SoC controller is active |

## Write arbitration (multi-client protection)

When the internal **SoC controller** is active (target SoC set), external
write access to battery registers is **blocked**:

```
evcc writes REG 1034 ────► Proxy
                            │
                     SoC controller active?
                            │
                      YES → Modbus Exception 0x06 (Server Device Busy)
                            + Log: "REJECTED write to reg 1034"
                            → evcc detects the error, retries later
                            │
                        NO → Write passed through ✅
```

**Affected registers:** 1026, 1028, 1030, 1032, 1034, 1036, 1038, 1040, 1042, 1044, 1280, 1282, 1284, 1286, 1288 (both words each, i.e. 1026–1045 and 1280–1289)

**Resolving conflicts:**
1. Set the SoC controller to 0 (automatic) → external writes possible again
2. Or: configure the evcc template so that it controls the SoC controller via the HA API
3. Rule of thumb: exactly **one** controller per battery – evcc `batteryMode`, an internal KORE
   controller or the inverter's own battery control (see the note above).

## Troubleshooting

### evcc shows no data
- Proxy active? → HA logs: `Modbus TCP proxy started on 127.0.0.1:5502` (or the configured bind/port)
- Does evcc point to HA-IP:5502 (not to inverter-IP:1502)?
- "not a SunSpec device" → the proxy forwards SunSpec registers (40000+); check the connection

### evcc write error "Server Device Busy" (0x06)
- The SoC controller is active → set `number.XXX_battery_target_soc` to 0
- Check the HA logs for `REJECTED write` messages

### MQTT Bridge shows no data
- Check whether the MQTT Bridge is enabled in the integration options
- `kostal_kore/{SERIAL}/modbus/available` → `online`?
- Is the MQTT broker reachable from HA and evcc?

### Battery does not respond to control commands
- External battery control active? → inverter web UI → Service → Battery → "External via protocol"
- G3 bug: REG 1080 always reports 0; write test: REG 1034 = 0 → check readback
- Sign correct? REG 1034: **NEGATIVE** = charge, **POSITIVE** = discharge
- Keepalive active? The value must be rewritten every 15-30s (deadman switch)
