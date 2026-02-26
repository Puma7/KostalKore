"""Per-area diagnostic sensor entities.

Each area (DC Solar, AC Grid, Battery, Inverter, Safety) gets its own
sensor entity that shows the current diagnosis status and actionable
recommendation as attributes.

The sensor value is the status: ok / hinweis / warnung / kritisch
Attributes contain: title, detail, action, raw_values
"""

from __future__ import annotations

import logging
from typing import Any, Final

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo

from .diagnostics_engine import AreaDiagnosis, DiagnosticsEngine, DiagStatus

_LOGGER: Final = logging.getLogger(__name__)

_AREA_CONFIG: Final[list[dict[str, str]]] = [
    {
        "area": "dc_solar",
        "name": "Diagnose: DC Solaranlage",
        "icon_ok": "mdi:solar-panel",
        "icon_warn": "mdi:solar-panel-large",
        "icon_crit": "mdi:alert",
    },
    {
        "area": "ac_grid",
        "name": "Diagnose: AC Netzanbindung",
        "icon_ok": "mdi:transmission-tower",
        "icon_warn": "mdi:transmission-tower",
        "icon_crit": "mdi:flash-alert",
    },
    {
        "area": "battery",
        "name": "Diagnose: Batterie",
        "icon_ok": "mdi:battery-check",
        "icon_warn": "mdi:battery-alert-variant-outline",
        "icon_crit": "mdi:battery-alert",
    },
    {
        "area": "inverter",
        "name": "Diagnose: Wechselrichter",
        "icon_ok": "mdi:power-plug-outline",
        "icon_warn": "mdi:power-plug-off-outline",
        "icon_crit": "mdi:alert-octagon",
    },
    {
        "area": "safety",
        "name": "Diagnose: Sicherheit",
        "icon_ok": "mdi:shield-check",
        "icon_warn": "mdi:shield-half-full",
        "icon_crit": "mdi:shield-alert",
    },
]


class AreaDiagnosticSensor(SensorEntity):
    """Diagnostic sensor for one subsystem area."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True

    def __init__(
        self,
        engine: DiagnosticsEngine,
        area: str,
        name: str,
        icon_ok: str,
        icon_warn: str,
        icon_crit: str,
        entry_id: str,
        device_info: DeviceInfo,
    ) -> None:
        self._engine = engine
        self._area = area
        self._icon_ok = icon_ok
        self._icon_warn = icon_warn
        self._icon_crit = icon_crit
        self._attr_name = name
        self._attr_unique_id = f"{entry_id}_diag_{area}"
        self._attr_device_info = device_info
        self._last_diagnosis: AreaDiagnosis | None = None

    def _get_diagnosis(self) -> AreaDiagnosis:
        all_diag = self._engine.diagnose_all()
        diag = all_diag.get(self._area)
        if diag is not None:
            self._last_diagnosis = diag
        return self._last_diagnosis or AreaDiagnosis(
            self._area, DiagStatus.OK, "Initialisierung...", "", "", {}
        )

    @property
    def native_value(self) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        return self._get_diagnosis().status

    @property
    def icon(self) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        d = self._get_diagnosis()
        if d.status == DiagStatus.KRITISCH:
            return self._icon_crit
        if d.status in (DiagStatus.WARNUNG, DiagStatus.HINWEIS):
            return self._icon_warn
        return self._icon_ok

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # pyright: ignore[reportIncompatibleVariableOverride]
        d = self._get_diagnosis()
        return {
            "title": d.title,
            "detail": d.detail,
            "action": d.action,
            **{f"raw_{k}": v for k, v in d.raw_values.items()},
        }


def create_diagnostic_sensors(
    engine: DiagnosticsEngine,
    entry_id: str,
    device_info: DeviceInfo,
) -> list[SensorEntity]:
    """Create one diagnostic sensor per area."""
    return [
        AreaDiagnosticSensor(
            engine=engine,
            area=cfg["area"],
            name=cfg["name"],
            icon_ok=cfg["icon_ok"],
            icon_warn=cfg["icon_warn"],
            icon_crit=cfg["icon_crit"],
            entry_id=entry_id,
            device_info=device_info,
        )
        for cfg in _AREA_CONFIG
    ]
