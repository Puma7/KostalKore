"""Comprehensive system health check for KOSTAL KORE.

Creates a detailed diagnostic report covering REST API, Modbus data,
entity registry, coordinator state, and known problem patterns.
Designed to surface integration issues without needing raw log access.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import datetime
from typing import Any, Final

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo

from .const import CONF_MODBUS_ENABLED, DOMAIN
from .coordinator import PlenticoreConfigEntry

_LOGGER: Final = logging.getLogger(__name__)

# Known plausibility ranges for key values (register_name → (min, max, unit))
PLAUSIBILITY_RANGES: Final[dict[str, tuple[float, float, str]]] = {
    "controller_temp": (-20.0, 100.0, "°C"),
    "battery_temperature": (-20.0, 60.0, "°C"),
    "battery_voltage": (0.0, 1000.0, "V"),
    "battery_soc": (0.0, 100.0, "%"),
    "battery_state_of_charge": (0.0, 100.0, "%"),
    "battery_gross_capacity": (0.0, 500.0, "Ah"),
    "grid_frequency": (45.0, 65.0, "Hz"),
    "total_dc_power": (-1000.0, 60000.0, "W"),
    "total_ac_power": (-60000.0, 60000.0, "W"),
    "isolation_resistance": (0.0, 65_000_000.0, "Ohm"),
    "phase1_voltage": (100.0, 280.0, "V"),
    "phase2_voltage": (100.0, 280.0, "V"),
    "phase3_voltage": (100.0, 280.0, "V"),
    "cos_phi": (-1.1, 1.1, ""),
    "home_consumption_rate": (0.0, 200.0, "%"),
    "power_limit_evu": (0.0, 110.0, "%"),
    "battery_cycles": (0.0, 50000.0, ""),
    "daily_yield": (0.0, 500_000.0, "Wh"),
    "total_yield": (0.0, 500_000_000.0, "Wh"),
    "generation_energy": (0.0, 500_000_000.0, "Wh"),
    "inverter_max_power": (100.0, 100_000.0, "W"),
}

# Cross-register consistency checks (a, b, tolerance_description)
CONSISTENCY_CHECKS: Final[list[tuple[str, str, str]]] = [
    ("total_yield", "generation_energy", "total_yield ≈ generation_energy"),
    ("battery_soc", "battery_state_of_charge", "battery_soc ≈ battery_state_of_charge"),
]


class SystemHealthCheckButton(ButtonEntity):
    """Button: run a full-stack health check and output results as notification."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:clipboard-check-outline"
    _attr_has_entity_name = True
    _attr_name = "Run System Health Check"

    def __init__(
        self,
        entry: PlenticoreConfigEntry,
        hass: HomeAssistant,
    ) -> None:
        self._entry = entry
        self._entry_id = entry.entry_id
        self._attr_unique_id = f"{entry.entry_id}_system_health_check"
        self._attr_device_info = entry.runtime_data.device_info
        self._attr_extra_state_attributes: dict[str, Any] = {}

    async def async_press(self) -> None:
        """Run all health checks and create persistent notification."""
        _LOGGER.info("System health check started")
        hass = self.hass
        plenticore = self._entry.runtime_data
        entry_data = hass.data.get(DOMAIN, {}).get(self._entry_id, {})

        report = _HealthReport()

        # Section 1: Integration environment
        await self._check_environment(report, plenticore, entry_data)

        # Section 2: REST API health
        await self._check_rest_api(report, plenticore)

        # Section 3: Modbus data health
        self._check_modbus_data(report, entry_data)

        # Section 4: Coordinator state
        self._check_coordinators(report, entry_data)

        # Section 5: Entity registry consistency
        self._check_entity_registry(report, hass)

        # Section 6: Health / fire safety / degradation subsystems
        self._check_subsystems(report, entry_data)

        # Section 7: Known problem patterns
        self._check_known_patterns(report, entry_data)

        # Build notification
        md = report.to_markdown()
        report_json = report.to_json()

        self._attr_extra_state_attributes = {
            "last_run": datetime.now().isoformat(),
            "pass_count": report.pass_count,
            "warn_count": report.warn_count,
            "fail_count": report.fail_count,
            "report_json": json.dumps(report_json, default=str),
        }
        self.async_write_ha_state()

        try:
            await hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": report.title_summary(),
                    "message": md,
                    "notification_id": f"kostal_system_health_{self._entry_id}",
                },
                blocking=True,
            )
        except Exception as err:
            _LOGGER.warning("Could not create health check notification: %s", err)

        _LOGGER.info(
            "System health check complete: %d pass, %d warn, %d fail",
            report.pass_count,
            report.warn_count,
            report.fail_count,
        )

    # ------------------------------------------------------------------
    # Section implementations
    # ------------------------------------------------------------------

    async def _check_environment(
        self,
        report: _HealthReport,
        plenticore: Any,
        entry_data: dict[str, Any],
    ) -> None:
        """Check integration configuration and runtime state."""
        report.section("Umgebung & Konfiguration")

        report.check(
            "Integration geladen",
            plenticore is not None,
            detail="runtime_data vorhanden" if plenticore else "runtime_data fehlt",
        )

        modbus_enabled = self._entry.options.get(CONF_MODBUS_ENABLED, False)
        modbus_coord = entry_data.get("modbus_coordinator")
        report.check(
            "Modbus aktiviert",
            modbus_enabled,
            detail=f"Coordinator: {'aktiv' if modbus_coord else 'nicht initialisiert'}",
            level="info",
        )

        if modbus_enabled and modbus_coord:
            client = modbus_coord.client
            report.check(
                "Modbus verbunden",
                client.connected,
                detail=f"{client.host}:{client.port} unit={client.unit_id} endianness={client.endianness}",
            )
            suppressed = client.unavailable_registers
            if suppressed:
                report.check(
                    "Unterdrückte Register",
                    False,
                    detail=f"{len(suppressed)} Register unterdrückt: {sorted(suppressed)}",
                    level="warn",
                )
            else:
                report.check("Unterdrückte Register", True, detail="keine")

        host = self._entry.data.get("host", "?")
        report.check(
            "Inverter-Host",
            bool(host and host != "?"),
            detail=str(host),
            level="info",
        )

        role = self._entry.data.get("access_role", "UNKNOWN")
        installer = self._entry.data.get("installer_access", False)
        report.check(
            "Zugangsrolle",
            True,
            detail=f"{role} (Installer-Schreibzugriff: {'ja' if installer else 'nein'})",
            level="info",
        )

    async def _check_rest_api(self, report: _HealthReport, plenticore: Any) -> None:
        """Check REST API connectivity and data freshness."""
        report.section("REST API")

        try:
            version = await asyncio.wait_for(
                plenticore.client.get_version(), timeout=10.0
            )
            report.check("API-Version", True, detail=str(version))
        except Exception as err:
            report.check("API-Version", False, detail=f"Fehler: {err}")

        try:
            me = await asyncio.wait_for(plenticore.client.get_me(), timeout=10.0)
            report.check("API-Login", True, detail=str(me))
        except Exception as err:
            report.check("API-Login", False, detail=f"Fehler: {err}")

        try:
            proc = await asyncio.wait_for(
                plenticore.client.get_process_data(), timeout=15.0
            )
            total_keys = sum(len(v) for v in (proc or {}).values())
            modules = list((proc or {}).keys())
            report.check(
                "Prozessdaten verfügbar",
                total_keys > 0,
                detail=f"{total_keys} Datenpunkte in {len(modules)} Modulen",
            )
            if not proc or total_keys == 0:
                report.check(
                    "Prozessdaten-Module",
                    False,
                    detail="Keine Module gefunden — Inverter antwortet leer",
                )
        except asyncio.TimeoutError:
            report.check(
                "Prozessdaten verfügbar",
                False,
                detail="Timeout (>15s) — Inverter überlastet oder nicht erreichbar",
            )
        except Exception as err:
            report.check("Prozessdaten verfügbar", False, detail=f"Fehler: {err}")

        try:
            settings = await asyncio.wait_for(
                plenticore.client.get_settings(), timeout=15.0
            )
            total_settings = sum(len(v) for v in (settings or {}).values())
            report.check(
                "Settings-Daten verfügbar",
                total_settings > 0,
                detail=f"{total_settings} Einstellungen",
            )
        except asyncio.TimeoutError:
            report.check(
                "Settings-Daten verfügbar",
                False,
                detail="Timeout (>15s) — verursacht fehlende Switches/Numbers",
                level="warn",
            )
        except Exception as err:
            report.check(
                "Settings-Daten verfügbar",
                False,
                detail=f"Fehler: {err}",
                level="warn",
            )

    def _check_modbus_data(
        self, report: _HealthReport, entry_data: dict[str, Any]
    ) -> None:
        """Check Modbus coordinator data for plausibility and consistency."""
        report.section("Modbus Datenqualität")

        coord = entry_data.get("modbus_coordinator")
        if coord is None:
            report.check(
                "Modbus Coordinator",
                True,
                detail="nicht aktiviert — Abschnitt übersprungen",
                level="info",
            )
            return

        data = coord.data or {}
        if not data:
            report.check(
                "Modbus-Daten vorhanden",
                False,
                detail="Coordinator hat keine Daten — erster Poll noch nicht abgeschlossen",
            )
            return

        report.check(
            "Modbus-Daten vorhanden",
            True,
            detail=f"{len(data)} Register gelesen",
        )

        # Plausibility checks
        out_of_range: list[str] = []
        for reg_name, (lo, hi, unit) in PLAUSIBILITY_RANGES.items():
            val = data.get(reg_name)
            if val is None:
                continue
            try:
                fval = float(val)
            except (TypeError, ValueError):
                continue
            if math.isnan(fval) or math.isinf(fval):
                out_of_range.append(f"{reg_name}={fval} (NaN/Inf)")
                continue
            if not (lo <= fval <= hi):
                out_of_range.append(
                    f"{reg_name}={fval:,.1f} {unit} (erwartet {lo:,.0f}–{hi:,.0f})"
                )

        if out_of_range:
            report.check(
                "Plausibilitätsprüfung",
                False,
                detail="Außerhalb erwarteter Bereiche:\n"
                + "\n".join(f"  • {x}" for x in out_of_range),
            )
        else:
            report.check(
                "Plausibilitätsprüfung",
                True,
                detail=f"{len(PLAUSIBILITY_RANGES)} Register geprüft — alle plausibel",
            )

        # Cross-register consistency
        inconsistent: list[str] = []
        for reg_a, reg_b, desc in CONSISTENCY_CHECKS:
            val_a = data.get(reg_a)
            val_b = data.get(reg_b)
            if val_a is None or val_b is None:
                continue
            try:
                fa, fb = float(val_a), float(val_b)
            except (TypeError, ValueError):
                continue
            if fa == 0 and fb == 0:
                continue
            avg = (abs(fa) + abs(fb)) / 2
            if avg > 0 and abs(fa - fb) / avg > 0.05:
                inconsistent.append(
                    f"{desc}: {reg_a}={fa:,.1f} vs {reg_b}={fb:,.1f} "
                    f"(Abweichung {abs(fa - fb) / avg * 100:.1f}%)"
                )

        if inconsistent:
            report.check(
                "Kreuz-Konsistenz",
                False,
                detail="Inkonsistente Register (mögliches Endianness-Problem):\n"
                + "\n".join(f"  • {x}" for x in inconsistent),
            )
        else:
            report.check(
                "Kreuz-Konsistenz",
                True,
                detail="Alle Kreuz-Checks bestanden",
            )

        # Isolation resistance: absent means sentinel was filtered by quality guard
        iso = data.get("isolation_resistance")
        if iso is None:
            report.check(
                "Isolationswiderstand",
                True,
                detail="nicht verfügbar — Firmware-Sentinel gefiltert "
                "(normal bei Nacht/Standby, kein DC-Strom für Messung)",
                level="info",
            )
        else:
            try:
                fiso = float(iso)
                if fiso < 100_000:
                    report.check(
                        "Isolationswiderstand",
                        False,
                        detail=f"{fiso:,.0f} Ω — KRITISCH NIEDRIG (< 100 kΩ)",
                    )
                else:
                    report.check(
                        "Isolationswiderstand",
                        True,
                        detail=f"{fiso:,.0f} Ω",
                    )
            except (TypeError, ValueError):
                pass

        # Inverter state
        inv_state = data.get("inverter_state")
        if inv_state is not None:
            try:
                from .modbus_registers import INVERTER_STATES

                state_name = INVERTER_STATES.get(
                    int(inv_state), f"Unbekannt ({inv_state})"
                )
                report.check(
                    "Inverter-Status",
                    True,
                    detail=state_name,
                    level="info",
                )
            except (TypeError, ValueError):
                report.check(
                    "Inverter-Status",
                    False,
                    detail=f"Ungültiger Wert: {inv_state}",
                    level="warn",
                )

        # Battery SoC
        soc = data.get("battery_soc")
        if soc is None:
            soc = data.get("battery_state_of_charge")
        if soc is not None:
            try:
                fsoc = float(soc)
                report.check(
                    "Batterie SoC", True, detail=f"{fsoc:.0f}%", level="info"
                )
            except (TypeError, ValueError):
                pass

    def _check_coordinators(
        self, report: _HealthReport, entry_data: dict[str, Any]
    ) -> None:
        """Check coordinator data freshness."""
        report.section("Coordinator-Status")

        for name, key in [
            ("Modbus", "modbus_coordinator"),
            ("KSEM", "ksem_coordinator"),
            ("Event", "event_coordinator"),
        ]:
            coord = entry_data.get(key)
            if coord is None:
                continue
            has_data = coord.data is not None and bool(coord.data)
            last_success = getattr(coord, "last_update_success", None)
            detail_parts = []
            if has_data:
                detail_parts.append("Daten vorhanden")
            else:
                detail_parts.append("keine Daten")
            if last_success is not None:
                detail_parts.append(
                    f"letzter Erfolg: {'ja' if last_success else 'nein'}"
                )
            severity = "info" if key == "ksem_coordinator" else "fail"
            report.check(
                f"{name} Coordinator",
                has_data,
                detail=", ".join(detail_parts),
                level=severity,
            )

    def _check_entity_registry(self, report: _HealthReport, hass: HomeAssistant) -> None:
        """Check entity registry for disabled/unavailable entities."""
        report.section("Entity-Registry")

        entity_registry = er.async_get(hass)
        entries = list(
            er.async_entries_for_config_entry(entity_registry, self._entry_id)
        )
        total = len(entries)
        disabled = [e for e in entries if e.disabled_by is not None]
        disabled_by_integration = [
            e for e in disabled if str(e.disabled_by) == "integration"
        ]

        report.check(
            "Registrierte Entities",
            total > 0,
            detail=f"{total} Entities, davon {len(disabled)} deaktiviert",
            level="info",
        )

        if disabled_by_integration:
            names = [
                f"{e.entity_id} ({e.original_name})"
                for e in disabled_by_integration[:10]
            ]
            remaining = len(disabled_by_integration) - 10
            detail = "\n".join(f"  • {n}" for n in names)
            if remaining > 0:
                detail += f"\n  … und {remaining} weitere"
            report.check(
                "Durch Integration deaktiviert",
                True,
                detail=f"{len(disabled_by_integration)} Entities:\n{detail}",
                level="warn",
            )

    def _check_subsystems(
        self, report: _HealthReport, entry_data: dict[str, Any]
    ) -> None:
        """Check health monitor, fire safety, degradation subsystems."""
        report.section("Überwachungs-Subsysteme")

        health: Any = entry_data.get("health_monitor")
        if health is not None:
            warnings = getattr(health, "active_warnings", [])
            report.check(
                "Health Monitor",
                len(warnings) == 0,
                detail=f"{len(warnings)} aktive Warnungen"
                if warnings
                else "keine Warnungen",
                level="warn" if warnings else "fail",
            )

        fire: Any = entry_data.get("fire_safety")
        if fire is not None:
            risk = fire.current_risk_level
            alert_count = fire.alert_count
            report.check(
                "Fire Safety",
                risk == "safe",
                detail=f"Risiko: {risk}, Alerts: {alert_count}",
                level="warn" if risk == "monitor" else "fail",
            )

        degradation: Any = entry_data.get("degradation_tracker")
        if degradation is not None:
            report.check(
                "Degradation Tracker",
                True,
                detail="aktiv",
                level="info",
            )

        soc_ctrl: Any = entry_data.get("soc_controller")
        if soc_ctrl is not None:
            active = getattr(soc_ctrl, "active", False)
            report.check(
                "SoC Controller",
                True,
                detail=f"{'aktiv' if active else 'inaktiv'}",
                level="info",
            )

        mqtt: Any = entry_data.get("mqtt_bridge")
        if mqtt is not None:
            report.check("MQTT Bridge", True, detail="konfiguriert", level="info")

        proxy: Any = entry_data.get("modbus_proxy")
        if proxy is not None:
            report.check("Modbus Proxy", True, detail="konfiguriert", level="info")

    def _check_known_patterns(
        self, report: _HealthReport, entry_data: dict[str, Any]
    ) -> None:
        """Detect known bug patterns from past log analysis."""
        report.section("Bekannte Problemmuster")

        coord = entry_data.get("modbus_coordinator")
        if coord is None or not coord.data:
            report.check(
                "Problemmuster",
                True,
                detail="Modbus nicht aktiv — keine Prüfung möglich",
                level="info",
            )
            return

        data = coord.data
        issues: list[str] = []

        # Pattern: UINT32 endianness issue (generation_energy >> total_yield)
        gen = data.get("generation_energy")
        total = data.get("total_yield")
        if gen is not None and total is not None:
            try:
                fgen, ftotal = float(gen), float(total)
                if fgen > 0 and ftotal > 0 and fgen > ftotal * 10:
                    issues.append(
                        f"generation_energy ({fgen:,.0f}) >> total_yield ({ftotal:,.0f}) "
                        "→ mögliches UINT32 Endianness-Problem"
                    )
            except (TypeError, ValueError):
                pass

        # Pattern: battery_gross_capacity unrealistic (> 1000 Ah)
        cap = data.get("battery_gross_capacity")
        if cap is not None:
            try:
                fcap = float(cap)
                if fcap > 1000:
                    issues.append(
                        f"battery_gross_capacity={fcap:,.0f} Ah unrealistisch "
                        "→ mögliches UINT32 Endianness-Problem"
                    )
            except (TypeError, ValueError):
                pass

        # Pattern: isolation_resistance absent (sentinel filtered by quality guard)
        if "isolation_resistance" not in data:
            dc_power = data.get("total_dc_power")
            try:
                has_dc = dc_power is not None and float(dc_power) > 50
            except (TypeError, ValueError):
                has_dc = False
            if has_dc:
                issues.append(
                    "isolation_resistance fehlt trotz DC-Leistung >50 W "
                    "→ Sentinel-Wert gefiltert, Firmware meldet 0xFFFF"
                )

        # Pattern: battery_net_capacity = 0 while gross > 0
        net_cap = data.get("battery_net_capacity")
        if net_cap is not None and cap is not None:
            try:
                fnet = float(net_cap)
                fgross = float(cap)
                if fgross > 0 and fnet == 0:
                    issues.append(
                        "battery_net_capacity=0 trotz battery_gross_capacity>0 "
                        "→ Register nicht unterstützt auf diesem Modell"
                    )
            except (TypeError, ValueError):
                pass

        if issues:
            report.check(
                "Erkannte Problemmuster",
                False,
                detail="\n".join(f"  ⚠ {x}" for x in issues),
                level="warn",
            )
        else:
            report.check(
                "Erkannte Problemmuster",
                True,
                detail="Keine bekannten Problemmuster erkannt",
            )


# ------------------------------------------------------------------
# Report builder
# ------------------------------------------------------------------


class _HealthReport:
    """Structured health check report builder."""

    def __init__(self) -> None:
        self._sections: list[tuple[str, list[dict[str, Any]]]] = []
        self._current_section: str = ""
        self._current_checks: list[dict[str, Any]] = []
        self.pass_count: int = 0
        self.warn_count: int = 0
        self.fail_count: int = 0

    def section(self, name: str) -> None:
        if self._current_section:
            self._sections.append((self._current_section, self._current_checks))
        self._current_section = name
        self._current_checks = []

    def check(
        self,
        name: str,
        passed: bool,
        *,
        detail: str = "",
        level: str = "fail",
    ) -> None:
        if passed:
            icon = "✅"
            self.pass_count += 1
        elif level == "warn":
            icon = "⚠️"
            self.warn_count += 1
        elif level == "info":
            icon = "ℹ️"
            self.pass_count += 1
        else:
            icon = "❌"
            self.fail_count += 1

        self._current_checks.append(
            {"name": name, "passed": passed, "icon": icon, "detail": detail}
        )

    def title_summary(self) -> str:
        total = self.pass_count + self.warn_count + self.fail_count
        if self.fail_count > 0:
            return f"System Health Check ({total} Checks, {self.fail_count} Fehler, {self.warn_count} Warnungen)"
        if self.warn_count > 0:
            return f"System Health Check ({total} Checks, {self.warn_count} Warnungen)"
        return f"System Health Check ({total} Checks — alles OK ✓)"

    def to_markdown(self) -> str:
        if self._current_section:
            self._sections.append((self._current_section, self._current_checks))
            self._current_section = ""
            self._current_checks = []

        lines: list[str] = []
        lines.append("## KOSTAL KORE System Health Check")
        lines.append(
            f"**Zeitpunkt:** {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
        )
        lines.append(
            f"**Ergebnis:** {self.pass_count} OK, {self.warn_count} Warnungen, {self.fail_count} Fehler"
        )
        lines.append("")

        for section_name, checks in self._sections:
            lines.append(f"### {section_name}")
            for c in checks:
                line = f"{c['icon']} **{c['name']}**"
                if c["detail"]:
                    if "\n" in c["detail"]:
                        line += f"\n{c['detail']}"
                    else:
                        line += f": {c['detail']}"
                lines.append(line)
            lines.append("")

        return "\n".join(lines)

    def to_json(self) -> dict[str, Any]:
        if self._current_section:
            self._sections.append((self._current_section, self._current_checks))
            self._current_section = ""
            self._current_checks = []

        return {
            "timestamp": datetime.now().isoformat(),
            "summary": {
                "pass": self.pass_count,
                "warn": self.warn_count,
                "fail": self.fail_count,
            },
            "sections": {
                name: checks for name, checks in self._sections
            },
        }
