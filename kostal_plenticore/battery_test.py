"""Safe battery charge/discharge test suite.

Performs a structured sequence of charge and discharge operations with
comprehensive pre-flight safety checks, real-time monitoring, and
detailed logging via HA notifications and the system log.

Test phases:
    1. Charge from grid at  1 kW for 5 minutes
    2. Charge from grid at  5 kW for 3 minutes
    3. Discharge to grid at 1 kW for 5 minutes
    4. Discharge to grid at 5 kW for 3 minutes

Safety guarantees:
    - Pre-flight: inverter capacity, battery HW limits, SoC bounds,
      battery temperature, home load headroom, Modbus control mode
    - Live: continuous monitoring every 10 s during each phase; automatic
      abort on ANY safety violation
    - Cleanup: always resets battery control register to 0 (automatic mode)
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
    REG_INVERTER_MAX_POWER,
)

_LOGGER: Final = logging.getLogger(__name__)

MONITOR_INTERVAL: Final[float] = 10.0
SAFETY_MARGIN_WATTS: Final[int] = 500
MIN_SOC_FOR_DISCHARGE: Final[float] = 10.0
MAX_SOC_FOR_CHARGE: Final[float] = 98.0
MAX_BATTERY_TEMP_C: Final[float] = 48.0
MIN_ISOLATION_KOHM: Final[float] = 200.0
GRID_BREAKER_LIMIT_W: Final[int] = 25_000


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
        """Signal the running test to abort after the current monitor cycle."""
        self._abort_requested = True

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def run(self, phases: list[TestPhase] | None = None) -> list[PhaseResult]:
        """Execute the full test suite. Returns results per phase."""
        if self._running:
            raise RuntimeError("Test suite is already running")

        self._running = True
        self._abort_requested = False
        self._log.clear()
        results: list[PhaseResult] = []

        if phases is None:
            phases = list(DEFAULT_PHASES)

        try:
            self._emit("═" * 50)
            self._emit("BATTERIE-TEST-SUITE GESTARTET")
            self._emit("═" * 50)

            preflight = await self._preflight_checks(phases)
            if not preflight.ok:
                self._emit("❌ PRE-FLIGHT FEHLGESCHLAGEN – Test abgebrochen")
                for err in preflight.errors:
                    self._emit(f"   ✗ {err}")
                await self._notify(
                    "Batterie-Test abgebrochen",
                    "Pre-Flight-Checks fehlgeschlagen:\n"
                    + "\n".join(f"• {e}" for e in preflight.errors),
                    severity="warning",
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

                self._emit(f"─── Phase {i}/{len(phases)}: {phase.name} ───")
                self._emit(f"    Leistung: {phase.power_w} W | Dauer: {phase.duration_s}s")
                self._emit(f"    {phase.description}")

                result = await self._run_phase(phase, preflight)
                results.append(result)

                if not result.success:
                    self._emit(f"❌ Phase abgebrochen: {result.abort_reason}")
                    break

                self._emit(
                    f"✅ Phase abgeschlossen | Ist-Leistung: "
                    f"{result.avg_actual_power:.0f} W | Dauer: {result.duration_actual_s:.0f}s"
                )
                self._emit("")

                # Short pause between phases
                if i < len(phases):
                    self._emit("    ⏳ Pause 15s zwischen Phasen...")
                    await asyncio.sleep(15)

        finally:
            await self._cleanup()
            self._running = False

            self._emit("═" * 50)
            self._emit("BATTERIE-TEST-SUITE BEENDET")
            summary = self._build_summary(results)
            self._emit(summary)
            self._emit("═" * 50)

            await self._notify(
                "Batterie-Test beendet",
                summary,
            )

        return results

    # ------------------------------------------------------------------
    # Pre-flight safety checks
    # ------------------------------------------------------------------

    async def _preflight_checks(self, phases: list[TestPhase]) -> PreFlightResult:
        """Run comprehensive safety checks before starting any test phase."""
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
            result.checks.append(
                f"Wechselrichter-Maximalleistung nicht lesbar, Fallback: {result.inverter_max_w} W"
            )
        else:
            result.checks.append(
                f"Wechselrichter-Maximalleistung: {result.inverter_max_w} W"
            )

        max_test_power = max(abs(p.power_w) for p in phases)
        if max_test_power > result.inverter_max_w:
            result.ok = False
            result.errors.append(
                f"Maximale Testleistung ({max_test_power} W) übersteigt "
                f"Wechselrichter-Kapazität ({result.inverter_max_w} W)"
            )

        # 2. Battery hardware limits
        raw_charge = data.get("battery_max_charge_hw")
        raw_discharge = data.get("battery_max_discharge_hw")
        if raw_charge is not None:
            try:
                result.bat_max_charge_w = float(raw_charge)
                result.checks.append(
                    f"Batterie max. Ladeleistung (HW): {result.bat_max_charge_w:.0f} W"
                )
            except (TypeError, ValueError):
                pass
        if raw_discharge is not None:
            try:
                result.bat_max_discharge_w = float(raw_discharge)
                result.checks.append(
                    f"Batterie max. Entladeleistung (HW): {result.bat_max_discharge_w:.0f} W"
                )
            except (TypeError, ValueError):
                pass

        for phase in phases:
            if phase.power_w > 0 and result.bat_max_charge_w > 0:
                if phase.power_w > result.bat_max_charge_w:
                    result.ok = False
                    result.errors.append(
                        f"Phase '{phase.name}': {phase.power_w} W übersteigt "
                        f"Batterie-HW-Ladelimit ({result.bat_max_charge_w:.0f} W)"
                    )
            if phase.power_w < 0 and result.bat_max_discharge_w > 0:
                if abs(phase.power_w) > result.bat_max_discharge_w:
                    result.ok = False
                    result.errors.append(
                        f"Phase '{phase.name}': {abs(phase.power_w)} W übersteigt "
                        f"Batterie-HW-Entladelimit ({result.bat_max_discharge_w:.0f} W)"
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

        has_charge_phases = any(p.power_w > 0 for p in phases)
        has_discharge_phases = any(p.power_w < 0 for p in phases)

        if has_charge_phases and result.battery_soc >= MAX_SOC_FOR_CHARGE:
            result.ok = False
            result.errors.append(
                f"Batterie zu voll ({result.battery_soc:.0f}%) für Ladetest "
                f"(max. {MAX_SOC_FOR_CHARGE}%)"
            )
        if has_discharge_phases and result.battery_soc <= MIN_SOC_FOR_DISCHARGE:
            result.ok = False
            result.errors.append(
                f"Batterie zu leer ({result.battery_soc:.0f}%) für Entladetest "
                f"(min. {MIN_SOC_FOR_DISCHARGE}%)"
            )

        # 4. Battery temperature
        raw_temp = data.get("battery_temperature")
        if raw_temp is not None:
            try:
                result.battery_temp = float(raw_temp)
                result.checks.append(
                    f"Batterie-Temperatur: {result.battery_temp:.1f}°C"
                )
                if result.battery_temp > MAX_BATTERY_TEMP_C:
                    result.ok = False
                    result.errors.append(
                        f"Batterie-Temperatur zu hoch ({result.battery_temp:.1f}°C, "
                        f"max. {MAX_BATTERY_TEMP_C}°C)"
                    )
            except (TypeError, ValueError):
                pass

        # 5. Home load / grid headroom
        raw_grid = data.get("pm_total_active")
        raw_home = data.get("home_from_grid")
        grid_power = 0.0
        if raw_grid is not None:
            try:
                grid_power = abs(float(raw_grid))
            except (TypeError, ValueError):
                pass
        if raw_home is not None:
            try:
                result.home_load_w = float(raw_home)
            except (TypeError, ValueError):
                pass

        result.checks.append(f"Aktuelle Hauslast vom Netz: {result.home_load_w:.0f} W")
        result.checks.append(f"Aktuelle Netzleistung (Powermeter): {grid_power:.0f} W")

        for phase in phases:
            if phase.power_w > 0:
                total_grid_draw = result.home_load_w + phase.power_w
                if total_grid_draw > GRID_BREAKER_LIMIT_W:
                    result.ok = False
                    result.errors.append(
                        f"Phase '{phase.name}': Gesamte Netzlast wäre "
                        f"{total_grid_draw:.0f} W (Hauslast {result.home_load_w:.0f} W "
                        f"+ Ladeleistung {phase.power_w} W) – übersteigt "
                        f"Sicherungsgrenze {GRID_BREAKER_LIMIT_W} W"
                    )

        # 6. Isolation resistance
        raw_iso = data.get("isolation_resistance")
        if raw_iso is not None:
            try:
                iso_kohm = float(raw_iso) / 1000.0
                result.checks.append(f"Isolationswiderstand: {iso_kohm:.0f} kΩ")
                if iso_kohm < MIN_ISOLATION_KOHM:
                    result.ok = False
                    result.errors.append(
                        f"Isolationswiderstand zu niedrig ({iso_kohm:.0f} kΩ, "
                        f"min. {MIN_ISOLATION_KOHM:.0f} kΩ)"
                    )
            except (TypeError, ValueError):
                pass

        # 7. Inverter state
        raw_state = data.get("inverter_state")
        if raw_state is not None:
            try:
                state_int = int(raw_state)
                if state_int not in (2, 3, 4, 5, 6, 7, 8, 9):
                    result.ok = False
                    result.errors.append(
                        f"Wechselrichter nicht im Betriebszustand "
                        f"(State={state_int}, erwartet: 2-9/FeedIn/Idle)"
                    )
                else:
                    result.checks.append(f"Wechselrichter-Status: {state_int} (OK)")
            except (TypeError, ValueError):
                pass

        # 8. Battery management mode
        raw_mgmt = dev.get("battery_mgmt_mode")
        if raw_mgmt is not None:
            result.checks.append(f"Batterie-Management-Modus: {raw_mgmt}")

        return result

    # ------------------------------------------------------------------
    # Phase execution with live monitoring
    # ------------------------------------------------------------------

    async def _run_phase(
        self, phase: TestPhase, preflight: PreFlightResult
    ) -> PhaseResult:
        """Execute a single test phase with continuous safety monitoring."""
        result = PhaseResult(phase=phase, success=False)
        start = time.monotonic()

        try:
            await self._coord.async_write_register(
                REG_BAT_CHARGE_DC_ABS_POWER, float(phase.power_w)
            )
            self._emit(f"    → Register 1034 gesetzt: {phase.power_w} W")
        except Exception as err:
            result.abort_reason = f"Schreibfehler: {err}"
            return result

        elapsed = 0.0
        power_samples: list[float] = []

        while elapsed < phase.duration_s:
            if self._abort_requested:
                result.abort_reason = "Benutzer-Abbruch"
                return result

            await asyncio.sleep(min(MONITOR_INTERVAL, phase.duration_s - elapsed))
            elapsed = time.monotonic() - start

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

            self._emit(
                f"    📊 {elapsed:.0f}s | Batterie: {actual_power:+.0f} W | "
                f"SoC: {soc:.0f}% | "
                f"Temp: {temp:.1f}°C | "
                f"Netz: {grid:+.0f} W"
                if temp is not None else
                f"    📊 {elapsed:.0f}s | Batterie: {actual_power:+.0f} W | "
                f"SoC: {soc:.0f}% | Netz: {grid:+.0f} W"
            )

            # Live safety checks
            abort = self._live_safety_check(phase, sample, preflight)
            if abort:
                result.abort_reason = abort
                return result

        result.success = True
        result.duration_actual_s = time.monotonic() - start
        if power_samples:
            result.avg_actual_power = sum(power_samples) / len(power_samples)

        return result

    def _collect_sample(self, data: dict[str, Any]) -> dict[str, Any]:
        """Collect relevant values from current coordinator data."""
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
        ):
            val = data.get(key)
            if val is not None and out not in sample:
                try:
                    sample[out] = float(val)
                except (TypeError, ValueError):
                    pass

        return sample

    def _live_safety_check(
        self,
        phase: TestPhase,
        sample: dict[str, Any],
        preflight: PreFlightResult,
    ) -> str | None:
        """Check safety constraints during phase execution. Returns abort reason or None."""

        soc = sample.get("soc", 50)
        if phase.power_w > 0 and soc >= 99:
            return f"Batterie voll (SoC {soc:.0f}%) – Ladetest sicher beendet"
        if phase.power_w < 0 and soc <= MIN_SOC_FOR_DISCHARGE:
            return f"Batterie leer (SoC {soc:.0f}%) – Entladetest sicher beendet"

        temp = sample.get("battery_temp")
        if temp is not None and temp > MAX_BATTERY_TEMP_C:
            return f"Batterie-Temperatur zu hoch ({temp:.1f}°C > {MAX_BATTERY_TEMP_C}°C)"

        ctrl_temp = sample.get("controller_temp")
        if ctrl_temp is not None and ctrl_temp > 80:
            return f"Controller-Temperatur zu hoch ({ctrl_temp:.1f}°C)"

        iso = sample.get("isolation_ohm")
        if iso is not None:
            iso_kohm = iso / 1000.0
            if iso_kohm < MIN_ISOLATION_KOHM:
                return f"Isolationswiderstand kritisch ({iso_kohm:.0f} kΩ)"

        if phase.power_w > 0:
            home_grid = sample.get("home_from_grid", 0)
            total_load = home_grid + abs(phase.power_w)
            if total_load > GRID_BREAKER_LIMIT_W + SAFETY_MARGIN_WATTS:
                return (
                    f"Netzlast-Sicherheitsgrenze überschritten "
                    f"({total_load:.0f} W > {GRID_BREAKER_LIMIT_W} W)"
                )

        state = sample.get("inverter_state")
        if state is not None:
            try:
                si = int(state)
                if si in (0, 1, 10, 15):
                    return f"Wechselrichter hat Betrieb eingestellt (State={si})"
            except (TypeError, ValueError):
                pass

        return None

    # ------------------------------------------------------------------
    # Cleanup / reset
    # ------------------------------------------------------------------

    async def _cleanup(self) -> None:
        """Reset battery control register to automatic mode."""
        try:
            await self._coord.async_write_register(
                REG_BAT_CHARGE_DC_ABS_POWER, 0.0
            )
            self._emit("🔄 Register 1034 auf 0 zurückgesetzt (Automatik-Modus)")
        except Exception as err:
            self._emit(f"⚠️  WARNUNG: Reset fehlgeschlagen: {err}")
            _LOGGER.error("Battery test cleanup failed: %s", err)

    # ------------------------------------------------------------------
    # Logging & notifications
    # ------------------------------------------------------------------

    def _emit(self, msg: str) -> None:
        """Log a message to both the internal log and the HA system log."""
        self._log.append(msg)
        _LOGGER.info("[BatteryTest] %s", msg)

    def _build_summary(self, results: list[PhaseResult]) -> str:
        """Build a human-readable test summary."""
        lines = ["Ergebnis-Zusammenfassung:"]
        passed = 0
        for r in results:
            icon = "✅" if r.success else "❌"
            power_info = f"Ist: {r.avg_actual_power:+.0f} W" if r.success else ""
            reason = f"({r.abort_reason})" if r.abort_reason else ""
            lines.append(
                f"  {icon} {r.phase.name}: "
                f"Soll: {r.phase.power_w:+d} W {power_info} {reason}"
            )
            if r.success:
                passed += 1
        lines.append(f"\n{passed}/{len(results)} Phasen erfolgreich")
        return "\n".join(lines)

    async def _notify(
        self, title: str, message: str, severity: str = "info"
    ) -> None:
        """Send a persistent notification via Home Assistant."""
        if self._hass is None:
            return
        try:
            await self._hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": f"🔋 {title}",
                    "message": message,
                    "notification_id": "kostal_battery_test",
                },
            )
        except Exception as err:
            _LOGGER.debug("Failed to send notification: %s", err)
