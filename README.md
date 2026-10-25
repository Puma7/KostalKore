# KOSTAL KORE for Home Assistant

**English** · [Deutsch](README.de.md)

[![CI](https://github.com/Puma7/KostalKore/actions/workflows/ci.yml/badge.svg)](https://github.com/Puma7/KostalKore/actions/workflows/ci.yml)
[![HACS](https://img.shields.io/badge/HACS-custom-orange.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![Release](https://img.shields.io/github/v/release/Puma7/KostalKore)](https://github.com/Puma7/KostalKore/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A custom Home Assistant integration for **KOSTAL PLENTICORE** inverters. It reads
the inverter over its local REST API and, optionally, over Modbus TCP. With
Modbus it can also set battery charge/discharge limits and share the inverter's
single Modbus connection with other systems such as evcc.

> **Independent project.** KOSTAL KORE is a community project. It is not made,
> endorsed or supported by KOSTAL Solar Electric GmbH. "KOSTAL" and
> "PLENTICORE" are trademarks of their owners and are used here only to name
> the hardware this integration talks to.

## Status: what is tested, and what is not

Please read this before you install it, especially before you enable battery
control.

| | |
|---|---|
| **Tested on real hardware** | One installation: the maintainer's PLENTICORE G3 L 20 kW with battery, running every day. |
| **Tested automatically** | About 1,350 tests against a mocked Home Assistant on Python 3.12 and 3.14, plus runs in a real Home Assistant instance against an inverter emulator (the [Inverter Charge Night](https://github.com/Puma7/InverterChargeNight) lab). Neither replaces your hardware. |
| **Untested** | PLENTICORE G1/G2, PLENTICORE MP G3, other power classes, other batteries and other firmware versions. They use the same interfaces and may work. Reports are welcome. |
| **Not supported** | Several inverters in one entry, Multi Device Control (MDC, firmware 3.06.10+), and running KORE's battery control at the same time as the inverter's own battery features (see [Battery control](#battery-control)). |

Large parts of the code were written with AI assistants (Claude, OpenAI Codex)
and reviewed by the maintainer. Questions about the inverter that are still
open are listed in
[docs/development/HARDWARE_VALIDATION_TODO.md](docs/development/HARDWARE_VALIDATION_TODO.md).

The integration comes without warranty (MIT license). Battery control writes to
your inverter. Check its effect on your own system.

## What it does

**Monitoring (REST, no extra setup)**
- PV, battery, grid and home power; DC string values; phase values.
- Energy statistics per day, month, year and total, ready for the Home Assistant
  Energy dashboard ([guide](docs/en/energy-dashboard.md)).
- Inverter state, inverter events and the settings the inverter exposes over
  REST. Changing settings is limited to an allowlist; some settings need the
  installer service code.
- Calculated sensors such as *Total Grid Consumption* and battery efficiency.
  They are sums or ratios of inverter counters and inherit their accuracy.

**Optional: Modbus TCP**
- Faster polling of power values, and values that REST does not provide
  (e.g. isolation resistance).
- Battery limits: *Battery Max Charge Limit (Modbus)* (register 1038),
  *Battery Max Discharge Limit (Modbus)* (1040), the G3 cyclic limits
  (1280/1282, kept alive automatically) and the active power setpoint (533).
  Writes through these entities are read back. A write that the inverter did
  not apply raises an error instead of being reported as done.
- Built-in controllers that use these limits: a battery target-SoC controller,
  *Block Battery Charging* and the *Grid Feed-In Optimizer*.
- A **Modbus proxy**, so evcc or another client can share KORE's single
  connection to the inverter, and an **MQTT bridge** that publishes values and
  accepts selected commands ([guide](docs/en/modbus-proxy-mqtt.md)).
- An optional KOSTAL Smart Energy Meter (KSEM) as a separate Modbus source.

**Diagnostics**
- Home Assistant diagnostics download, communication health per channel, repair
  issues for missing installer access and similar problems
  ([guide](docs/en/diagnostics.md)).
- Health, degradation and battery state-of-health sensors. These are estimates
  derived from inverter data, not measurements.
- Safety warnings (isolation resistance, temperatures, DC strings). These are
  software heuristics on inverter data. They are **not** a certified safety
  device and do not replace an arc-fault detector, a residual-current device or
  a smoke detector.

**Migration** from Home Assistant's built-in `kostal_plenticore` integration,
including entity IDs and history ([guide](docs/en/migration.md)).

## Requirements

- Home Assistant 2024.12 or newer.
- A PLENTICORE inverter reachable from Home Assistant on the local network
  (HTTP, port 80). The password of the inverter's web interface.
- Optional: the installer **service code**, for settings that the inverter
  reserves for installers.
- For Modbus: *Modbus TCP* enabled on the inverter (default port 1502,
  unit ID 71). For battery control, the inverter's battery management must be
  set to *External via protocol (Modbus TCP)*.

## Installation

**HACS (recommended)**
1. In HACS, open the menu (top right) → **Custom repositories**.
2. Add `https://github.com/Puma7/KostalKore`, category **Integration**.
3. Install **KOSTAL KORE** and restart Home Assistant.

**Manual**
1. Copy `custom_components/kostal_kore` to `<config>/custom_components/kostal_kore`.
2. Restart Home Assistant.

## Setup

1. **Settings → Devices & services → Add integration → KOSTAL KORE.**
2. Enter the host (leave it empty to try discovery on the local network), the
   password and, if you have it, the service code. Discovery is best effort and
   often fails across VLANs or firewalls; entering the IP address always works.
3. In the second step, choose whether to enable Modbus TCP, the MQTT bridge and
   the Modbus proxy. The wizard tests the Modbus connection and shows the
   detected access role (user or installer) and whether writes are possible.

All of this can be changed later under **Configure**.

## Battery control

KORE controls the battery over Modbus and only coordinates with itself and
with clients that use its proxy. It does **not** coordinate with control logic
running inside the inverter. Use **one** controller per battery:

- Switch off the inverter's own battery features while KORE controls the
  battery: *Smart AC Charge* (on by default since firmware 3.05), time-controlled
  or dynamic-tariff charging, and EEBus control.
- Do not let evcc's `batteryMode` (template `kostal-plenticore-gen2`) or your
  own automations write registers 1034/1038/1040 at the same time. While a KORE
  controller owns a register, KORE rejects competing writes through its proxy.
- An active §14a EnWG import limit also throttles grid charging.

On a G3, register 1038 reads back the limit **in effect**, not the last command.
The BMS lowers it as the battery fills (and 1040 as it empties), and *Battery
Manual Charge* lifts it to the inverter maximum. KORE therefore accepts a read
below the command as applied (`last_command_outcome = limited`). Details:
[entity reference](docs/en/entities.md).

## Documentation

| Topic | English | Deutsch |
|---|---|---|
| Entities, registers and attributes | [entities.md](docs/en/entities.md) | [entities.md](docs/de/entities.md) |
| Modbus proxy and MQTT bridge (evcc, ioBroker) | [modbus-proxy-mqtt.md](docs/en/modbus-proxy-mqtt.md) | [modbus-proxy-mqtt.md](docs/de/modbus-proxy-mqtt.md) |
| Energy dashboard | [energy-dashboard.md](docs/en/energy-dashboard.md) | [energy-dashboard.md](docs/de/energy-dashboard.md) |
| Troubleshooting | [troubleshooting.md](docs/en/troubleshooting.md) | [troubleshooting.md](docs/de/troubleshooting.md) |
| Diagnostics download | [diagnostics.md](docs/en/diagnostics.md) | [diagnostics.md](docs/de/diagnostics.md) |
| Communication failures | [communication-failures.md](docs/en/communication-failures.md) | [communication-failures.md](docs/de/communication-failures.md) |
| Firmware compatibility | [firmware-compatibility.md](docs/en/firmware-compatibility.md) | [firmware-compatibility.md](docs/de/firmware-compatibility.md) |
| Migration from `kostal_plenticore` | [migration.md](docs/en/migration.md) | [migration.md](docs/de/migration.md) |
| Orphaned recorder history | [orphan-history.md](docs/en/orphan-history.md) | [orphan-history.md](docs/de/orphan-history.md) |

KOSTAL's own documents (manuals, event list, the
[Modbus protocol description](https://www.kostal-solar-electric.com/fileadmin/downloadcenter/kse/BA/Protokollbeschreibung/BA_KOSTAL_Interface_MODBUS-TCP_SunSpec_with_Control.pdf))
are available on [kostal-solar-electric.com](https://www.kostal-solar-electric.com/).
Developer notes are in [docs/development/](docs/development/), changes in
[CHANGELOG.md](CHANGELOG.md).

## Getting help

Open an [issue](https://github.com/Puma7/KostalKore/issues) and attach the
diagnostics download. Please state the model and generation (e.g. G3 L 20 kW),
the inverter firmware, the battery and whether Modbus is enabled. To log in
detail:

```yaml
logger:
  logs:
    custom_components.kostal_kore: debug
    pykoplenti: debug
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Translations are welcome: the user
interface strings live in `custom_components/kostal_kore/translations/`.

## Credits and license

KORE builds on [pykoplenti](https://github.com/stegm/pykoplenti) by `@stegm` and
on the `kostal_plenticore` integration in Home Assistant Core. It became a
separate project because its scope and architecture moved too far apart to
merge back.

MIT license, see [LICENSE](LICENSE).
