# Entity Reference: KOSTAL KORE (Kostal Plenticore)

**English** · [Deutsch](../de/entities.md)

Complete documentation of all entities, values, units and their meaning.
Note: The availability of individual entities depends on the inverter generation/firmware and on the access profile (User/Installer).

---

## When Modbus controls are greyed out

`Battery Charge Power (Modbus)`, `Battery Max Charge Limit (Modbus)`,
`Battery Max Discharge Limit (Modbus)` and `Active Power Setpoint (Modbus)`
are still present. After an incomplete slow Modbus poll
(`modbus_partial`), KORE marks the controls of this group as unavailable.
A missing energy value can also trigger this, even if individual charge limits
were read successfully. After a complete successful poll they become
available again. The safeguard prevents old readbacks from appearing
as current values. A disabled entity, or an entity that according to HA is no
longer provided by the integration, is a different case.

The green Modbus startup message only describes the check performed during setup.
It confirms neither all registers nor the current communication state or
write access. For a fault, the current communication diagnostics
and the logs from the same point in time are what matter.

`Battery Max Charge Power (SoC Ctrl)` shows an internal setting of the
SoC controller. A visible value therefore does not prove a successful device poll.
The G3 controls are separate, generation-specific settings; whether they are suitable
depends on model and firmware. They are not enabled or assigned across the board as a
replacement for failed Modbus controls.

`Active Power Setpoint (Modbus)` limits the inverter output power
in percent and is not a battery charge control. Without a write command previously sent by KORE,
its value can be unknown, because KORE does not read this setpoint cyclically.

## 1. Modbus registers (monitoring)

### 1.1 Device information

| Entity | Register | Unit | Description |
|---|---|---|---|
| `modbus_enable` | 2 | – | Modbus interface enabled (True/False). Must be switched on in the inverter WebUI. |
| `unit_id` | 4 | – | Modbus device address (default: 71). Required for communication. |
| `byte_order` | 5 | – | Byte order: 0 = Little-Endian (CDAB), 1 = Big-Endian (ABCD). Affects how float values are interpreted. |
| `article_number` | 6 | – | Article number of the inverter (e.g. "10541199"). |
| `serial_number` | 14 | – | Serial number of the inverter. Unique identification. |
| `num_bidirectional` | 30 | – | Number of bidirectional converters. For hybrid inverters with battery: 1. |
| `num_ac_phases` | 32 | – | Number of AC phases. Three-phase: 3. |
| `num_pv_strings` | 34 | – | Number of PV DC inputs. Determines how many DC string sensors are created. |
| `hw_version` | 36 | – | Hardware version number of the inverter. |
| `sw_version_mc` | 38 | – | Software version of the main controller (MC). |
| `sw_version_ioc` | 46 | – | Software version of the I/O controller (IOC). |
| `power_id` | 54 | – | Power ID of the inverter (internal identifier). |
| `inverter_state` | 56 | – | Current operating state: 0=Off, 1=Init, 2=IsoMeas, 3=GridCheck, 4=StartUp, 6=Feed-in, 7=Throttled, 8=External off, 9=Update, 10=Standby, 11=GridSync, 12=GridPreCheck, 13=GridSwitchOff, 14=Overheating, 15=Shutdown, 16=DC voltage invalid, 17=Emergency shutdown |
| `sw_version` | 58 | – | Overall software version (UI/SW, e.g. "3.06.01.20869"). |
| `product_name` | 768 | – | Product name (e.g. "PLENTICORE"). |
| `power_class` | 800 | – | Power class (e.g. "L G3" for 20kW Generation 3). |
| `worktime` | 144 | s | Total operating time of the inverter in seconds since initial commissioning. |
| `inverter_max_power` | 531 | W | Maximum AC output power of the inverter (e.g. 20000 for 20kW). Used for dynamic power limits. |

### 1.2 Power measurement

| Entity | Register | Unit | Description |
|---|---|---|---|
| `controller_temp` | 98 | °C | Temperature of the controller board (PCB). Normal: 30-60°C. Warning from 70°C. The inverter throttles automatically from approx. 75°C. |
| `total_dc_power` | 100 | W | Total DC input power of all PV strings combined. Positive during the day, 0 or slightly negative at night. |
| `em_state` | 104 | – | State of the energy manager: 0x00=Idle, 0x02=Emergency charge, 0x08=Winter mode stage 1, 0x10=Winter mode stage 2. |
| `home_from_battery` | 106 | W | Current power flowing from the battery into the house. > 0 only while the battery is discharging. |
| `home_from_grid` | 108 | W | Current power flowing from the grid into the house (grid import). 0 when self-sufficient. |
| `home_from_pv` | 116 | W | Current power flowing directly from the PV modules into the house (self-consumption). |
| `isolation_resistance` | 120 | Ω | Isolation resistance between the DC side and earth. Safe: >500kΩ. Critical: <100kΩ. At night/standby: 0 or maximum value (invalid). A falling trend can indicate cable damage, moisture or animal bites. |
| `power_limit_evu` | 122 | % | Power limitation by the grid operator (EVU). 100% = no limitation. <100% = curtailment active. |
| `home_consumption_rate` | 124 | % | Self-consumption rate: share of PV production that is consumed directly (not fed in). |
| `cos_phi` | 150 | – | Power factor (cos φ) at the inverter output. 1.0 = purely active power. <1.0 = reactive power component. |
| `grid_frequency` | 152 | Hz | Grid frequency. Nominal value: 50.000 Hz. Normal: 49.8-50.2 Hz. A deviation >±0.5 Hz indicates a grid disturbance. |
| `total_ac_power` | 172 | W | Total AC active power at the inverter output. Positive = feed-in, negative = import. |
| `total_ac_reactive` | 174 | Var | Total AC reactive power. Relevant for grid stability and cos φ control. |
| `total_ac_apparent` | 178 | VA | Total AC apparent power. Vector sum of active and reactive power. |
| `inverter_gen_power` | 575 | W | Current generation power of the inverter (AC side). |
| `total_dc_power_all` | 1066 | W | Sum of the DC power of all PV inputs (redundant to total_dc_power). |

### 1.3 AC phases (3-phase)

| Entity | Register | Unit | Description |
|---|---|---|---|
| `phase1_current` | 154 | A | Current on phase L1. Normal range: 0-30A depending on inverter power. |
| `phase1_power` | 156 | W | Active power on phase L1. With a symmetrical load it should be approx. 1/3 of the total power. |
| `phase1_voltage` | 158 | V | Voltage on phase L1. Nominal value: 230V. Standard range according to EN 50160: 207-253V. |
| `phase2_current` | 160 | A | Current on phase L2. |
| `phase2_power` | 162 | W | Active power on phase L2. |
| `phase2_voltage` | 164 | V | Voltage on phase L2. |
| `phase3_current` | 166 | A | Current on phase L3. |
| `phase3_power` | 168 | W | Active power on phase L3. |
| `phase3_voltage` | 170 | V | Voltage on phase L3. |

### 1.4 DC strings (PV inputs)

| Entity | Register | Unit | Description |
|---|---|---|---|
| `dc1_current` | 258 | A | Current from PV string 1. Depends on the number of modules and irradiance. |
| `dc1_power` | 260 | W | Power from PV string 1. With the same module layout it should be similar to DC2. A large deviation (>30%) indicates shading, soiling or a cable fault. |
| `dc1_voltage` | 266 | V | Voltage of PV string 1. Depends on the number of modules, temperature and irradiance. At night: open-circuit voltage or close to 0. |
| `dc2_current` | 268 | A | Current from PV string 2. |
| `dc2_power` | 270 | W | Power from PV string 2. |
| `dc2_voltage` | 276 | V | Voltage of PV string 2. |
| `dc3_current` | 278 | A | Current from PV string 3. Only if the 3rd input is in use. |
| `dc3_power` | 280 | W | Power from PV string 3. |
| `dc3_voltage` | 286 | V | Voltage of PV string 3. |

### 1.5 Battery

| Entity | Register | Unit | Description |
|---|---|---|---|
| `battery_soc` | 514 | % | State of charge of the battery. 0% = empty, 100% = full. |
| `battery_state_of_charge` | 210 | % | State of charge as a float value (more precise than battery_soc). |
| `battery_temperature` | 214 | °C | Temperature of the battery. LFP optimal: <30°C. NMC optimal: <25°C. The health warning applies from 45°C and critical from 55°C for every chemistry; the longevity rating uses chemistry-specific limits (LFP 50/60°C, NMC and unknown 45/55°C, see below). High temperatures accelerate ageing considerably. |
| `battery_voltage` | 216 | V | Battery voltage. Depends on chemistry and SoC. Typical for Pyontech Force H: 600-800V. |
| `battery_charge_current` | 190 | A | Charge current of the battery. Positive = charging. |
| `battery_actual_current` | 200 | A | Current charge/discharge current. Positive = charging, negative = discharging. |
| `battery_cycles` | 194 | – | Number of full charge/discharge cycles since initial commissioning. LFP batteries: >6000 cycles service life. NMC: >3000 cycles. |
| `battery_gross_capacity` | 512 | Ah | Gross capacity of the battery. **Note**: Can return an invalid value for some battery types (Pyontech). |
| `battery_work_capacity` | 1068 | Wh | Usable working capacity of the battery in watt-hours. More reliable than battery_gross_capacity. |
| `battery_type` | 588 | – | Battery type code: 0x0004=BYD, 0x0040=LG, 0x0200=Pyontech, 0x2000=VARTA, 0x1000=Dyness, 0x0008=BMZ, 0x0010=AXIstorage. |
| `battery_serial` | 1070 | – | Serial number of the battery. |
| `battery_max_charge_hw` | 1076 | W | Maximum charge power allowed by the battery hardware. Set by the BMS. |
| `battery_max_discharge_hw` | 1078 | W | Maximum discharge power allowed by the battery hardware. |
| `battery_mgmt_mode` | 1080 | – | Battery management mode: 0x00=No external control (or no Modbus command received yet), 0x01=External via digital I/O, 0x02=External via MODBUS (active). |
| `battery_cd_power` | 582 | W | Current battery charge/discharge power. Positive = discharging, negative = charging. |
| `sensor_type` | 1082 | – | Installed energy meter type: 0x00=SDM 630, 0x01=B-Control EM-300, 0x03=KOSTAL Smart Energy Meter (KSEM), 0xFF=No sensor. |

### 1.6 Energy counters (cumulative)

| Entity | Register | Unit | Description |
|---|---|---|---|
| `total_yield` | 320 | Wh | Total energy generated since initial commissioning (lifetime). |
| `daily_yield` | 322 | Wh | Energy generated today (reset at midnight). |
| `monthly_yield` | 326 | Wh | Energy generated this month. |
| `yearly_yield` | 324 | Wh | Energy generated this year. |
| `generation_energy` | 577 | Wh | Total generation energy (AC side). |
| `total_home_battery` | 110 | Wh | Total home consumption from battery (cumulative). |
| `total_home_grid` | 112 | Wh | Total home consumption from grid (cumulative). |
| `total_home_pv` | 114 | Wh | Total home consumption from PV (cumulative). |
| `total_home_consumption` | 118 | Wh | Total home consumption from all sources (battery + grid + PV). |
| `total_dc_charge` | 1046 | Wh | Total DC-side charge energy into the battery (from PV and grid). Sensor `Battery DC Charge Energy Total (Modbus)` in kWh. |
| `total_dc_discharge` | 1048 | Wh | Total DC-side discharge energy (battery → inverter). |
| `total_ac_charge` | 1050 | Wh | Total AC-side charge energy (grid/PV → battery, measured on the AC side). |
| `total_ac_discharge` | 1052 | Wh | Total AC-side discharge energy (battery → grid). |
| `total_ac_charge_grid` | 1054 | Wh | According to the KOSTAL documentation, the AC-side charge energy from the grid (*Total AC charge energy (grid to battery)*). Sensor `Battery AC Charge Energy from Grid Total (Modbus)` in kWh. During a night without PV, ΔREG 1046 / ΔREG 1054 gives the efficiency of grid charging. That 1054 really is the AC side has not yet been confirmed against a meter (HV9). |
| `total_dc_pv_energy` | 1056 | Wh | Total DC energy of all PV inputs (sum PV1+PV2+PV3). |
| `total_dc_pv1` | 1058 | Wh | Total DC energy from PV string 1. |
| `total_dc_pv2` | 1060 | Wh | Total DC energy from PV string 2. |
| `total_dc_pv3` | 1062 | Wh | Total DC energy from PV string 3. |
| `total_ac_to_grid` | 1064 | Wh | Total energy fed into the grid (cumulative). |

### 1.7 Energy meter (power meter / KSEM)

| Entity | Register | Unit | Description |
|---|---|---|---|
| `pm_total_active` | 252 | W | Total active power at the energy meter. Positive = grid import, negative = feed-in. With sensor position 2 (grid connection point). |
| `pm_total_reactive` | 254 | Var | Total reactive power at the energy meter. |
| `pm_total_apparent` | 256 | VA | Total apparent power at the energy meter. |
| `pm_cos_phi` | 218 | – | Power factor at the energy meter. |
| `pm_frequency` | 220 | Hz | Grid frequency measured at the energy meter. |

---

## 2. Modbus control (writable registers)

### 2.1 Battery control

| Entity | Register | Unit | Range | Description |
|---|---|---|---|---|
| **Battery Charge Power (Modbus)** | 1034 | W | -MaxPower..+MaxPower | DC-side battery charge power. **Negative = charging**, positive = discharging. Example: -5000 = charging at 5kW. Requires "External via protocol (Modbus TCP)" in the inverter WebUI. |
| **Battery Max Charge Limit (Modbus)** | 1038 | W | 0..MaxPower | Limits the maximum charge power of the battery. The value is capped from above by the BMS limit (battery_max_charge_hw). **On a G3, the read value is the effective limit, not the command** (night of 24/25 Oct 2026): 1000 W reads as 997.9–999.9 W; **Battery Manual Charge** (forced charging) raises it to the inverter maximum (~21.4 kW); with charging switched off it reads 0; shortly before full charge the BMS lowers it (245 W, 195 W); after a limit is raised, the displayed value only catches up after some time. The entity's state is therefore not the last command. Attributes: `last_commanded_value`, `last_command_outcome` (`confirmed`, `limited`, `above_command`, `below_command`, `unreadable`, `invalidated`), `last_command_readback`. If it reads **below** the command, the command is considered applied and limited by the BMS (`limited`, HV8). Only if it reads above it (forced charging), is unreadable, or a raised limit still reads the previous command (ignored), does the write raise `ReadbackUnconfirmedError` with `outcome`, `commanded` and `observed`. |
| **Battery Max Discharge Limit (Modbus)** | 1040 | W | 0..MaxPower | Limits the maximum discharge power. 0 = discharging disabled. As with 1038, the read value is the effective limit: shortly before the battery is empty the BMS lowers it; a value below the command is considered applied (`limited`). |
| **Battery Min SoC (Modbus)** | 1042 | % | 5..100 | Minimum state of charge. The battery is not discharged below this value. 5% minimum to protect against deep discharge. |
| **Battery Max SoC (Modbus)** | 1044 | % | 5..100 | Maximum state of charge. The battery is not charged above this value. |
| **Active Power Setpoint (Modbus)** | 533 | % | 1..100 | **Feed-in limitation / active power limitation** of the inverter **output** (Kostal-native equivalent of SunSpec Model 123 `WMaxLimPct`). **100% = no limitation** (full feed-in), <100% throttles the AC output power. **Minimum 1%** — true 0% (zero feed-in) cannot be reached here; for that use REST `ActivePower:ExtCtrlP:P`=0 or a watt limit. **Volatile**: the inverter discards the value on restart/reset and returns to full power (fail-open, no keepalive needed). Not to be confused with the *Grid Feed-In Optimizer* (which limits battery **charging** via Reg 1038, not the output) or with the §14a **import** limit (`Inverter:ActivePowerConsumLimitation`). Attributes: `curtailment_active`, `at_full_power`. Valid on G3 from FW 3.04.01. |
| **G3 Max Charge Power (Modbus)** | 1280 | W | 0..MaxPower | G3-specific: maximum battery charge power. **Must be written cyclically** (automatic keepalive every fallback_time/2), otherwise the inverter activates fallback limits. |
| **G3 Max Discharge Power (Modbus)** | 1282 | W | 0..MaxPower | G3-specific: maximum battery discharge power. Same cyclic write requirement as G3 Max Charge. |

*MaxPower = read dynamically from register 531 (e.g. 20000W for PLENTICORE L G3)*

### 2.2 Grid control

| Entity | Register | Unit | Range | Description |
|---|---|---|---|---|
| `reactive_power_setpoint` | 583 | % | -100..+100 | Reactive power setpoint in percent of rated power. 0 = no reactive power. Positive = capacitive, negative = inductive. |
| `delta_cos_phi` | 585 | – | -32768..+32767 | Delta cos φ setpoint. Negative = under-excited, positive = over-excited. Value range -1.0..+1.0 mapped to -32768..+32767. |
| `low_prio_active_power` | 832 | W | 0..65535 | Low-priority active power setpoint. Must be scaled with the scale factor (register 833). |

---

## 3. Health monitoring entities

### 3.1 Health sensors

| Entity | Unit | Description |
|---|---|---|
| **Inverter Health Score** | % | Overall health rating (0-100%). 100% = everything optimal. Drops on warnings/errors. Attributes: overall_health, error_rate, communication_reliability. |
| **Inverter Health Level** | – | Health level as text: "good", "info", "warning", "critical", "unknown". The icon changes with the level (shield green/yellow/red). |
| **Isolation Resistance (Health)** | Ω | Isolation resistance with trend analysis. Attributes: trend (rising/stable/falling), min, max, avg. Falling trend = possible cable degradation. |
| **Controller Temperature (Health)** | °C | Controller temperature with peak tracking. Attribute: peak (highest measured value). For long-term monitoring of inverter ventilation. |
| **Battery Health (SoH Trend)** | % | Battery state of health with degradation tracking. Attributes: soh_trend, cycles_current, cycles_total. Falling trend = battery is ageing. |
| **Error Rate (per hour)** | – | Error rate per hour. Attributes: recent_events (last 5 events with category and level). >5/h = communication problem. |
| **Modbus Communication Reliability** | % | Success rate of Modbus queries. 100% = perfect. <95% = network/connection problem. |
| **DC String Imbalance** | % | Power difference between the DC strings. 0% = perfectly even. >30% = possible shading, soiling or cable fault. Attributes: dc1/dc2/dc3_power. |
| **Phase Voltage Imbalance** | % | Voltage difference between the AC phases. 0% = symmetrical. >3% = possible grid problem. Attributes: phase1/2/3_voltage. |
| **Inverter State Changes** | – | Counter of inverter state changes (e.g. FeedIn → Standby → FeedIn). Frequent changes indicate instability. |

### 3.2 Warning binary sensors

| Entity | ON when | Automation example |
|---|---|---|
| **Isolation Resistance Warning** | Isolation resistance < 500kΩ | Push message: "Isolation resistance low - check DC cables" |
| **Controller Overheat Warning** | Controller temp > 70°C | Notification: "Check inverter ventilation" |
| **Battery Health Warning** | Battery SoH < 80% | Long-term alarm: "Battery capacity declining" |
| **Battery Temperature Warning** | Battery temp > 45°C | "Check battery ventilation" |
| **Grid Frequency Warning** | Frequency ±0.5 Hz from 50 Hz | "Grid disturbance detected" |
| **Phase 1/2/3 Voltage Warning** | Voltage < 195V or > 255V | "Grid voltage phase X abnormal" |
| **DC String Imbalance Warning** | Imbalance > 30% | "Check PV modules/cables" |
| **High Error Rate Warning** | > 5 errors/hour | "Check communication problem" |
| **Active Inverter Errors** | Active errors > 0 | "Check inverter error log" |

---

## 4. Fire protection entities

| Entity | Type | Description |
|---|---|---|
| **Fire Risk Level** | Sensor | Current risk level: safe / monitor / elevated / high / emergency. Attributes: active_alerts with details and recommended actions. |
| **Active Safety Alerts** | Sensor | Number of active safety alerts in the last hour. |
| **PV System Safety** | Binary Sensor | ON = system safe, OFF = safety alert active. Device Class: SAFETY. For automations. At night (no PV production): always ON. |
| **Isolation Fault Danger** | Binary Sensor | ON when an isolation fault with risk HIGH or EMERGENCY is detected. |
| **Battery Fire Risk** | Binary Sensor | ON when the battery temperature is critical or a voltage anomaly at high temperature is detected. |
| **DC Cable Danger** | Binary Sensor | ON when DC string data indicates a possible arc or cable fault. |

---

## 5. Calculated battery SoH entities

These entities calculate the battery state of health (SoH) directly from
Modbus telemetry — independently of the inverter-internal SoH register, which is
unreliable on some firmware versions. All values are persisted locally
and survive HA restarts. The entities are classified under `EntityCategory.DIAGNOSTIC`.

| Entity | Unit | Description |
|---|---|---|
| **Battery SoH (Calculated)** | % | Current state of health as a ratio: `current_capacity_wh / baseline_capacity_wh × 100`. Baseline = highest `battery_work_capacity` ever measured (only rises, never falls). Safeguards: 0.5 % noise threshold for raising the baseline, 10 MWh sanity ceiling against Modbus outliers. Unavailable until the first baseline has been determined. Attributes: `source`, `baseline_wh`, `current_wh`, `baseline_age_days`, `total_discharge_kwh`, `total_charge_kwh`, `cycles_observed`, `samples`. |
| **Battery SoH 5-Year Projection** | % | 5-year projection via OLS regression: linear slope of `capacity_wh` over `discharge_kwh` (industry-standard axis), extrapolated to 5 years using the observed annual throughput rate. Unavailable until the minimum requirements are met: ≥ 30 samples AND ≥ 30 days of observation period between the oldest and newest sample. Attributes: `source`, `degradation_per_kwh` (Wh of capacity loss per kWh of discharge), `annual_discharge_kwh`, `samples`, `projection_reliable`. |

### Important notes on commissioning

- **Battery SoH (Calculated)** is available immediately after the first HA start (as soon as a valid `battery_work_capacity` value is received).
- **Battery SoH 5-Year Projection** needs ~30 days of data collection. The attribute `projection_reliable = False` indicates that there is not enough data yet.
- Sampling interval: at least 3 hours between two samples, max. 500 samples (rolling window ≈ 62 days).
- Store schema V2: Earlier installations with the V1 schema (charge+discharge as the throughput axis) lose the regression samples on upgrade — the baseline is preserved.

---

## 6. Diagnosis entities

| Entity | Values | Description |
|---|---|---|
| **Diagnose: DC Solaranlage** | ok / hinweis / warnung / kritisch | Assesses PV strings, MC4 connectors, cable condition. The attribute `action` contains a concrete recommended action in plain text. |
| **Diagnose: AC Netzanbindung** | ok / hinweis / warnung / kritisch | Assesses phase voltage, frequency, power factor. Recommends contacting the grid operator if necessary. |
| **Diagnose: Batterie** | ok / hinweis / warnung / kritisch | Assesses temperature, SoH, cycles. On critical: evacuation notice. On notice: installation location recommendation. |
| **Diagnose: Wechselrichter** | ok / hinweis / warnung / kritisch | Assesses controller temperature, error status, communication. Recommends a ventilation check. |
| **Diagnose: Sicherheit** | ok / hinweis / warnung / kritisch | Overall safety assessment: isolation, fire risk, cable faults. On critical: immediate action recommended. |

---

## 6. Longevity entities

| Entity | Description |
|---|---|
| **Batterie Langlebigkeit** | Temperature assessment based on the detected battery chemistry (LFP/NMC). Shows "Optimal", "Akzeptabel" (acceptable), "Erhöht" (elevated) or "Kritisch" (critical). The attribute `tips` contains concrete recommendations for extending service life. The attribute `chemistry` shows the detected chemistry. |
| **Wechselrichter Langlebigkeit** | Assesses the average and peak temperature of the controller. Gives recommendations on ventilation and mounting location. |
| **PV-Anlage Langlebigkeit** | Monitors DC string balance and isolation trend. Gives recommendations on module cleaning and cable maintenance. |

---

## 7. Management entities

| Entity | Type | Description |
|---|---|---|
| **Import Legacy Plenticore Data** | Button | Step 1 of the legacy migration: imports old entry data and migrates registry bindings without removing the legacy entry immediately. Full documentation: [migration.md](migration.md). |
| **Finalize Legacy Cleanup** | Button | Step 2 of the legacy migration: removes remaining legacy entities/device links and then deletes the old legacy entry (3-step confirmation). |
| **Legacy Cleanup Confirmation Code** | Text | Confirmation code for *Finalize Legacy Cleanup* (copy it from the notification). |
| **Reset Modbus Registers** | Button | Resets all temporarily disabled registers. Press after a firmware update or inverter replacement so that new registers are detected. |
| **Run Modbus Diagnostics** | Button | Reads all registers and creates a detailed diagnostic report as an HA persistent notification. 100% read-only, no write access. |

Diagnostics download, the debug action and how to interpret charge limits are described in
[Diagnostic reports](diagnostics.md).

---

## Threshold reference

### Temperature thresholds by battery chemistry

| Level | LFP (BYD, Pyontech, VARTA) | NMC (LG, BMZ) | Unknown |
|---|---|---|---|
| **Optimal** | < 30°C | < 25°C | < 25°C |
| **Acceptable** | < 40°C | < 35°C | < 35°C |
| **Warning** | > 50°C | > 45°C | > 45°C |
| **Critical** | > 60°C | > 55°C | > 55°C |

### Inverter temperature

| Level | Controller PCB |
|---|---|
| **Info** | > 62°C |
| **Warning** | > 70°C |
| **Critical** | > 80°C |

### Grid thresholds

| Parameter | Info | Warning | Critical |
|---|---|---|---|
| Frequency | ±0.3 Hz | ±0.5 Hz | ±1.0 Hz |
| Phase voltage | 207-253V | 195-255V | 185-265V |

### Battery service life

| Parameter | Info | Warning | Critical |
|---|---|---|---|
| SoH | < 90% | < 80% | < 60% |
| Cycles (LFP) | > 4000 | > 6000 | > 8000 |
| Cycles (NMC) | > 3000 | > 4000 | > 6000 |

---

*Last Updated: 2026-03-01*
