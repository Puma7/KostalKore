# Hardware-Validation TODO — Kostal G3 firmware coexistence

These follow-ups came out of cross-referencing the Kostal PLENTICORE G3 firmware
changelog (updated through FW 3.07.03.27419, 2026-10) against KORE. They are **deferred because they
require facts that can only be confirmed on a real inverter** (a register/REST value,
a behavior, or an address that is not in any public doc KORE has). Each item lists the
exact hardware question that unblocks it.

The [firmware compatibility note](../en/firmware-compatibility.md) records the
documented 1024 correction and the backward-compatible 588 expansion. KORE does
not select a new writer or change every model's register width on update.
Before implementing either extension, validate generation, complete firmware
build, scale/word layout and independent feedback on old and corrected devices.
The same G3 release changes Smart AC-Link behavior, including mandatory use in
MDC configurations; recheck the native-control coexistence questions below on
that firmware rather than assuming a native mode can always be disabled.

How to gather most of these: run KORE's debug bundle / diagnostics service, pull a REST
`get_settings` dump (`pykoplenti` `get_setting_values`), and/or do a targeted Modbus scan
against your FW 3.06.10 G3.

> Implemented in 3.0.3 (no hardware needed): firmware-version awareness (`parse_firmware_version`
> / `firmware_at_least`), a heads-up log on FW ≥ 3.05, a read-only setpoint-divergence warning in
> the SoC controller, the reg-588 u16 protective comment, and docs. This file covers what is left.

---

## Hardware questions (answer these first)

- **HV1 — Does the firmware suspend its own battery control while Modbus battery mode
  (reg 1080 == 0x02) is active?** Set up KORE battery control, leave Smart AC Charge on, and
  watch whether the battery ever moves against KORE's setpoint (the new divergence warning will
  fire). → Decides whether **M1** (auto-disable) is necessary or merely belt-and-suspenders.
- **HV2 — On FW 3.06.03+, do NON-HELIVOR batteries still return a valid
  `battery_work_capacity` (Modbus reg 1068)?** The changelog says "battery capacity only for
  HELIVOR". If reg 1068 returns 0 / `ILLEGAL_DATA_ADDRESS`, KORE's computed SoH + degradation
  projection go unavailable for non-HELIVOR. → Gates the **O2** capacity fallback.
- **HV2b — Owner's battery chemistry mapping:** the owner's G3 shows the tight 35 °C
  battery-temperature thresholds, i.e. it is classified as NMC or "Unknown → conservative".
  Capture the **Battery Chemistry / Battery Type sensor value** (Modbus reg 588 code) from the
  live system; if it reads "Unknown (0x…)", add the code to `BATTERY_TYPES`
  (`modbus_registers.py`), the dict in `helper.py`, and `_TYPE_TO_CHEMISTRY`
  (`battery_chemistry.py`) so an LFP pack gets its correct 40 °C acceptable threshold.
- **HV3 — Exact Modbus address + datatype of the SunSpec model-802 battery SoH field**
  (from the updated *BA_KOSTAL Interface MODBUS-TCP / SunSpec* document). The changelog only says
  "SoH via SunSpec 802", not an address. → Blocks **O1**.
- **HV4 — Does FW 3.06.10 expose an MDC host/client role indicator** (a Modbus register or a REST
  settings key)? Needed to detect MDC setups. → Blocks **D1**.
- **HV5 — REST setting IDs for the new native grid-charging / dynamic-tariff battery modes**
  (from a `get_settings` dump). `Battery:SmartBatteryControl:Enable` and `Battery:TimeControl:Enable`
  are already known; the newer grid/tariff modes are not. → Completes **M3**.
- **HV6 — Is the active-power output setpoint (Modbus reg 533, "Active Power Setpoint") writable
  independent of the battery-management mode?** Today every Modbus number entity — including reg 533,
  which is *feed-in curtailment / Wirkleistungsbegrenzung*, not battery control — is gated by the
  read-only logic in `create_modbus_number_entities` (`modbus_number.py`): writable when reg 1080 ==
  0x02 ("External via Modbus"), or when reg 1080 == 0x00 and a read probe (`_probe_modbus_access`,
  which only reads Min SoC — never writes) succeeds; read-only for any other mode; and writable when
  reg 1080 is unavailable. On a G3, set the inverter to a non-Modbus battery mode and try
  writing reg 533 (e.g. 90 %): if the inverter accepts it, output curtailment is being needlessly
  gated behind battery mode and should be ungated (curtailment does not require external battery
  control). Also confirm the 1 % floor and that the value really is discarded on reset (fail-open).
  → Decides whether to relax the read-only gate for reg 533. Context: evcc PR #31487 adds this same
  capability to its Gen2 template via SunSpec model 123 (regs 40217/40221); KORE already covers it on
  G3 via reg 533, so **no SunSpec-123 port is needed** — only this gating question is open.
- **HV7 — Is there a definitive "energy meter (KSEM) present / communicating" datapoint?** When the
  inverter is not talking to its meter (stand-alone mode), all KSEM-dependent values read 0
  (home consumption, autarky, own-consumption rate, grid feed-in energy, energy-manager state) — the
  symptom behind upstream `kostal_plenticore` home-assistant/core#166779. A helpful diagnostic would
  surface "meter not communicating" instead of silent zeros, but a value-based heuristic (all-zero →
  meter missing) would false-positive on legitimately-zero moments (full battery + no grid flow), so
  it must be gated on a real status datapoint. Check a REST `get_settings`/`get_process_data` dump and
  a Modbus scan for a meter-present / meter-communication flag (e.g. a `scb:powermeter` field or a
  dedicated register). If one exists, add a read-only "Energy meter connected" diagnostic + optional
  repair hint. Note: KORE already handles the *other* two facets of #166779 — the device name uses the
  extracted hostname value (not the raw settings dict, so no `{"Hostname":…}` names), and the slow
  settings read at setup is timeout-protected (`get_hostname_id` via `asyncio.wait_for`).

- **HV8 — What do 1038 (and 1040) return when read on a G3?** Night 24/25 Oct 2026: 1038
  read 21 460 W right after `Battery:ManualCharge` = 1, 0 W after it was switched off, 245/195 W
  near full charge and 52.6 W a few seconds after restoring 12 540 W (which then read back a
  minute later). That fits "the limit in effect" (min of command, BMS limit; lifted by a forced
  charge), not the stored command. Confirm with the WebUI and the BMS max-charge register, and check
  1040 the same way. → Decides whether a read below the command may count as applied (today it
  fails with `outcome = below_command`) and how long KORE should wait after lifting a limit.
  **Answered 2026-10-25 (owner, G3):** yes — the BMS lowers the charge limit as the battery
  fills and the discharge limit as it empties, so 1038/1040 read min(command, BMS limit). A read
  below the command now counts as applied (`limited`); a raised limit that still reads the
  previous command still fails as ignored. Still open: whether the G3 cyclic limits 1280/1282
  read back the same way (they keep exact confirmation), and how long the read lags a raise.
- **HV9 — Which battery charge counters are AC and which are DC?** `Grid2Bat_P` tracks `Battery
  Power` within 1-2 % during night charging, so it is DC. Compare over one night without PV:
  REG 1054 (`total_ac_charge_grid`), REG 1050 (`total_ac_charge`), REG 1046 (`total_dc_charge`),
  REST `Statistic:EnergyChargeGrid:Total` and the grid meter import minus home consumption. →
  Decides whether `Total Grid Consumption` should use the AC-side counter so it includes the
  conversion loss.

---

## Deferred work items

### M1 — Auto-disable firmware battery levers on Modbus takeover  *(breaking, med)*  — gated by HV1
When a KORE feature acquires REG 1038 (SoC controller / grid feed-in optimizer / charge block /
battery test), best-effort REST-write `Battery:SmartBatteryControl:Enable=0` (and consider
`Battery:TimeControl:Enable=0`), remember the prior value, and restore on release.
- Reuse: the REST write path (`coordinator.py:~536` `client.set_setting_values`), the write
  allowlist (`helper.py:_ALLOWED_WRITE_IDS` already contains `Battery:SmartBatteryControl:Enable`;
  add `Battery:TimeControl:Enable` if used), and the owner lifecycle in
  `battery_reg_1038_owner.py` (`acquire_reg_1038_or_raise` / `release_reg_1038`).
- Why deferred: it mutates the user's inverter settings and its *necessity* depends on HV1. The
  3.0.3 divergence warning already surfaces the conflict read-only; auto-disable should ship once
  HV1 confirms the firmware actually fights Modbus mode.
- Tests: takeover writes 0 + restore on release; absent setting → no-op. (`battery_reg_1038_owner.py`
  is coverage-gated → needs full tests.)

### M3 — Detect/refuse on active native battery modes  *(breaking, med)*  — partial; gated by HV5
Before acquiring REG 1038, read the native-mode REST flags and refuse-with-message or warn,
reusing `acquire_reg_1038_or_raise`'s user-visible error. The **known** levers
(`Battery:SmartBatteryControl:Enable`, `Battery:TimeControl:Enable`) can be checked now; the
**new** grid-charging / dynamic-tariff modes need their setting IDs (HV5).

### D1 — MDC detection + self-protect  *(monitor, med)*  — gated by HV4
If FW 3.06.10 exposes an MDC host/client indicator, surface it; when an MDC host with "Battery
control with MDC" is detected, make the battery number entities read-only (reuse the read-only
path in `modbus_number.py:~205-234`) and skip KORE battery writes. Also verify whether aggregated
host telemetry (SoC reg 514, power 575/582, work-capacity 1068) reports host-aggregated vs. local
values — if aggregated, the SoH baseline / degradation tracking must use per-client data.

### O1 — Native SoH via SunSpec 802  *(opportunity, low)*  — gated by HV3
Once the address is known, add a read-only register (e.g. `REG_BATTERY_SOH_SUNSPEC`) + a native
Modbus SoH sensor (mainly for Modbus-only setups lacking the REST BMS SoH at `sensor.py:~419`);
optionally feed it into `health_monitor`/`degradation_tracker` as a higher-confidence source
*alongside* the estimator (keep `battery_soh_entities.py` `source` attribute). Watch word-order:
fields at addr ≥ 500 are forced big-endian (`modbus_client.py:~810-822`). Pre-3.06.05 returns
`ILLEGAL_DATA_ADDRESS` (already handled). **Do not invent the address.**

### O2 — HELIVOR battery type/chemistry + capacity fallback  *(monitor, low)*  — gated by HV2
(a) Add HELIVOR's reg-588 code to `BATTERY_TYPES` (`modbus_registers.py:~353`), the duplicate dict
in `helper.py`, and `_TYPE_TO_CHEMISTRY` (`battery_chemistry.py`) with its correct chemistry once
the code value is known (until then a new code degrades gracefully to "Unknown (0x…)" + conservative
thresholds — no breakage). (b) If HV2 shows non-HELIVOR loses reg 1068, wire the REST
`devices:local:battery[SoH]` feed (`sensor.py:~1538`) or reg 529 as a capacity/SoH fallback into
`BatterySohCalculator`.

### Shared divergence monitor across all four battery controllers  *(altitude, no HW needed)*
The setpoint-divergence diagnostic currently lives only in `BatterySocController`;
`grid_charge_limiter.py`, `charge_block_switch.py`, and `battery_test.py` blind-write the
same registers and would need the same ~50 lines copy-pasted. When extending it, move the
mechanism to a shared home keyed off the current REG-1038 owner (`battery_reg_1038_owner.py`)
or a small conflict-monitor on the Modbus coordinator, so all controllers are covered once.
Also note: HV1 uses the divergence warning as its measurement instrument — it only fires for
the SoC controller today, so validate HV1 via the SoC controller (not Charge Block).

### O3 (enhancement half) — §14a import-limit awareness  *(monitor, low)*  — no HW strictly needed
Optional: when grid-charging, read `em_state` (reg 104) / `power_limit_evu` (reg 122) /
`Inverter:ActivePowerConsumLimitationEnable` and emit a diagnostic when the grid-charge setpoint is
being throttled by an active import cap (the 3.0.3 divergence warning already catches the symptom).
Also add a test asserting the `Inverter:ActivePowerConsumLimitation` number/switch entities are
created when the datapoint is present (currently no coverage). Deferred only to avoid shipping a
complex integration test without a local test runner; pick up alongside the items above.

### Legacy battery-limit readbacks while native control is active
Compare read-only feedback from 1038/1040 with the inverter WebUI, supported REST
settings and device mode during standby and normal operation. Confirm the meaning
and enforcement of fractional or unexpected legacy limits before changing them.
Local SoC-controller defaults are separate configuration values; G3 1280/1282
are separate cyclic limits. Do not infer any of these values from the others or
replace device feedback with a guessed default. This requires real-hardware
observations; simulator decoding checks alone cannot establish firmware behavior.
