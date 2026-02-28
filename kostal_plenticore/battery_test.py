"""Safe battery charge/discharge test suite — evcc-compatible strategy.

Register strategy (matching evcc kostal-plenticore-gen2 template):

    CHARGE from grid:
        REG 1034 = -power_watts  (negative = charge, per Kostal §3.4 Note 1)
        Keepalive: re-write every 15s (evcc uses 30s = watchdog/2)

    DISCHARGE to grid:
        REG 1034 = +power_watts  (positive = discharge)
        REG 1038 = 0             (block charging during discharge)
        Keepalive: re-write every 15s

    HOLD (block discharge):
        REG 1040 = 0             (max discharge = 0, matching evcc hold mode)

    RESET to automatic:
        REG 1034 = 0

    G3 registers (1280/1282) are NOT used — evcc doesn't use them either,
    and they caused "Server device failure" errors on some firmware versions.

Read conventions:
    REG 582 (battery_cd_power):  negative=charging, positive=discharging
    REG 252 (pm_total_active):   negative=grid export, positive=grid import
      (depends on sensor position — position 2/grid: positive=import)

Debug log written to: custom_components/kostal_plenticore/battery_test_debug.log
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
from .modbus_registers import REGISTER_BY_NAME

_LOGGER: Final = logging.getLogger(__name__)

KEEPALIVE_INTERVAL: Final[float] = 15.0
MONITOR_INTERVAL: Final[float] = 8.0
MIN_SOC_FOR_DISCHARGE: Final[float] = 10.0
MAX_SOC_FOR_CHARGE: Final[float] = 98.0
MAX_BATTERY_TEMP_C: Final[float] = 48.0
GRID_BREAKER_LIMIT_W: Final[int] = 25_000
POWER_TOLERANCE_PCT: Final[float] = 50.0
RAMP_UP_SAMPLES: Final[int] = 3

# Essential registers for fast monitoring (~1-2s)
FAST_REGS: Final[list[str]] = [
    "battery_cd_power", "battery_soc", "pm_total_active",
    "battery_temperature", "inverter_state",
]

# Full register set for every 3rd cycle (~8s)
FULL_REGS: Final[list[str]] = FAST_REGS + [
    "battery_state_of_charge", "battery_voltage", "battery_actual_current",
    "total_ac_power", "controller_temp", "isolation_resistance",
    "home_from_battery", "home_from_grid", "home_from_pv", "total_dc_power",
    "bat_charge_dc_abs_power", "bat_max_charge_limit", "bat_max_discharge_limit",
]

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
    battery_soc: float = 0.0
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
        self, coordinator: ModbusDataUpdateCoordinator, hass: Any = None,
    ) -> None:
        self._coord = coordinator
        self._hass = hass
        self._running = False
        self._abort_requested = False
        self._log: list[str] = []
        self._debug_path = os.path.join(DEBUG_LOG_DIR, "battery_test_debug.log")
        self._dbg: list[str] = []

    @property
    def running(self) -> bool:
        return self._running

    @property
    def log_lines(self) -> list[str]:
        return list(self._log)

    def request_abort(self) -> None:
        self._abort_requested = True

    # ------------------------------------------------------------------
    # Register I/O helpers
    # ------------------------------------------------------------------

    def _d(self, msg: str) -> None:
        self._dbg.append(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}")

    async def _rd(self, name: str) -> Any:
        reg = REGISTER_BY_NAME.get(name)
        if not reg:
            return None
        try:
            v = await self._coord.client.read_register(reg)
            self._d(f"RD {name}({reg.address})={v}")
            return v
        except Exception as e:
            self._d(f"RD {name}({reg.address}) FAIL:{e}")
            return None

    async def _wr(self, name: str, value: float) -> bool:
        reg = REGISTER_BY_NAME.get(name)
        if not reg:
            self._d(f"WR {name} FAIL:unknown")
            return False
        try:
            await self._coord.async_write_register(reg, value)
            self._d(f"WR {name}({reg.address})={value} OK")
            return True
        except Exception as e:
            self._d(f"WR {name}({reg.address})={value} FAIL:{e}")
            self._emit(f"  ❌ WR {name}={value}: {e}")
            return False

    async def _read_sample(self, full: bool = False) -> dict[str, Any]:
        regs = FULL_REGS if full else FAST_REGS
        s: dict[str, Any] = {"ts": time.time()}
        for n in regs:
            v = await self._rd(n)
            if v is not None:
                try:
                    s[n] = float(v)
                except (TypeError, ValueError):
                    s[n] = v
        return s

    # ------------------------------------------------------------------
    # Control writes — evcc-compatible strategy
    # ------------------------------------------------------------------

    async def _write_charge(self, power: int) -> bool:
        """Force charge from grid. evcc mode 3: REG 1034 = -power."""
        val = float(-abs(power))
        self._emit(f"  📝 REG 1034 bat_charge_dc_abs = {val:+.0f}W (Laden {abs(power)}W)")
        return await self._wr("bat_charge_dc_abs_power", val)

    async def _write_discharge(self, power: int) -> bool:
        """Force discharge to grid. REG 1034 = +power, REG 1038 = 0 (block charge)."""
        val = float(abs(power))
        self._emit(f"  📝 REG 1034 bat_charge_dc_abs = {val:+.0f}W (Entladen {abs(power)}W)")
        ok1 = await self._wr("bat_charge_dc_abs_power", val)
        self._emit("  📝 REG 1038 bat_max_charge_limit = 0 (Laden blockiert)")
        ok2 = await self._wr("bat_max_charge_limit", 0.0)
        return ok1 and ok2

    async def _write_normal(self) -> None:
        """Reset to automatic. evcc mode 1: REG 1034 = 0."""
        await self._wr("bat_charge_dc_abs_power", 0.0)
        await self._wr("bat_max_charge_limit", 20000.0)
        await self._wr("bat_max_discharge_limit", 20000.0)
        self._emit("  🔄 Reset: REG 1034=0, REG 1038/1040=20000 (Automatik)")

    async def _keepalive(self, phase: TestPhase) -> bool:
        """Re-write the control value (deadman keepalive)."""
        if phase.power_w > 0:
            return await self._wr("bat_charge_dc_abs_power", float(-abs(phase.power_w)))
        else:
            ok1 = await self._wr("bat_charge_dc_abs_power", float(abs(phase.power_w)))
            ok2 = await self._wr("bat_max_charge_limit", 0.0)
            return ok1 and ok2

    # ------------------------------------------------------------------
    # Main
    # ------------------------------------------------------------------

    async def run(self, phases: list[TestPhase] | None = None) -> list[PhaseResult]:
        if self._running:
            raise RuntimeError("Already running")
        self._running = True
        self._abort_requested = False
        self._log.clear()
        self._dbg.clear()
        results: list[PhaseResult] = []
        if phases is None:
            phases = list(DEFAULT_PHASES)

        try:
            self._emit("═" * 60)
            self._emit("BATTERIE-TEST v4 (evcc-kompatible Strategie)")
            self._emit("═" * 60)
            self._emit("  Laden:    REG 1034 = -Watt (Kostal §3.4: negativ=Laden)")
            self._emit("  Entladen: REG 1034 = +Watt, REG 1038 = 0 (Laden blockiert)")
            self._emit("  Normal:   REG 1034 = 0")
            self._emit(f"  Keepalive: alle {KEEPALIVE_INTERVAL:.0f}s")
            self._emit(f"  Debug-Log: {self._debug_path}")
            self._emit("")

            pf = await self._preflight(phases)
            if not pf.ok:
                self._emit("❌ PRE-FLIGHT FEHLGESCHLAGEN")
                for e in pf.errors:
                    self._emit(f"   ✗ {e}")
                await self._notify("Test abgebrochen", "\n".join(f"• {e}" for e in pf.errors))
                return results

            self._emit("✅ Pre-Flight:")
            for c in pf.checks:
                self._emit(f"   ✓ {c}")

            await self._notify("Test gestartet", f"{len(phases)} Phasen | SoC {pf.battery_soc:.0f}%")

            for i, phase in enumerate(phases, 1):
                if self._abort_requested:
                    self._emit("⚠️  Abbruch")
                    break
                self._emit("")
                self._emit(f"{'━' * 60}")
                self._emit(f"PHASE {i}/{len(phases)}: {phase.name}")
                self._emit(f"  Soll: {phase.power_w:+d}W | Dauer: {phase.duration_s}s")

                r = await self._run_phase(phase)
                results.append(r)

                if not r.success:
                    self._emit(f"❌ ABBRUCH: {r.abort_reason}")
                    break
                icon = "✅" if r.power_match else "⚠️"
                self._emit(f"{icon} Ist: {r.avg_actual_power:+.0f}W | Writes: {r.keepalive_writes}x")

                await self._write_normal()
                if i < len(phases):
                    self._emit("  ⏳ 15s...")
                    await asyncio.sleep(15)

        finally:
            await self._write_normal()
            self._running = False
            self._emit("")
            self._emit("═" * 60)
            s = self._summary(results)
            self._emit(s)
            self._emit("═" * 60)
            self._flush_debug()
            await self._notify("Test beendet", s)

        return results

    # ------------------------------------------------------------------
    # Pre-flight
    # ------------------------------------------------------------------

    async def _preflight(self, phases: list[TestPhase]) -> PreFlightResult:
        r = PreFlightResult(ok=True)
        self._emit("Pre-Flight...")

        sample = await self._read_sample(full=True)
        dev = self._coord.device_info_data or {}

        self._emit("  Register-Dump:")
        for k, v in sorted(sample.items()):
            if k != "ts":
                self._emit(f"    {k} = {v}")

        # Battery management mode
        # NOTE: On some G3 firmware versions, register 1080 always reports 0
        # even when external control IS active in the WebUI. We downgrade
        # this to a warning and rely on the write-test below instead.
        mgmt = await self._rd("battery_mgmt_mode")
        if mgmt is not None:
            r.battery_mgmt_mode = int(mgmt)
            if r.battery_mgmt_mode == 2:
                r.checks.append(f"Mgmt-Mode: 2 (Modbus ✅)")
            elif r.battery_mgmt_mode == 0:
                r.checks.append(
                    f"⚠️  Mgmt-Mode: 0 (G3-Bug? Register meldet 'keine externe Steuerung', "
                    f"Schreibtest prüft ob es trotzdem funktioniert)"
                )
            else:
                r.checks.append(f"Mgmt-Mode: {r.battery_mgmt_mode}")

        # Inverter max
        raw_max = dev.get("inverter_max_power")
        if raw_max:
            try:
                r.inverter_max_w = int(raw_max)
            except (TypeError, ValueError):
                pass
        if r.inverter_max_w <= 0:
            r.inverter_max_w = 10000
        r.checks.append(f"WR-Max: {r.inverter_max_w}W")

        mx = max(abs(p.power_w) for p in phases)
        if mx > r.inverter_max_w:
            r.ok = False
            r.errors.append(f"Test {mx}W > WR {r.inverter_max_w}W")

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
            r.errors.append(f"SoC {soc:.0f}% → zu hoch für Laden")
        if any(p.power_w < 0 for p in phases) and soc <= MIN_SOC_FOR_DISCHARGE:
            r.ok = False
            r.errors.append(f"SoC {soc:.0f}% → zu niedrig für Entladen")

        # Temperature
        temp = sample.get("battery_temperature")
        if temp:
            r.checks.append(f"Bat-Temp: {temp:.1f}°C")
            if temp > MAX_BATTERY_TEMP_C:
                r.ok = False
                r.errors.append(f"Batterie {temp:.1f}°C > {MAX_BATTERY_TEMP_C}°C")

        # Write test — this is the real gate-keeper (not register 1080)
        # If writes succeed, external control is working regardless of mode register
        self._emit("  Schreibtest REG 1034...")
        write_ok = await self._wr("bat_charge_dc_abs_power", 0.0)
        if write_ok:
            await asyncio.sleep(0.5)
            rb = await self._rd("bat_charge_dc_abs_power")
            r.checks.append(f"Schreibtest REG 1034: OK (readback={rb})")

            # Also test REG 1038/1040 (used for discharge blocking)
            w1038 = await self._wr("bat_max_charge_limit", 20000.0)
            w1040 = await self._wr("bat_max_discharge_limit", 20000.0)
            if w1038 and w1040:
                r.checks.append("Schreibtest REG 1038/1040: OK")
            else:
                r.checks.append("⚠️  REG 1038/1040 nicht beschreibbar (Test läuft trotzdem)")
        else:
            r.ok = False
            r.errors.append(
                "REG 1034 nicht beschreibbar! Externe Batteriesteuerung muss "
                "im WebUI unter Service → Batterie aktiviert sein."
            )

        return r

    # ------------------------------------------------------------------
    # Phase execution
    # ------------------------------------------------------------------

    async def _run_phase(self, phase: TestPhase) -> PhaseResult:
        res = PhaseResult(phase=phase, success=False)
        start = time.monotonic()
        cycle = 0

        # Initial write
        if phase.power_w > 0:
            ok = await self._write_charge(phase.power_w)
        else:
            ok = await self._write_discharge(phase.power_w)
        if not ok:
            res.abort_reason = "REG 1034 Schreibfehler"
            return res
        res.keepalive_writes = 1
        last_write = time.monotonic()

        # Readback
        await asyncio.sleep(2)
        rb1034 = await self._rd("bat_charge_dc_abs_power")
        rb_cd = await self._rd("battery_cd_power")
        self._emit(f"  🔍 Readback: REG1034={rb1034} | bat_cd_power={rb_cd}")

        power_samples: list[float] = []

        while True:
            elapsed = time.monotonic() - start
            if elapsed >= phase.duration_s:
                break
            if self._abort_requested:
                res.abort_reason = "Abbruch"
                return res

            cycle += 1

            # ── KEEPALIVE FIRST ──
            since_write = time.monotonic() - last_write
            if since_write >= KEEPALIVE_INTERVAL:
                ok = await self._keepalive(phase)
                res.keepalive_writes += 1
                last_write = time.monotonic()
                self._emit(f"  🔁 KA#{res.keepalive_writes} @{elapsed:.0f}s ({since_write:.0f}s seit letztem Write) {'✅' if ok else '⚠️'}")

            # ── MONITOR ──
            full = (cycle % 3 == 0)
            s = await self._read_sample(full=full)
            res.samples.append(s)

            raw_cd = s.get("battery_cd_power", 0)
            user_p = -raw_cd  # Kostal neg=charge → user pos=charge
            power_samples.append(user_p)

            soc = s.get("battery_soc") or s.get("battery_state_of_charge") or 0
            grid = s.get("pm_total_active", 0)
            temp = s.get("battery_temperature")
            ttw = KEEPALIVE_INTERVAL - (time.monotonic() - last_write)

            t_str = f"T={temp:.0f}°C " if temp else ""
            elapsed = time.monotonic() - start
            self._emit(
                f"  📊 {elapsed:5.0f}s │ "
                f"Bat={user_p:+6.0f}W (raw:{raw_cd:+.0f}) │ "
                f"Grid={grid:+7.0f}W │ "
                f"SoC={soc:5.1f}% │ {t_str}"
                f"nxtWR:{ttw:.0f}s"
            )

            # ── SAFETY ──
            if phase.power_w > 0 and soc >= 99:
                res.abort_reason = f"Batterie voll ({soc:.0f}%)"
                return res
            if phase.power_w < 0 and soc <= MIN_SOC_FOR_DISCHARGE:
                res.abort_reason = f"Batterie leer ({soc:.0f}%)"
                return res
            if temp and temp > MAX_BATTERY_TEMP_C:
                res.abort_reason = f"Temp {temp:.1f}°C"
                return res
            inv_st = s.get("inverter_state")
            if inv_st is not None:
                try:
                    if int(inv_st) in (0, 1, 10, 15):
                        res.abort_reason = f"WR off (state={int(inv_st)})"
                        return res
                except (TypeError, ValueError):
                    pass

            # ── SLEEP (capped to not miss keepalive) ──
            ttw2 = KEEPALIVE_INTERVAL - (time.monotonic() - last_write)
            remain = phase.duration_s - (time.monotonic() - start)
            slp = max(1.0, min(MONITOR_INTERVAL, ttw2 - 2.0, remain))
            await asyncio.sleep(slp)

        res.success = True
        res.duration_actual_s = time.monotonic() - start

        ev = power_samples[RAMP_UP_SAMPLES:] if len(power_samples) > RAMP_UP_SAMPLES + 1 else power_samples
        if ev:
            res.avg_actual_power = sum(ev) / len(ev)
        if phase.power_w != 0:
            dev = abs(res.avg_actual_power - phase.power_w) / abs(phase.power_w) * 100
            res.power_match = dev <= POWER_TOLERANCE_PCT
            self._emit(f"  Abweichung: {dev:.0f}% ({'✅' if res.power_match else '⚠️'})")

        return res

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _emit(self, msg: str) -> None:
        self._log.append(msg)
        _LOGGER.info("[BatteryTest] %s", msg)

    def _flush_debug(self) -> None:
        try:
            with open(self._debug_path, "w", encoding="utf-8") as f:
                f.write("\n".join(self._dbg))
            self._emit(f"  📄 Debug: {self._debug_path} ({len(self._dbg)} Zeilen)")
        except Exception as e:
            self._emit(f"  ⚠️  Debug-Log: {e}")

    def _summary(self, results: list[PhaseResult]) -> str:
        L = ["ERGEBNIS (evcc-kompatible Strategie)", ""]
        for r in results:
            ic = "✅" if r.success and r.power_match else "⚠️" if r.success else "❌"
            dv = ""
            if r.success and r.phase.power_w != 0:
                d = abs(r.avg_actual_power - r.phase.power_w) / abs(r.phase.power_w) * 100
                dv = f" ({d:.0f}% Abw)"
            ab = f" → {r.abort_reason}" if r.abort_reason else ""
            L.append(f"  {ic} {r.phase.name}: Soll {r.phase.power_w:+d}W → Ist {r.avg_actual_power:+.0f}W{dv}{ab}")
            L.append(f"     KA: {r.keepalive_writes}x | {r.duration_actual_s:.0f}s")

        ok = sum(1 for r in results if r.success and r.power_match)
        L.append(f"\n{ok}/{len(results)} Phasen OK")
        L.append(f"\n📄 Debug: {self._debug_path}")
        L.append("\nFür eigene Automationen (evcc-kompatibel):")
        L.append("  Laden:    REG 1034 = -Watt (z.B. -5000 für 5kW)")
        L.append("  Entladen: REG 1034 = +Watt, REG 1038 = 0")
        L.append("  Normal:   REG 1034 = 0")
        L.append(f"  Keepalive: alle {KEEPALIVE_INTERVAL:.0f}s neu schreiben!")
        return "\n".join(L)

    async def _notify(self, title: str, msg: str) -> None:
        if not self._hass:
            return
        try:
            await self._hass.services.async_call(
                "persistent_notification", "create",
                {"title": f"🔋 {title}", "message": msg,
                 "notification_id": "kostal_battery_test"},
            )
        except Exception:
            pass
