"""Safe battery charge/discharge test suite with detailed debug logging.

Kostal Modbus sign convention (from official documentation Section 3.4):
    Register 1034 (bat_charge_dc_abs_power):
        NEGATIVE value = CHARGE the battery (grid → battery)
        POSITIVE value = DISCHARGE the battery (battery → grid)
        0 = automatic mode

    Register 582 (battery_cd_power) read-only:
        NEGATIVE = battery is charging
        POSITIVE = battery is discharging

    Register 200 (battery_actual_current):
        NEGATIVE = charge current
        POSITIVE = discharge current

    G3 Limit registers 1280/1282 are UNSIGNED limits (always positive).

    Register 1080 (battery_mgmt_mode):
        0x00 = No external battery management (writes ignored!)
        0x01 = External via digital I/O
        0x02 = External via MODBUS protocol (required for this test!)

    DEADMAN SWITCH: Section 3.5 states registers 1280/1282 must be
    written cyclically, otherwise fallback values activate. Register 1034
    also times out after ~60s without re-write.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from .modbus_coordinator import ModbusDataUpdateCoordinator
from .modbus_registers import (
    REG_BAT_CHARGE_DC_ABS_POWER,
    REG_G3_MAX_CHARGE,
    REG_G3_MAX_DISCHARGE,
    REGISTER_BY_NAME,
)

_LOGGER: Final = logging.getLogger(__name__)

KEEPALIVE_INTERVAL: Final[float] = 15.0
MONITOR_INTERVAL: Final[float] = 8.0
MIN_SOC_FOR_DISCHARGE: Final[float] = 10.0
MAX_SOC_FOR_CHARGE: Final[float] = 98.0
MAX_BATTERY_TEMP_C: Final[float] = 48.0
MIN_ISOLATION_KOHM: Final[float] = 200.0
GRID_BREAKER_LIMIT_W: Final[int] = 25_000
POWER_TOLERANCE_PCT: Final[float] = 50.0
RAMP_UP_SAMPLES: Final[int] = 3

# Fast path: only essential registers (5 reads ≈ 1-2s)
# Read AFTER keepalive to minimize time between write and next write
MONITOR_REGS_FAST: Final[list[str]] = [
    "battery_cd_power",
    "battery_soc",
    "pm_total_active",
    "battery_temperature",
    "inverter_state",
]

# Full set: read every 3rd cycle for detailed logging
MONITOR_REGS_FULL: Final[list[str]] = [
    "battery_cd_power", "battery_soc", "battery_state_of_charge",
    "battery_temperature", "battery_voltage", "battery_actual_current",
    "pm_total_active", "total_ac_power", "controller_temp",
    "isolation_resistance", "inverter_state",
    "home_from_battery", "home_from_grid", "home_from_pv",
    "total_dc_power", "g3_max_charge", "g3_max_discharge",
]

# Path for detailed debug log file
DEBUG_LOG_DIR: Final[str] = os.path.dirname(os.path.abspath(__file__))


@dataclass
class TestPhase:
    name: str
    power_w: int
    duration_s: int
    description: str


@dataclass
class PreFlightResult:
    ok: bool
    checks: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    inverter_max_w: int = 0
    bat_max_charge_w: float = 0.0
    bat_max_discharge_w: float = 0.0
    battery_soc: float = 0.0
    battery_temp: float | None = None
    home_load_w: float = 0.0
    battery_mgmt_mode: int = -1


@dataclass
class PhaseResult:
    phase: TestPhase
    success: bool
    samples: list[dict[str, Any]] = field(default_factory=list)
    abort_reason: str | None = None
    avg_actual_power: float = 0.0
    duration_actual_s: float = 0.0
    power_match: bool = False
    keepalive_writes: int = 0


DEFAULT_PHASES: Final[list[TestPhase]] = [
    TestPhase("Netzladung 1 kW", 1000, 300,
              "Batterie lädt mit 1 kW aus dem Netz"),
    TestPhase("Netzladung 5 kW", 5000, 180,
              "Batterie lädt mit 5 kW aus dem Netz"),
    TestPhase("Netzentladung 1 kW", -1000, 300,
              "Batterie entlädt 1 kW ins Netz"),
    TestPhase("Netzentladung 5 kW", -5000, 180,
              "Batterie entlädt 5 kW ins Netz"),
]


class BatteryTestSuite:

    def __init__(
        self,
        coordinator: ModbusDataUpdateCoordinator,
        hass: Any = None,
    ) -> None:
        self._coord = coordinator
        self._hass = hass
        self._running = False
        self._abort_requested = False
        self._log: list[str] = []
        self._debug_path = os.path.join(DEBUG_LOG_DIR, "battery_test_debug.log")
        self._debug_lines: list[str] = []

    @property
    def running(self) -> bool:
        return self._running

    @property
    def log_lines(self) -> list[str]:
        return list(self._log)

    def request_abort(self) -> None:
        self._abort_requested = True

    # ------------------------------------------------------------------
    # Debug file logging
    # ------------------------------------------------------------------

    def _debug(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{ts}] {msg}"
        self._debug_lines.append(line)
        _LOGGER.debug("[BatteryTest] %s", msg)

    def _flush_debug(self) -> None:
        try:
            with open(self._debug_path, "w", encoding="utf-8") as f:
                f.write("\n".join(self._debug_lines))
            self._emit(f"  📄 Debug-Log geschrieben: {self._debug_path}")
        except Exception as err:
            self._emit(f"  ⚠️  Debug-Log Schreibfehler: {err}")

    # ------------------------------------------------------------------
    # Direct register I/O
    # ------------------------------------------------------------------

    async def _read_reg(self, name: str) -> Any:
        reg = REGISTER_BY_NAME.get(name)
        if reg is None:
            return None
        try:
            val = await self._coord.client.read_register(reg)
            self._debug(f"READ  {name} (addr={reg.address}) = {val}")
            return val
        except Exception as err:
            self._debug(f"READ  {name} (addr={reg.address}) FAILED: {err}")
            return None

    async def _write_reg(self, name: str, value: float) -> bool:
        reg = REGISTER_BY_NAME.get(name)
        if reg is None:
            self._debug(f"WRITE {name} = {value} FAILED: unknown register")
            return False
        try:
            await self._coord.async_write_register(reg, value)
            self._debug(f"WRITE {name} (addr={reg.address}) = {value} OK")
            return True
        except Exception as err:
            self._debug(f"WRITE {name} (addr={reg.address}) = {value} FAILED: {err}")
            self._emit(f"  ❌ WRITE {name} = {value} → {err}")
            return False

    async def _read_monitor(self, full: bool = False) -> dict[str, Any]:
        """Read monitoring registers. Fast mode (5 regs, ~1s) or full (17 regs, ~8s)."""
        regs = MONITOR_REGS_FULL if full else MONITOR_REGS_FAST
        sample: dict[str, Any] = {"timestamp": time.time()}
        self._debug(f"--- MONITOR {'FULL' if full else 'FAST'} START ({len(regs)} regs) ---")
        for name in regs:
            val = await self._read_reg(name)
            if val is not None:
                try:
                    sample[name] = float(val)
                except (TypeError, ValueError):
                    sample[name] = val
        self._debug(f"--- MONITOR {'FULL' if full else 'FAST'} END ---")
        return sample

    # ------------------------------------------------------------------
    # Main
    # ------------------------------------------------------------------

    async def run(self, phases: list[TestPhase] | None = None) -> list[PhaseResult]:
        if self._running:
            raise RuntimeError("Already running")

        self._running = True
        self._abort_requested = False
        self._log.clear()
        self._debug_lines.clear()
        results: list[PhaseResult] = []

        if phases is None:
            phases = list(DEFAULT_PHASES)

        try:
            self._emit("═" * 60)
            self._emit("BATTERIE-TEST-SUITE v3")
            self._emit("═" * 60)
            self._emit("")
            self._emit("Kostal Vorzeichenkonvention (offizielle Doku §3.4):")
            self._emit("  REG 1034: NEGATIV = Laden, POSITIV = Entladen")
            self._emit("  REG 582:  NEGATIV = Laden, POSITIV = Entladen")
            self._emit(f"  Keepalive alle {KEEPALIVE_INTERVAL:.0f}s")
            self._emit(f"  Debug-Log: {self._debug_path}")
            self._emit("")

            self._debug("=== BATTERY TEST SUITE v3 STARTED ===")
            self._debug(f"Phases: {[(p.name, p.power_w, p.duration_s) for p in phases]}")

            preflight = await self._preflight(phases)
            if not preflight.ok:
                self._emit("❌ PRE-FLIGHT FEHLGESCHLAGEN")
                for e in preflight.errors:
                    self._emit(f"   ✗ {e}")
                await self._notify("Test abgebrochen", "\n".join(f"• {e}" for e in preflight.errors))
                return results

            self._emit("✅ Pre-Flight bestanden:")
            for c in preflight.checks:
                self._emit(f"   ✓ {c}")

            await self._notify(
                "Batterie-Test gestartet",
                f"{len(phases)} Phasen | SoC {preflight.battery_soc:.0f}% | "
                f"Mgmt-Mode {preflight.battery_mgmt_mode}",
            )

            for i, phase in enumerate(phases, 1):
                if self._abort_requested:
                    self._emit("⚠️  Benutzer-Abbruch")
                    break

                self._emit("")
                self._emit(f"{'━' * 60}")
                self._emit(f"PHASE {i}/{len(phases)}: {phase.name}")
                charging = phase.power_w > 0
                modbus_val = -abs(phase.power_w) if charging else abs(phase.power_w)
                self._emit(f"  Soll: {phase.power_w:+d} W → REG 1034 = {modbus_val:+d}")
                self._emit(f"  Dauer: {phase.duration_s}s | {phase.description}")
                self._emit("")

                result = await self._run_phase(phase, preflight)
                results.append(result)

                if not result.success:
                    self._emit(f"❌ ABBRUCH: {result.abort_reason}")
                    break

                icon = "✅" if result.power_match else "⚠️"
                self._emit(
                    f"{icon} Soll: {phase.power_w:+d} W | "
                    f"Ist: {result.avg_actual_power:+.0f} W | "
                    f"Writes: {result.keepalive_writes}x"
                )

                await self._reset_all()
                if i < len(phases):
                    self._emit("  ⏳ 15s Pause...")
                    await asyncio.sleep(15)

        finally:
            await self._reset_all()
            self._running = False
            self._emit("")
            self._emit("═" * 60)
            summary = self._build_summary(results)
            self._emit(summary)
            self._emit("═" * 60)
            self._flush_debug()
            await self._notify("Batterie-Test beendet", summary)

        return results

    # ------------------------------------------------------------------
    # Pre-flight
    # ------------------------------------------------------------------

    async def _preflight(self, phases: list[TestPhase]) -> PreFlightResult:
        r = PreFlightResult(ok=True)

        self._emit("Pre-Flight: Direkte Register-Reads...")
        self._debug("=== PRE-FLIGHT START ===")

        sample = await self._read_monitor(full=True)
        dev = self._coord.device_info_data or {}
        data = self._coord.data or {}

        self._emit("  Aktuelle Werte (direkt gelesen):")
        for k, v in sorted(sample.items()):
            if k != "timestamp":
                self._emit(f"    {k} = {v}")

        # Battery management mode - CRITICAL
        mgmt = await self._read_reg("battery_mgmt_mode")
        if mgmt is not None:
            r.battery_mgmt_mode = int(mgmt)
            if r.battery_mgmt_mode == 0:
                r.checks.append(f"⚠️  Batterie-Mgmt-Mode: {r.battery_mgmt_mode} (KEIN externer Zugriff!)")
                r.errors.append(
                    "Battery-Management-Mode = 0 (keine externe Steuerung). "
                    "Im Wechselrichter-WebUI unter Service → Batterie muss "
                    "'Extern über Protokoll (Modbus TCP)' aktiviert sein!"
                )
                r.ok = False
            elif r.battery_mgmt_mode == 2:
                r.checks.append(f"Batterie-Mgmt-Mode: {r.battery_mgmt_mode} (Modbus ✅)")
            else:
                r.checks.append(f"Batterie-Mgmt-Mode: {r.battery_mgmt_mode}")

        # Inverter max power
        raw_max = dev.get("inverter_max_power")
        if raw_max:
            try:
                r.inverter_max_w = int(raw_max)
            except (TypeError, ValueError):
                pass
        if r.inverter_max_w <= 0:
            r.inverter_max_w = 10000
        r.checks.append(f"WR-Max: {r.inverter_max_w} W")

        max_test = max(abs(p.power_w) for p in phases)
        if max_test > r.inverter_max_w:
            r.ok = False
            r.errors.append(f"Testleistung {max_test} W > WR {r.inverter_max_w} W")

        # Battery HW limits
        for key, attr, label in (
            ("battery_max_charge_hw", "bat_max_charge_w", "Lade"),
            ("battery_max_discharge_hw", "bat_max_discharge_w", "Entlade"),
        ):
            raw = data.get(key)
            if raw:
                try:
                    setattr(r, attr, float(raw))
                    r.checks.append(f"Batterie HW-{label}limit: {float(raw):.0f} W")
                except (TypeError, ValueError):
                    pass

        # SoC
        soc = sample.get("battery_soc") or sample.get("battery_state_of_charge") or 0
        r.battery_soc = soc
        if soc > 0:
            r.checks.append(f"SoC: {soc:.0f}%")
        else:
            r.ok = False
            r.errors.append("SoC nicht lesbar")

        if any(p.power_w > 0 for p in phases) and soc >= MAX_SOC_FOR_CHARGE:
            r.ok = False
            r.errors.append(f"SoC {soc:.0f}% zu hoch für Laden")
        if any(p.power_w < 0 for p in phases) and soc <= MIN_SOC_FOR_DISCHARGE:
            r.ok = False
            r.errors.append(f"SoC {soc:.0f}% zu niedrig für Entladen")

        # Temperature
        temp = sample.get("battery_temperature")
        if temp:
            r.battery_temp = temp
            r.checks.append(f"Batterie-Temp: {temp:.1f}°C")
            if temp > MAX_BATTERY_TEMP_C:
                r.ok = False
                r.errors.append(f"Batterie zu heiß ({temp:.1f}°C)")

        # Grid headroom
        grid = sample.get("home_from_grid", 0)
        r.home_load_w = grid
        r.checks.append(f"Hauslast Netz: {grid:.0f} W")

        # Isolation
        iso = sample.get("isolation_resistance")
        if iso:
            iso_k = iso / 1000.0
            r.checks.append(f"Isolation: {iso_k:.0f} kΩ")
            if iso_k < MIN_ISOLATION_KOHM:
                r.ok = False
                r.errors.append(f"Isolation {iso_k:.0f} kΩ < {MIN_ISOLATION_KOHM} kΩ")

        self._debug("=== PRE-FLIGHT END ===")
        return r

    # ------------------------------------------------------------------
    # Phase execution
    # ------------------------------------------------------------------

    async def _run_phase(self, phase: TestPhase, pf: PreFlightResult) -> PhaseResult:
        result = PhaseResult(phase=phase, success=False)
        start = time.monotonic()
        cycle_count = 0

        self._debug(f"=== PHASE START: {phase.name} power_w={phase.power_w} ===")

        # Initial write
        ok = await self._write_phase_regs(phase)
        if not ok:
            result.abort_reason = "Primäres Register 1034 nicht beschreibbar"
            return result
        result.keepalive_writes = 1
        last_write = time.monotonic()

        # Quick readback (only 4 critical registers)
        await asyncio.sleep(2)
        rb_1034 = await self._read_reg("bat_charge_dc_abs_power")
        rb_cd = await self._read_reg("battery_cd_power")
        rb_g3c = await self._read_reg("g3_max_charge")
        rb_g3d = await self._read_reg("g3_max_discharge")
        self._emit(
            f"  🔍 Readback: REG1034={rb_1034} | "
            f"bat_cd={rb_cd} | g3c={rb_g3c} | g3d={rb_g3d}"
        )

        elapsed = 0.0
        power_samples: list[float] = []

        while elapsed < phase.duration_s:
            if self._abort_requested:
                result.abort_reason = "Benutzer-Abbruch"
                return result

            cycle_count += 1

            # ──────────────────────────────────────────────
            # STEP 1: KEEPALIVE FIRST — before any slow I/O
            # This is critical: the deadman timer runs while
            # we do monitoring reads. Write BEFORE reading.
            # ──────────────────────────────────────────────
            time_since_write = time.monotonic() - last_write
            if time_since_write >= KEEPALIVE_INTERVAL:
                ok = await self._write_phase_regs(phase)
                result.keepalive_writes += 1
                last_write = time.monotonic()
                self._emit(
                    f"  🔁 Keepalive #{result.keepalive_writes} @ {elapsed:.0f}s "
                    f"(nach {time_since_write:.0f}s) {'✅' if ok else '⚠️'}"
                )

            # ──────────────────────────────────────────────
            # STEP 2: Fast monitoring (5 regs ≈ 1-2s)
            # Full read every 3rd cycle for detailed debug
            # ──────────────────────────────────────────────
            full_read = (cycle_count % 3 == 0)
            sample = await self._read_monitor(full=full_read)
            result.samples.append(sample)

            raw_bat = sample.get("battery_cd_power", 0)
            user_bat = -raw_bat  # Kostal: neg=charge → our: pos=charge
            power_samples.append(user_bat)

            soc = sample.get("battery_soc") or sample.get("battery_state_of_charge") or 0
            temp = sample.get("battery_temperature")
            grid = sample.get("pm_total_active", 0)
            bat_i = sample.get("battery_actual_current")

            t = f"T={temp:.0f}°C " if temp else ""
            i_str = f"I={bat_i:+.1f}A " if bat_i is not None else ""
            secs_to_next = KEEPALIVE_INTERVAL - (time.monotonic() - last_write)

            elapsed = time.monotonic() - start
            self._emit(
                f"  📊 {elapsed:5.0f}s │ "
                f"Bat={user_bat:+6.0f}W (raw:{raw_bat:+.0f}) │ "
                f"Grid={grid:+7.0f}W │ "
                f"SoC={soc:5.1f}% │ "
                f"{i_str}{t}"
                f"next_write:{secs_to_next:.0f}s"
            )

            abort = self._live_safety(phase, sample)
            if abort:
                result.abort_reason = abort
                return result

            # ──────────────────────────────────────────────
            # STEP 3: Sleep — but not longer than time to
            # next keepalive, so we never miss the deadline
            # ──────────────────────────────────────────────
            time_to_next_write = KEEPALIVE_INTERVAL - (time.monotonic() - last_write)
            time_to_phase_end = phase.duration_s - (time.monotonic() - start)
            actual_sleep = max(1.0, min(MONITOR_INTERVAL, time_to_next_write - 2.0, time_to_phase_end))
            await asyncio.sleep(actual_sleep)
            elapsed = time.monotonic() - start

        result.success = True
        result.duration_actual_s = time.monotonic() - start

        eval_samples = power_samples[RAMP_UP_SAMPLES:] if len(power_samples) > RAMP_UP_SAMPLES + 1 else power_samples
        if eval_samples:
            result.avg_actual_power = sum(eval_samples) / len(eval_samples)

        target = phase.power_w
        actual = result.avg_actual_power
        if target != 0:
            dev_pct = abs(actual - target) / abs(target) * 100
            result.power_match = dev_pct <= POWER_TOLERANCE_PCT
            self._emit(f"  Abweichung: {dev_pct:.0f}% ({'✅' if result.power_match else '⚠️'})")

        self._debug(f"=== PHASE END: {phase.name} avg={result.avg_actual_power:+.0f}W match={result.power_match} ===")
        return result

    async def _write_phase_regs(self, phase: TestPhase) -> bool:
        """Write control registers with CORRECT Kostal sign convention.

        Kostal §3.4: Negative = charge, Positive = discharge.
        Our phase.power_w: Positive = charge, Negative = discharge.
        → Invert the sign for register 1034.
        """
        charging = phase.power_w > 0
        power = abs(phase.power_w)

        # PRIMARY: Register 1034 with INVERTED sign
        # Kostal: negative=charge, positive=discharge
        modbus_val = float(-power) if charging else float(power)
        self._emit(f"  📝 REG 1034 = {modbus_val:+.0f} (Kostal: {'charge' if charging else 'discharge'})")
        primary_ok = await self._write_reg("bat_charge_dc_abs_power", modbus_val)

        # SUPPLEMENTARY: G3 limits (unsigned, best-effort)
        if charging:
            await self._write_reg("g3_max_charge", float(power))
            await self._write_reg("g3_max_discharge", 0.0)
        else:
            await self._write_reg("g3_max_charge", 0.0)
            await self._write_reg("g3_max_discharge", float(power))

        return primary_ok

    def _live_safety(self, phase: TestPhase, s: dict[str, Any]) -> str | None:
        soc = s.get("battery_soc") or s.get("battery_state_of_charge") or 50
        if phase.power_w > 0 and soc >= 99:
            return f"Batterie voll (SoC {soc:.0f}%)"
        if phase.power_w < 0 and soc <= MIN_SOC_FOR_DISCHARGE:
            return f"Batterie leer (SoC {soc:.0f}%)"
        temp = s.get("battery_temperature")
        if temp and temp > MAX_BATTERY_TEMP_C:
            return f"Batterie zu heiß ({temp:.1f}°C)"
        ctrl = s.get("controller_temp")
        if ctrl and ctrl > 80:
            return f"Controller zu heiß ({ctrl:.1f}°C)"
        iso = s.get("isolation_resistance")
        if iso and iso / 1000.0 < MIN_ISOLATION_KOHM:
            return f"Isolation kritisch"
        state = s.get("inverter_state")
        if state is not None:
            try:
                if int(state) in (0, 1, 10, 15):
                    return f"WR abgeschaltet (State={int(state)})"
            except (TypeError, ValueError):
                pass
        return None

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def _reset_all(self) -> None:
        self._debug("=== RESET ALL REGISTERS ===")
        await self._write_reg("bat_charge_dc_abs_power", 0.0)
        await self._write_reg("g3_max_charge", 20000.0)
        await self._write_reg("g3_max_discharge", 20000.0)
        self._emit("  🔄 Register auf Automatik zurückgesetzt")

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _emit(self, msg: str) -> None:
        self._log.append(msg)
        _LOGGER.info("[BatteryTest] %s", msg)

    def _build_summary(self, results: list[PhaseResult]) -> str:
        lines = ["ERGEBNIS-ZUSAMMENFASSUNG", ""]
        lines.append("Vorzeichenkonvention: +W = Laden, -W = Entladen")
        lines.append("(Kostal-intern invertiert: REG 1034 negativ=Laden)")
        lines.append("")
        for r in results:
            icon = "✅" if r.success and r.power_match else "⚠️" if r.success else "❌"
            dev_str = ""
            if r.success and r.phase.power_w != 0:
                dev = abs(r.avg_actual_power - r.phase.power_w) / abs(r.phase.power_w) * 100
                dev_str = f" (Abw: {dev:.0f}%)"
            reason = f" → {r.abort_reason}" if r.abort_reason else ""
            lines.append(f"  {icon} {r.phase.name}")
            lines.append(f"     Soll: {r.phase.power_w:+d} W | Ist: {r.avg_actual_power:+.0f} W{dev_str}{reason}")
            lines.append(f"     Keepalive: {r.keepalive_writes}x | Dauer: {r.duration_actual_s:.0f}s")

        passed = sum(1 for r in results if r.success and r.power_match)
        lines.append(f"\n{passed}/{len(results)} Phasen erfolgreich")
        lines.append(f"\n📄 Detailliertes Debug-Log: {self._debug_path}")
        lines.append("")
        lines.append("Für eigene Automationen:")
        lines.append("  number.XXX_battery_charge_power_modbus")
        lines.append("    REG 1034: NEGATIV = Laden, POSITIV = Entladen")
        lines.append(f"    Wert alle {KEEPALIVE_INTERVAL:.0f}s neu schreiben!")
        return "\n".join(lines)

    async def _notify(self, title: str, message: str) -> None:
        if self._hass is None:
            return
        try:
            await self._hass.services.async_call(
                "persistent_notification", "create",
                {"title": f"🔋 {title}", "message": message,
                 "notification_id": "kostal_battery_test"},
            )
        except Exception:
            pass
