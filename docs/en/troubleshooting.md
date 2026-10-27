# Troubleshooting

**English** · [Deutsch](../de/troubleshooting.md)

When you report a problem, attach the diagnostics download
([diagnostics.md](diagnostics.md)) and, if possible, a debug log:

```yaml
logger:
  logs:
    custom_components.kostal_kore: debug
```

## Setup and connection

- **Cannot connect:** check the inverter's IP address and that Home Assistant
  reaches it on port 80. Discovery during setup is best effort; across VLANs or
  firewalls it often finds nothing. Enter the IP address manually.
- **Invalid authentication:** the password is the one of the inverter's web
  interface. The service code is optional and only needed for installer
  settings.
- **Timeouts:** the inverter answers only a limited number of requests at a
  time and is busy during updates and maintenance. KORE serialises its own
  requests; other tools polling the inverter at the same time make timeouts more
  likely.

## Setup fails with `cannot import name 'MessageTooLongError' from 'Crypto'`

The `pycryptodome` library in Home Assistant is broken: its files come from two
versions (3.x and 4.x). Up to version 3.0.16, KORE logged in through it. Since
3.0.17, KORE ships its own copy of pykoplenti that uses `cryptography` instead,
so updating KORE fixes the login. Other integrations that use `pycryptodome`
(for example the built-in Kostal integration) stay affected until the library
is reinstalled cleanly: on Home Assistant OS run `ha core rebuild` in the
Terminal add-on, or in the container run
`python3 -m pip install --force-reinstall --no-deps "pycryptodome>=3.19,<4"` and
restart Home Assistant (pykoplenti, which the built-in Kostal integration uses,
requires a 3.x version).

## Installer access required

If Home Assistant shows the repair issue *Installer access required*, add the
service code with **Reconfigure** in the entry's menu (**Configure** only holds
the Modbus, MQTT, proxy and KSEM options). Settings that the
inverter reserves for installers stay read-only until then.

## Modbus controls are greyed out or read-only

- Writes need Modbus TCP enabled in the inverter and, for battery control, the
  battery management mode *External via protocol (Modbus TCP)*. In other modes
  the battery controls are read-only.
- After an incomplete Modbus poll, KORE marks the control entities of that
  group as unavailable until a complete poll succeeds, so that stale read-backs
  are never shown as current values. See the
  [entity reference](entities.md).
- A failed command (`ReadbackUnconfirmedError`) says in its `outcome` why: the
  limit reads above the command (e.g. *Battery Manual Charge* is on), the
  register could not be read, or a raised limit still reads the previous
  command. A read *below* the command is the BMS limit in effect and counts as
  applied (`limited`).
- For communication problems on the proxy or MQTT paths, see
  [communication-failures.md](communication-failures.md).

## Isolation resistance shows `unknown`

Isolation resistance comes from Modbus register 120, not from REST.

- `unknown` means that no valid Modbus value has been read yet: during startup,
  after a disconnect, or because the model or firmware does not provide the
  register.
- During PV production the inverter reports **65,535,000 Ω** for an off-scale
  high value (its web interface shows about 65.5 MΩ). KORE records it and sets
  the attributes `modbus_off_scale_high` and `kostal_display_mohm`. At night the
  same raw value means "no measurement" and is not recorded.
- In the log, look for `Connection lost reading isolation_resistance` or
  `Illegal data address`, and check the Modbus host, port and unit ID.

## Home consumption, autarky or own consumption stay at 0

If the sensors that depend on the energy meter (home consumption, autarky,
own-consumption rate, grid feed-in energy, energy manager state) all report 0 or
are unavailable, the inverter is most likely not communicating with its energy
meter (KSEM) and runs without consumption data. That is a wiring or
commissioning issue on the inverter side; have the meter connection checked by
your installer. (Same root cause as home-assistant/core#166779 in the built-in
integration.)

## Recorder warnings about energy sensors

Occasionally the recorder logs *"Entity … has state class `total_increasing`,
but its state is not strictly increasing"* for energy sensors such as *Home
Consumption from Grid Day/Month/Year/Total*. This is harmless: the inverter
sometimes corrects a counter slightly downwards (e.g. 5.1 → 5.0 kWh), and Home
Assistant drops that single sample. The long-term statistics stay consistent.
KORE keeps `total_increasing` on purpose, because these counters reset per
period and the Energy dashboard handles that reset correctly. A negative energy
reading is shown as unavailable for one poll instead of a negative value. (The
built-in integration behaves the same, see home-assistant/core#174543.)

## The battery does something KORE did not command

Most often another controller writes the same registers: the inverter's own
*Smart AC Charge*, time-controlled or dynamic-tariff charging, EEBus, evcc's
`batteryMode`, or an automation. Use one controller per battery; see
[Battery control](../../README.md#battery-control) in the README. KORE logs a
warning when it sees the battery move against its setpoint.
