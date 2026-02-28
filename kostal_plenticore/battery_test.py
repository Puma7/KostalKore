"""Safe battery charge/discharge test suite with direct register I/O.

Performs structured charge/discharge tests with comprehensive safety
checks, real-time DIRECT register monitoring, and detailed logging.

Kostal G3 battery control:
    The inverter has a DEADMAN SWITCH: control register values expire
    if not re-written within ~60s. This suite re-writes every 30s.

    Primary control: Register 1034 (bat_charge_dc_abs_power)
        +value = charge battery at X watts (grid → battery)
        -value = discharge battery at X watts (battery → grid)
        0 = automatic mode

    The value is INDEPENDENT of house consumption. The inverter manages
    grid exchange internally. "charge 5000W" means the battery charges
    at up to 5kW – the inverter pulls the needed power from the grid
    on top of whatever the house already draws.

    Supplementary limits: Register 1280/1282 (g3_max_charge/discharge)
        These cap the maximum rate. Set to test power or higher.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Final

from .modbus_coordinator import ModbusDataUpdateCoordinator
from .modbus_registers import (
    REG_BAT_CHARGE_DC_ABS_POWER,
    REG_BATTERY_CHARGE_DISCHARGE_POWER,
    REG_G3_MAX_CHARGE,
    REG_G3_MAX_DISCHARGE,
    REG_INVERTER_MAX_POWER,
    REGISTER_BY_NAME,
)

_LOGGER: Final = logging.getLogger(__name__)

KEEPALIVE_INTERVAL: Final[float] = 30.0
MONITOR_INTERVAL: Final[float] = 10.0
MIN_SOC_FOR_DISCHARGE: Final[float] = 10.0
MAX_SOC_FOR_CHARGE: Final[float] = 98.0
MAX_BATTERY_TEMP_C: Final[float] = 48.0
MIN_ISOLATION_KOHM: Final[float] = 200.0
GRID_BREAKER_LIMIT_W: Final[int] = 25_000
POWER_TOLERANCE_PCT: Final[float] = 50.0
RAMP_UP_SAMPLES: Final[int] = 3

# Registers to read directly during monitoring (not from cache)
MONITOR_REGS: Final[list[str]] = [
    "battery_cd_power",
    "battery_soc",
    "battery_temperature",
    "battery_voltage",
    "pm_total_active",
    "total_ac_power",
    "controller_temp",
    "isolation_resistance",
    "inverter_state",
    "home_from_battery",
    "home_from_grid",
]


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
              "Batterie wird mit 1 kW aus dem Netz geladen"),
    TestPhase("Netzladung 5 kW", 5000, 180,
              "Batterie wird mit 5 kW aus dem Netz geladen"),
    TestPhase("Netzentladung 1 kW", -1000, 300,
              "Batterie entlädt mit 1 kW ins Netz"),
    TestPhase("Netzentladung 5 kW", -5000, 180,
              "Batterie entlädt mit 5 kW ins Netz"),
]


class BatteryTestSuite:
    """Orchestrates safe battery charge/discharge tests."""

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

    @property
    def running(self) -> bool:
        return self._running

    @property
    def log_lines(self) -> list[str]:
        return list(self._log)

    def request_abort(self) -> None:
        self._abort_requested = True

    # ------------------------------------------------------------------
    # Direct register I/O (bypasses coordinator cache)
    # ------------------------------------------------------------------

    async def _direct_read(self, reg_name: str) -> Any:
        """Read a single register directly from the inverter (not from cache)."""
        reg = REGISTER_BY_NAME.get(reg_name)
        if reg is None:
            return None
        try:
            return await self._coord.client.read_register(reg)
        except Exception:
            return None

    async def _direct_read_sample(self) -> dict[str, Any]:
        """Read all monitoring registers directly from the inverter."""
        sample: dict[str, Any] = {"timestamp": time.time()}
        for name in MONITOR_REGS:
            val = await self._direct_read(name)
            if val is not None:
                try:
                    sample[name] = float(val)
                except (TypeError, ValueError):
                    sample[name] = val
        return sample

    async def _direct_write(self, reg_name: str, value: float) -> bool:
        """Write a register and return success."""
        reg = REGISTER_BY_NAME.get(reg_name)
        if reg is None:
            self._emit(f"  ❌ Unbekanntes Register: {reg_name}")
            return False
        try:
            await self._coord.async_write_register(reg, value)
            return True
        except Exception as err:
            self._emit(f"  ❌ Schreibfehler {reg_name}: {err}")
            return False

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def run(self, phases: list[TestPhase] | None = None) -> list[PhaseResult]:
        if self._running:
            raise RuntimeError("Test suite is already running")

        self._running = True
        self._abort_requested = False
        self._log.clear()
        results: list[PhaseResult] = []

        if phases is None:
            phases = list(DEFAULT_PHASES)

        try:
            self._emit("═" * 60)
            self._emit("BATTERIE-TEST-SUITE GESTARTET")
            self._emit("═" * 60)
            self._emit("")
            self._emit("Primäre Steuerung: REG 1034 (bat_charge_dc_abs_power)")
            self._emit("  +Wert = Laden aus Netz, -Wert = Entladen ins Netz")
            self._emit("G3-Limits: REG 1280/1282 (max charge/discharge)")
            self._emit(f"Keepalive: alle {KEEPALIVE_INTERVAL:.0f}s neu schreiben")
            self._emit("Monitoring: DIREKTE Register-Reads (kein Cache)")
            self._emit("")

            # Verify direct read works
            self._emit("Verbindungstest: Lese battery_cd_power direkt...")
            test_val = await self._direct_read("battery_cd_power")
            if test_val is not None:
                self._emit(f"  ✅ Direkter Read OK: battery_cd_power = {test_val}")
            else:
                self._emit("  ⚠️  Direkter Read fehlgeschlagen – nutze Coordinator-Cache")

            preflight = await self._preflight_checks(phases)
            if not preflight.ok:
                self._emit("")
                self._emit("❌ PRE-FLIGHT FEHLGESCHLAGEN")
                for err in preflight.errors:
                    self._emit(f"   ✗ {err}")
                await self._notify(
                    "Batterie-Test abgebrochen",
                    "Pre-Flight fehlgeschlagen:\n" + "\n".join(f"• {e}" for e in preflight.errors),
                )
                return results

            self._emit("")
            self._emit("✅ Pre-Flight bestanden:")
            for chk in preflight.checks:
                self._emit(f"   ✓ {chk}")

            await self._notify(
                "Batterie-Test gestartet",
                f"{len(phases)} Phasen | WR max {preflight.inverter_max_w} W | "
                f"SoC {preflight.battery_soc:.0f}% | Hauslast {preflight.home_load_w:.0f} W",
            )

            for i, phase in enumerate(phases, 1):
                if self._abort_requested:
                    self._emit("⚠️  Abbruch durch Benutzer")
                    break

                self._emit("")
                self._emit(f"{'━' * 60}")
                self._emit(f"PHASE {i}/{len(phases)}: {phase.name}")
                self._emit(f"  Soll: {phase.power_w:+d} W | Dauer: {phase.duration_s}s")
                self._emit(f"  {phase.description}")
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
                    f"Writes: {result.keepalive_writes} | "
                    f"Dauer: {result.duration_actual_s:.0f}s"
                )

                await self._reset_all()
                self._emit("  🔄 Register zurückgesetzt auf Automatik")

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
            await self._notify("Batterie-Test beendet", summary)

        return results

    # ------------------------------------------------------------------
    # Pre-flight
    # ------------------------------------------------------------------

    async def _preflight_checks(self, phases: list[TestPhase]) -> PreFlightResult:
        result = PreFlightResult(ok=True)

        # Read everything directly
        self._emit("")
        self._emit("Pre-Flight: Lese Register direkt vom Wechselrichter...")
        sample = await self._direct_read_sample()

        dev = self._coord.device_info_data or {}

        # Log all raw values
        self._emit("  Aktuelle Werte:")
        for k, v in sorted(sample.items()):
            if k != "timestamp":
                self._emit(f"    {k} = {v}")

        # 1. Inverter max power
        raw_max = dev.get("inverter_max_power")
        if raw_max is not None:
            try:
                result.inverter_max_w = int(raw_max)
            except (TypeError, ValueError):
                pass
        if result.inverter_max_w <= 0:
            result.inverter_max_w = 10000
        result.checks.append(f"WR-Max: {result.inverter_max_w} W")

        max_test = max(abs(p.power_w) for p in phases)
        if max_test > result.inverter_max_w:
            result.ok = False
            result.errors.append(f"Testleistung {max_test} W > WR-Max {result.inverter_max_w} W")

        # 2. Battery HW limits (from coordinator cache since these are slow-poll)
        data = self._coord.data or {}
        for key, attr, label in (
            ("battery_max_charge_hw", "bat_max_charge_w", "Lade"),
            ("battery_max_discharge_hw", "bat_max_discharge_w", "Entlade"),
        ):
            raw = data.get(key)
            if raw is not None:
                try:
                    setattr(result, attr, float(raw))
                    result.checks.append(f"Batterie HW-{label}limit: {float(raw):.0f} W")
                except (TypeError, ValueError):
                    pass

        for phase in phases:
            if phase.power_w > 0 and result.bat_max_charge_w > 0:
                if phase.power_w > result.bat_max_charge_w:
                    result.ok = False
                    result.errors.append(f"'{phase.name}' {phase.power_w} W > HW-Ladelimit {result.bat_max_charge_w:.0f} W")
            if phase.power_w < 0 and result.bat_max_discharge_w > 0:
                if abs(phase.power_w) > result.bat_max_discharge_w:
                    result.ok = False
                    result.errors.append(f"'{phase.name}' {abs(phase.power_w)} W > HW-Entladelimit {result.bat_max_discharge_w:.0f} W")

        # 3. SoC
        soc = sample.get("battery_soc", 0)
        result.battery_soc = soc
        if soc > 0:
            result.checks.append(f"SoC: {soc:.0f}%")
        else:
            result.ok = False
            result.errors.append("SoC nicht lesbar")
        if any(p.power_w > 0 for p in phases) and soc >= MAX_SOC_FOR_CHARGE:
            result.ok = False
            result.errors.append(f"SoC {soc:.0f}% zu hoch für Ladetest")
        if any(p.power_w < 0 for p in phases) and soc <= MIN_SOC_FOR_DISCHARGE:
            result.ok = False
            result.errors.append(f"SoC {soc:.0f}% zu niedrig für Entladetest")

        # 4. Temperature
        temp = sample.get("battery_temperature")
        if temp is not None:
            result.battery_temp = temp
            result.checks.append(f"Batterie-Temp: {temp:.1f}°C")
            if temp > MAX_BATTERY_TEMP_C:
                result.ok = False
                result.errors.append(f"Batterie zu heiß ({temp:.1f}°C)")

        # 5. Grid headroom
        grid = sample.get("home_from_grid", 0)
        result.home_load_w = grid
        result.checks.append(f"Hauslast Netz: {grid:.0f} W")
        for phase in phases:
            if phase.power_w > 0 and grid + phase.power_w > GRID_BREAKER_LIMIT_W:
                result.ok = False
                result.errors.append(f"'{phase.name}' Hauslast+Ladung={grid+phase.power_w:.0f} W > {GRID_BREAKER_LIMIT_W} W")

        # 6. Isolation
        iso = sample.get("isolation_resistance")
        if iso is not None:
            iso_k = iso / 1000.0
            result.checks.append(f"Isolation: {iso_k:.0f} kΩ")
            if iso_k < MIN_ISOLATION_KOHM:
                result.ok = False
                result.errors.append(f"Isolation {iso_k:.0f} kΩ < {MIN_ISOLATION_KOHM} kΩ")

        # 7. Inverter state
        state = sample.get("inverter_state")
        if state is not None:
            try:
                si = int(state)
                result.checks.append(f"WR-Status: {si}")
                if si in (0, 1, 10, 15):
                    result.ok = False
                    result.errors.append(f"WR nicht im Betrieb (State={si})")
            except (TypeError, ValueError):
                pass

        return result

    # ------------------------------------------------------------------
    # Phase execution
    # ------------------------------------------------------------------

    async def _run_phase(self, phase: TestPhase, preflight: PreFlightResult) -> PhaseResult:
        result = PhaseResult(phase=phase, success=False)
        start = time.monotonic()

        # Initial register write
        ok = await self._write_phase_registers(phase)
        if not ok:
            result.abort_reason = "Register-Schreibfehler beim Start"
            return result
        result.keepalive_writes = 1

        # Verify write by reading back
        await asyncio.sleep(2)
        readback = await self._direct_read("battery_cd_power")
        self._emit(f"  🔍 Readback battery_cd_power = {readback}")

        elapsed = 0.0
        power_samples: list[float] = []
        last_keepalive = start

        while elapsed < phase.duration_s:
            if self._abort_requested:
                result.abort_reason = "Benutzer-Abbruch"
                return result

            sleep_time = min(MONITOR_INTERVAL, phase.duration_s - elapsed)
            await asyncio.sleep(sleep_time)
            elapsed = time.monotonic() - start

            # Keepalive
            if time.monotonic() - last_keepalive >= KEEPALIVE_INTERVAL:
                ok = await self._write_phase_registers(phase)
                result.keepalive_writes += 1
                last_keepalive = time.monotonic()
                if ok:
                    self._emit(f"  🔁 Keepalive #{result.keepalive_writes} @ {elapsed:.0f}s")
                else:
                    self._emit(f"  ⚠️  Keepalive #{result.keepalive_writes} TEILWEISE FEHLGESCHLAGEN")

            # DIRECT register reads for monitoring
            sample = await self._direct_read_sample()
            result.samples.append(sample)

            bat_power = sample.get("battery_cd_power", 0)
            power_samples.append(bat_power)
            soc = sample.get("battery_soc", 0)
            temp = sample.get("battery_temperature")
            grid = sample.get("pm_total_active", 0)
            bat_v = sample.get("battery_voltage")
            home_bat = sample.get("home_from_battery", 0)
            home_grid = sample.get("home_from_grid", 0)

            t_str = f"BatT={temp:.0f}°C " if temp is not None else ""
            v_str = f"BatV={bat_v:.0f}V " if bat_v is not None else ""

            self._emit(
                f"  📊 {elapsed:5.0f}s │ "
                f"BatP={bat_power:+6.0f}W │ "
                f"Grid={grid:+7.0f}W │ "
                f"SoC={soc:5.1f}% │ "
                f"{t_str}{v_str}"
                f"HomeBat={home_bat:+.0f}W HomeGrid={home_grid:.0f}W"
            )

            # Live safety
            abort = self._live_safety_check(phase, sample)
            if abort:
                result.abort_reason = abort
                return result

        result.success = True
        result.duration_actual_s = time.monotonic() - start

        # Evaluate: skip ramp-up samples
        eval_samples = power_samples[RAMP_UP_SAMPLES:] if len(power_samples) > RAMP_UP_SAMPLES + 1 else power_samples
        if eval_samples:
            result.avg_actual_power = sum(eval_samples) / len(eval_samples)

        target = phase.power_w
        actual = result.avg_actual_power
        if target != 0:
            dev_pct = abs(actual - target) / abs(target) * 100
            result.power_match = dev_pct <= POWER_TOLERANCE_PCT
            self._emit(f"  Abweichung: {dev_pct:.0f}% ({'✅ OK' if result.power_match else '⚠️  ABWEICHUNG'})")
        else:
            result.power_match = True

        return result

    async def _write_phase_registers(self, phase: TestPhase) -> bool:
        """Write control registers. Returns True if primary register succeeded."""
        power = abs(phase.power_w)
        charging = phase.power_w > 0
        all_ok = True

        # PRIMARY: Direct battery charge/discharge command
        direct_val = float(phase.power_w)
        self._emit(f"  📝 REG 1034 bat_charge_dc_abs_power = {direct_val:+.0f} W")
        if not await self._direct_write("bat_charge_dc_abs_power", direct_val):
            all_ok = False

        # SUPPLEMENTARY: G3 limits (best-effort, non-blocking)
        if charging:
            self._emit(f"  📝 REG 1280 g3_max_charge = {power} W")
            await self._direct_write("g3_max_charge", float(power))
            self._emit("  📝 REG 1282 g3_max_discharge = 0 W")
            await self._direct_write("g3_max_discharge", 0.0)
        else:
            self._emit("  📝 REG 1280 g3_max_charge = 0 W")
            await self._direct_write("g3_max_charge", 0.0)
            self._emit(f"  📝 REG 1282 g3_max_discharge = {power} W")
            await self._direct_write("g3_max_discharge", float(power))

        return all_ok

    def _live_safety_check(self, phase: TestPhase, s: dict[str, Any]) -> str | None:
        soc = s.get("battery_soc", 50)
        if phase.power_w > 0 and soc >= 99:
            return f"Batterie voll (SoC {soc:.0f}%)"
        if phase.power_w < 0 and soc <= MIN_SOC_FOR_DISCHARGE:
            return f"Batterie leer (SoC {soc:.0f}%)"
        temp = s.get("battery_temperature")
        if temp is not None and temp > MAX_BATTERY_TEMP_C:
            return f"Batterie zu heiß ({temp:.1f}°C)"
        ctrl = s.get("controller_temp")
        if ctrl is not None and ctrl > 80:
            return f"Controller zu heiß ({ctrl:.1f}°C)"
        iso = s.get("isolation_resistance")
        if iso is not None and iso / 1000.0 < MIN_ISOLATION_KOHM:
            return f"Isolation kritisch ({iso/1000:.0f} kΩ)"
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
        for name, val, desc in (
            ("bat_charge_dc_abs_power", 0.0, "→ 0 (Automatik)"),
            ("g3_max_charge", 20000.0, "→ 20000 W (unbegrenzt)"),
            ("g3_max_discharge", 20000.0, "→ 20000 W (unbegrenzt)"),
        ):
            try:
                reg = REGISTER_BY_NAME.get(name)
                if reg:
                    await self._coord.async_write_register(reg, val)
            except Exception as err:
                self._emit(f"  ⚠️  Reset {name} fehlgeschlagen: {err}")

    # ------------------------------------------------------------------
    # Logging & notifications
    # ------------------------------------------------------------------

    def _emit(self, msg: str) -> None:
        self._log.append(msg)
        _LOGGER.info("[BatteryTest] %s", msg)

    def _build_summary(self, results: list[PhaseResult]) -> str:
        lines = ["ERGEBNIS-ZUSAMMENFASSUNG", ""]
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
        lines.append("")
        lines.append("Steuerung für eigene Automationen:")
        lines.append("  Entity: number.XXX_battery_charge_power_modbus")
        lines.append("    +Wert (W) = Laden aus Netz")
        lines.append("    -Wert (W) = Entladen ins Netz")
        lines.append("    0 = Automatik")
        lines.append(f"  ⚠️  Wert muss alle {KEEPALIVE_INTERVAL:.0f}s neu geschrieben werden!")
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
