# Hardware-Validation TODO — Kostal G3 firmware coexistence

These follow-ups came out of cross-referencing the Kostal PLENTICORE G3 firmware
changelog (up to FW 3.06.10, 2026-06) against KORE. They are **deferred because they
require facts that can only be confirmed on a real inverter** (a register/REST value,
a behavior, or an address that is not in any public doc KORE has). Each item lists the
exact hardware question that unblocks it.

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
