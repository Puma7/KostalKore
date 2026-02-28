"""Safe battery charge/discharge test suite.

Performs a structured sequence of charge and discharge operations with
comprehensive pre-flight safety checks, real-time monitoring, and
detailed logging via HA notifications and the system log.

Test phases:
    1. Charge from grid at  1 kW for 5 minutes
    2. Charge from grid at  5 kW for 3 minutes
    3. Discharge to grid at 1 kW for 5 minutes
    4. Discharge to grid at 5 kW for 3 minutes

Kostal G3 battery control strategy:
    The Kostal Plenticore G3 uses a DEADMAN SWITCH (Totmann-Schalter):
    Registers 1280 (g3_max_charge) and 1282 (g3_max_discharge) must be
    re-written cyclically. If the value is not refreshed before the
    fallback timer (register 1288) expires, the inverter reverts to the
    fallback limits (registers 1284/1286).

    For charging:  Set g3_max_charge=TARGET, g3_max_discharge=0
                   → forces all surplus + grid into battery
    For discharging: Set g3_max_discharge=TARGET, g3_max_charge=0
                     → forces battery output, blocks charging

    Additionally, register 1034 (bat_charge_dc_abs_power) is set as
    a direct control hint. Both are refreshed every KEEPALIVE_INTERVAL.

    The values are INDEPENDENT of house consumption. The inverter manages
    the total grid exchange internally. Setting charge=5000W means
    "charge battery with up to 5kW", the inverter handles the rest.

Safety guarantees:
    - Pre-flight: inverter capacity, battery HW limits, SoC bounds,
      battery temperature, home load headroom, isolation resistance
    - Live: continuous monitoring every 10s; automatic abort on violation
    - Cleanup: always resets ALL control registers to defaults
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
    REG_G3_MAX_CHARGE,
    REG_G3_MAX_DISCHARGE,
    REG_G3_MAX_CHARGE_FALLBACK,
    REG_G3_MAX_DISCHARGE_FALLBACK,
    REG_G3_FALLBACK_TIME,
    REG_INVERTER_MAX_POWER,
)

_LOGGER: Final = logging.getLogger(__name__)

KEEPALIVE_INTERVAL: Final[float] = 30.0
MONITOR_INTERVAL: Final[float] = 10.0
SAFETY_MARGIN_WATTS: Final[int] = 500
MIN_SOC_FOR_DISCHARGE: Final[float] = 10.0
MAX_SOC_FOR_CHARGE: Final[float] = 98.0
MAX_BATTERY_TEMP_C: Final[float] = 48.0
MIN_ISOLATION_KOHM: Final[float] = 200.0
GRID_BREAKER_LIMIT_W: Final[int] = 25_000
POWER_TOLERANCE_PCT: Final[float] = 50.0
FALLBACK_TIMER_S: Final[int] = 120


@dataclass
class TestPhase:
    """Definition of a single test phase."""

    name: str
    power_w: int
    duration_s: int
    description: str


@dataclass
class PreFlightResult:
    """Result of pre-flight safety checks."""

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
    """Result of a single test phase execution."""

    phase: TestPhase
    success: bool
    samples: list[dict[str, Any]] = field(default_factory=list)
    abort_reason: str | None = None
    avg_actual_power: float = 0.0
    duration_actual_s: float = 0.0
    power_match: bool = False
    keepalive_writes: int = 0


DEFAULT_PHASES: Final[list[TestPhase]] = [
    TestPhase(
        "Netzladung 1 kW", 1000, 300,
        "Batterie wird mit 1 kW aus dem Netz geladen",
    ),
    TestPhase(
        "Netzladung 5 kW", 5000, 180,
        "Batterie wird mit 5 kW aus dem Netz geladen",
    ),
    TestPhase(
        "Netzentladung 1 kW", -1000, 300,
        "Batterie entlädt mit 1 kW ins Netz",
    ),
    TestPhase(
        "Netzentladung 5 kW", -5000, 180,
        "Batterie entlädt mit 5 kW ins Netz",
    ),
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
            self._emit("Steuerungsstrategie: G3-Register (1280/1282) + DC-Register (1034)")
            self._emit(f"Keepalive-Intervall: {KEEPALIVE_INTERVAL:.0f}s")
            self._emit(f"Fallback-Timer wird auf {FALLBACK_TIMER_S}s gesetzt")
            self._emit("")

            preflight = await self._preflight_checks(phases)
            if not preflight.ok:
                self._emit("❌ PRE-FLIGHT FEHLGESCHLAGEN – Test abgebrochen")
                for err in preflight.errors:
                    self._emit(f"   ✗ {err}")
                await self._notify(
                    "Batterie-Test abgebrochen",
                    "Pre-Flight-Checks fehlgeschlagen:\n"
                    + "\n".join(f"• {e}" for e in preflight.errors),
                )
                return results

            self._emit("✅ Pre-Flight-Checks bestanden:")
            for chk in preflight.checks:
                self._emit(f"   ✓ {chk}")
            self._emit("")

            await self._notify(
                "Batterie-Test gestartet",
                f"{len(phases)} Testphasen werden durchgeführt.\n"
                f"Wechselrichter: max {preflight.inverter_max_w} W\n"
                f"Batterie SoC: {preflight.battery_soc:.0f}%\n"
                f"Hauslast: {preflight.home_load_w:.0f} W",
            )

            for i, phase in enumerate(phases, 1):
                if self._abort_requested:
                    self._emit("⚠️  Abbruch durch Benutzer angefordert")
                    break

                self._emit(f"{'─' * 60}")
                self._emit(f"Phase {i}/{len(phases)}: {phase.name}")
                self._emit(f"  Soll-Leistung: {phase.power_w:+d} W")
                self._emit(f"  Dauer: {phase.duration_s}s")
                self._emit(f"  {phase.description}")
                self._emit("")

                result = await self._run_phase(phase, preflight)
                results.append(result)

                if not result.success:
                    self._emit(f"❌ Phase abgebrochen: {result.abort_reason}")
                    await self._reset_all_registers()
                    break

                match_icon = "✅" if result.power_match else "⚠️"
                self._emit(
                    f"{match_icon} Phase abgeschlossen | "
                    f"Soll: {phase.power_w:+d} W | "
                    f"Ist: {result.avg_actual_power:+.0f} W | "
                    f"Dauer: {result.duration_actual_s:.0f}s | "
                    f"Keepalive-Writes: {result.keepalive_writes}"
                )
                self._emit("")

                await self._reset_all_registers()
                self._emit("  🔄 Register zurückgesetzt")

                if i < len(phases):
                    self._emit("  ⏳ Pause 15s zwischen Phasen...")
                    await asyncio.sleep(15)

        finally:
            await self._reset_all_registers()
            self._running = False

            self._emit("")
            self._emit("═" * 60)
            self._emit("BATTERIE-TEST-SUITE BEENDET")
            summary = self._build_summary(results)
            self._emit(summary)
            self._emit("═" * 60)

            await self._notify("Batterie-Test beendet", summary)

        return results

    # ------------------------------------------------------------------
    # Pre-flight safety checks
    # ------------------------------------------------------------------

    async def _preflight_checks(self, phases: list[TestPhase]) -> PreFlightResult:
        result = PreFlightResult(ok=True)

        await self._coord.async_request_refresh()
        await asyncio.sleep(2)
        data = self._coord.data or {}
        dev = self._coord.device_info_data or {}

        # 1. Inverter max power
        raw_max = dev.get("inverter_max_power") or dev.get(REG_INVERTER_MAX_POWER.name)
        if raw_max is not None:
            try:
                result.inverter_max_w = int(raw_max)
            except (TypeError, ValueError):
                pass
        if result.inverter_max_w <= 0:
            result.inverter_max_w = 10000
            result.checks.append(f"Wechselrichter-Max nicht lesbar → Fallback {result.inverter_max_w} W")
        else:
            result.checks.append(f"Wechselrichter-Maximalleistung: {result.inverter_max_w} W")

        max_test_power = max(abs(p.power_w) for p in phases)
        if max_test_power > result.inverter_max_w:
            result.ok = False
            result.errors.append(
                f"Testleistung ({max_test_power} W) > Wechselrichter ({result.inverter_max_w} W)"
            )

        # 2. Battery hardware limits
        for key, attr, label in (
            ("battery_max_charge_hw", "bat_max_charge_w", "Ladeleistung"),
            ("battery_max_discharge_hw", "bat_max_discharge_w", "Entladeleistung"),
        ):
            raw = data.get(key)
            if raw is not None:
                try:
                    val = float(raw)
                    setattr(result, attr, val)
                    result.checks.append(f"Batterie max. {label} (HW): {val:.0f} W")
                except (TypeError, ValueError):
                    pass

        for phase in phases:
            if phase.power_w > 0 and result.bat_max_charge_w > 0:
                if phase.power_w > result.bat_max_charge_w:
                    result.ok = False
                    result.errors.append(
                        f"'{phase.name}': {phase.power_w} W > HW-Ladelimit {result.bat_max_charge_w:.0f} W"
                    )
            if phase.power_w < 0 and result.bat_max_discharge_w > 0:
                if abs(phase.power_w) > result.bat_max_discharge_w:
                    result.ok = False
                    result.errors.append(
                        f"'{phase.name}': {abs(phase.power_w)} W > HW-Entladelimit {result.bat_max_discharge_w:.0f} W"
                    )

        # 3. Battery SoC
        raw_soc = data.get("battery_soc") or data.get("battery_state_of_charge")
        if raw_soc is not None:
            try:
                result.battery_soc = float(raw_soc)
            except (TypeError, ValueError):
                pass

        if result.battery_soc > 0:
            result.checks.append(f"Batterie-SoC: {result.battery_soc:.0f}%")
        else:
            result.ok = False
            result.errors.append("Batterie-SoC nicht lesbar")

        if any(p.power_w > 0 for p in phases) and result.battery_soc >= MAX_SOC_FOR_CHARGE:
            result.ok = False
            result.errors.append(f"SoC {result.battery_soc:.0f}% ≥ {MAX_SOC_FOR_CHARGE}% → kein Ladetest")
        if any(p.power_w < 0 for p in phases) and result.battery_soc <= MIN_SOC_FOR_DISCHARGE:
            result.ok = False
            result.errors.append(f"SoC {result.battery_soc:.0f}% ≤ {MIN_SOC_FOR_DISCHARGE}% → kein Entladetest")

        # 4. Battery temperature
        raw_temp = data.get("battery_temperature")
        if raw_temp is not None:
            try:
                result.battery_temp = float(raw_temp)
                result.checks.append(f"Batterie-Temperatur: {result.battery_temp:.1f}°C")
                if result.battery_temp > MAX_BATTERY_TEMP_C:
                    result.ok = False
                    result.errors.append(f"Batterie zu heiß ({result.battery_temp:.1f}°C > {MAX_BATTERY_TEMP_C}°C)")
            except (TypeError, ValueError):
                pass

        # 5. Home load / grid headroom
        raw_home = data.get("home_from_grid")
        if raw_home is not None:
            try:
                result.home_load_w = float(raw_home)
            except (TypeError, ValueError):
                pass
        result.checks.append(f"Hauslast vom Netz: {result.home_load_w:.0f} W")

        for phase in phases:
            if phase.power_w > 0:
                total = result.home_load_w + phase.power_w
                if total > GRID_BREAKER_LIMIT_W:
                    result.ok = False
                    result.errors.append(
                        f"'{phase.name}': Hauslast {result.home_load_w:.0f} W + "
                        f"Ladeleistung {phase.power_w} W = {total:.0f} W > Sicherung {GRID_BREAKER_LIMIT_W} W"
                    )

        # 6. Isolation resistance
        raw_iso = data.get("isolation_resistance")
        if raw_iso is not None:
            try:
                iso_kohm = float(raw_iso) / 1000.0
                result.checks.append(f"Isolationswiderstand: {iso_kohm:.0f} kΩ")
                if iso_kohm < MIN_ISOLATION_KOHM:
                    result.ok = False
                    result.errors.append(f"Isolation zu niedrig ({iso_kohm:.0f} kΩ < {MIN_ISOLATION_KOHM} kΩ)")
            except (TypeError, ValueError):
                pass

        # 7. Inverter state
        raw_state = data.get("inverter_state")
        if raw_state is not None:
            try:
                si = int(raw_state)
                if si not in (2, 3, 4, 5, 6, 7, 8, 9):
                    result.ok = False
                    result.errors.append(f"Wechselrichter-Status {si} – nicht im Betrieb")
                else:
                    result.checks.append(f"Wechselrichter-Status: {si} (OK)")
            except (TypeError, ValueError):
                pass

        return result

    # ------------------------------------------------------------------
    # Phase execution with keepalive + live monitoring
    # ------------------------------------------------------------------

    async def _run_phase(
        self, phase: TestPhase, preflight: PreFlightResult
    ) -> PhaseResult:
        result = PhaseResult(phase=phase, success=False)
        start = time.monotonic()

        # Set up the registers
        try:
            await self._set_phase_registers(phase)
            result.keepalive_writes = 1
        except Exception as err:
            result.abort_reason = f"Register-Schreibfehler: {err}"
            return result

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

            # Keepalive: re-write registers periodically
            if time.monotonic() - last_keepalive >= KEEPALIVE_INTERVAL:
                try:
                    await self._set_phase_registers(phase)
                    result.keepalive_writes += 1
                    last_keepalive = time.monotonic()
                    self._emit(
                        f"  🔁 Keepalive #{result.keepalive_writes} "
                        f"(Register neu geschrieben bei {elapsed:.0f}s)"
                    )
                except Exception as err:
                    self._emit(f"  ⚠️  Keepalive fehlgeschlagen: {err}")

            # Read current values
            await self._coord.async_request_refresh()
            await asyncio.sleep(1)
            data = self._coord.data or {}
            sample = self._collect_sample(data)
            result.samples.append(sample)

            actual_power = sample.get("battery_cd_power", 0)
            power_samples.append(actual_power)

            soc = sample.get("soc", 0)
            temp = sample.get("battery_temp")
            grid = sample.get("grid_power", 0)
            bat_v = sample.get("battery_voltage")

            temp_str = f"BatTemp: {temp:.1f}°C | " if temp is not None else ""
            volt_str = f"BatV: {bat_v:.1f}V | " if bat_v is not None else ""

            self._emit(
                f"  📊 t={elapsed:5.0f}s | "
                f"BatPower: {actual_power:+6.0f} W | "
                f"SoC: {soc:5.1f}% | "
                f"{temp_str}{volt_str}"
                f"Netz: {grid:+7.0f} W"
            )

            # Live safety checks
            abort = self._live_safety_check(phase, sample)
            if abort:
                result.abort_reason = abort
                return result

        result.success = True
        result.duration_actual_s = time.monotonic() - start
        if power_samples:
            # Skip first 2 samples (ramp-up time)
            eval_samples = power_samples[2:] if len(power_samples) > 3 else power_samples
            result.avg_actual_power = sum(eval_samples) / len(eval_samples) if eval_samples else 0

        # Check if actual power matches target within tolerance
        target = phase.power_w
        actual = result.avg_actual_power
        if target != 0:
            deviation_pct = abs(actual - target) / abs(target) * 100
            result.power_match = deviation_pct <= POWER_TOLERANCE_PCT
            self._emit(
                f"  Soll/Ist-Abweichung: {deviation_pct:.0f}% "
                f"({'OK' if result.power_match else 'ABWEICHUNG'})"
            )
        else:
            result.power_match = True

        return result

    async def _set_phase_registers(self, phase: TestPhase) -> None:
        """Write all control registers for a phase. Called initially and on keepalive.

        Write order: charge/discharge registers FIRST (critical), then
        fallback timer (optional – some firmware versions reject it).
        """
        power = abs(phase.power_w)
        charging = phase.power_w > 0

        if charging:
            self._emit(f"  📝 REG 1280 g3_max_charge = {power} W")
            await self._coord.async_write_register(REG_G3_MAX_CHARGE, float(power))

            self._emit("  📝 REG 1282 g3_max_discharge = 0 W")
            await self._coord.async_write_register(REG_G3_MAX_DISCHARGE, 0.0)

            self._emit(f"  📝 REG 1034 bat_charge_dc_abs_power = {power} W")
            await self._coord.async_write_register(REG_BAT_CHARGE_DC_ABS_POWER, float(power))
        else:
            self._emit("  📝 REG 1280 g3_max_charge = 0 W")
            await self._coord.async_write_register(REG_G3_MAX_CHARGE, 0.0)

            self._emit(f"  📝 REG 1282 g3_max_discharge = {power} W")
            await self._coord.async_write_register(REG_G3_MAX_DISCHARGE, float(power))

            self._emit(f"  📝 REG 1034 bat_charge_dc_abs_power = -{power} W")
            await self._coord.async_write_register(REG_BAT_CHARGE_DC_ABS_POWER, float(-power))

        # Fallback timer is optional – some firmware versions don't support writing it
        try:
            self._emit(f"  📝 REG 1288 g3_fallback_time = {FALLBACK_TIMER_S}s")
            await self._coord.async_write_register(REG_G3_FALLBACK_TIME, FALLBACK_TIMER_S)
        except Exception as err:
            self._emit(
                f"  ℹ️  REG 1288 g3_fallback_time nicht beschreibbar ({err}) "
                f"– Keepalive kompensiert"
            )

    def _collect_sample(self, data: dict[str, Any]) -> dict[str, Any]:
        sample: dict[str, Any] = {"timestamp": time.time()}
        for key, out in (
            ("battery_cd_power", "battery_cd_power"),
            ("battery_soc", "soc"),
            ("battery_state_of_charge", "soc"),
            ("battery_temperature", "battery_temp"),
            ("battery_voltage", "battery_voltage"),
            ("pm_total_active", "grid_power"),
            ("home_from_grid", "home_from_grid"),
            ("total_ac_power", "ac_power"),
            ("controller_temp", "controller_temp"),
            ("isolation_resistance", "isolation_ohm"),
            ("inverter_state", "inverter_state"),
            ("g3_max_charge", "g3_max_charge"),
            ("g3_max_discharge", "g3_max_discharge"),
            ("g3_fallback_time", "g3_fallback_time"),
        ):
            val = data.get(key)
            if val is not None and out not in sample:
                try:
                    sample[out] = float(val)
                except (TypeError, ValueError):
                    pass
        return sample

    def _live_safety_check(
        self, phase: TestPhase, sample: dict[str, Any],
    ) -> str | None:
        soc = sample.get("soc", 50)
        if phase.power_w > 0 and soc >= 99:
            return f"Batterie voll (SoC {soc:.0f}%)"
        if phase.power_w < 0 and soc <= MIN_SOC_FOR_DISCHARGE:
            return f"Batterie leer (SoC {soc:.0f}%)"

        temp = sample.get("battery_temp")
        if temp is not None and temp > MAX_BATTERY_TEMP_C:
            return f"Batterie zu heiß ({temp:.1f}°C)"

        ctrl_temp = sample.get("controller_temp")
        if ctrl_temp is not None and ctrl_temp > 80:
            return f"Controller zu heiß ({ctrl_temp:.1f}°C)"

        iso = sample.get("isolation_ohm")
        if iso is not None and iso / 1000.0 < MIN_ISOLATION_KOHM:
            return f"Isolationswiderstand kritisch ({iso/1000:.0f} kΩ)"

        state = sample.get("inverter_state")
        if state is not None:
            try:
                si = int(state)
                if si in (0, 1, 10, 15):
                    return f"Wechselrichter abgeschaltet (State={si})"
            except (TypeError, ValueError):
                pass

        return None

    # ------------------------------------------------------------------
    # Cleanup / reset
    # ------------------------------------------------------------------

    async def _reset_all_registers(self) -> None:
        """Reset ALL battery control registers to safe defaults."""
        writes = [
            (REG_BAT_CHARGE_DC_ABS_POWER, 0.0, "1034 bat_charge_dc_abs_power → 0"),
            (REG_G3_MAX_CHARGE, 20000.0, "1280 g3_max_charge → 20000 (unrestricted)"),
            (REG_G3_MAX_DISCHARGE, 20000.0, "1282 g3_max_discharge → 20000 (unrestricted)"),
        ]
        for reg, val, desc in writes:
            try:
                await self._coord.async_write_register(reg, val)
                self._emit(f"  🔄 Reset REG {desc}")
            except Exception as err:
                self._emit(f"  ⚠️  Reset fehlgeschlagen REG {desc}: {err}")
                _LOGGER.error("Battery test reset failed for %s: %s", desc, err)

    # ------------------------------------------------------------------
    # Logging & notifications
    # ------------------------------------------------------------------

    def _emit(self, msg: str) -> None:
        self._log.append(msg)
        _LOGGER.info("[BatteryTest] %s", msg)

    def _build_summary(self, results: list[PhaseResult]) -> str:
        lines = ["Ergebnis-Zusammenfassung:", ""]
        for r in results:
            phase_icon = "✅" if r.success and r.power_match else "⚠️" if r.success else "❌"
            match_info = ""
            if r.success and r.phase.power_w != 0:
                dev_pct = abs(r.avg_actual_power - r.phase.power_w) / abs(r.phase.power_w) * 100
                match_info = f" (Abweichung: {dev_pct:.0f}%)"
            reason = f" ABBRUCH: {r.abort_reason}" if r.abort_reason else ""
            lines.append(
                f"  {phase_icon} {r.phase.name}: "
                f"Soll {r.phase.power_w:+d} W → Ist {r.avg_actual_power:+.0f} W"
                f"{match_info}{reason}"
            )
            lines.append(f"     Keepalive-Writes: {r.keepalive_writes} | Dauer: {r.duration_actual_s:.0f}s")

        passed = sum(1 for r in results if r.success and r.power_match)
        total = len(results)
        lines.append(f"\n{passed}/{total} Phasen erfolgreich (innerhalb {POWER_TOLERANCE_PCT:.0f}% Toleranz)")

        lines.append("")
        lines.append("Register-Referenz:")
        lines.append("  Laden:    REG 1280 (g3_max_charge) = Leistung in W")
        lines.append("            REG 1282 (g3_max_discharge) = 0")
        lines.append("  Entladen: REG 1280 (g3_max_charge) = 0")
        lines.append("            REG 1282 (g3_max_discharge) = Leistung in W")
        lines.append(f"  Timer:    REG 1288 (g3_fallback_time) = {FALLBACK_TIMER_S}s")
        lines.append("  Direkt:   REG 1034 (bat_charge_dc_abs_power)")
        lines.append("            +Wert = Laden, -Wert = Entladen")
        lines.append("  ⚠️  Alle Register müssen zyklisch neu geschrieben werden!")
        lines.append(f"  ⚠️  Keepalive-Intervall: {KEEPALIVE_INTERVAL:.0f}s")

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
        except Exception as err:
            _LOGGER.debug("Notification failed: %s", err)
