"""Button entities for Modbus management actions.

Provides:
- 'Reset Modbus Registers' button (clears suppressed registers)
- 'Run Modbus Diagnostics' button (full system test with report)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Final

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo

from .modbus_client import ModbusClientError, ModbusPermanentError
from .modbus_coordinator import ModbusDataUpdateCoordinator
from .modbus_registers import (
    ALL_REGISTERS,
    Access,
    BATTERY_MGMT_MODES,
    BATTERY_TYPES,
    INVERTER_STATES,
    RegisterGroup,
)

_LOGGER: Final = logging.getLogger(__name__)


class ModbusResetButton(ButtonEntity):
    """Button to reset suppressed Modbus registers."""

    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:restart"
    _attr_has_entity_name = True
    _attr_name = "Reset Modbus Registers"

    def __init__(
        self,
        coordinator: ModbusDataUpdateCoordinator,
        entry_id: str,
        device_info: DeviceInfo,
    ) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry_id}_modbus_reset_registers"
        self._attr_device_info = device_info

    async def async_press(self) -> None:
        """Handle button press -- reset all suppressed registers."""
        client = self._coordinator.client
        suppressed_count = len(client.unavailable_registers)
        client.reset_unavailable()
        _LOGGER.info(
            "Modbus register reset: cleared %d suppressed registers, "
            "all registers will be re-polled on next cycle",
            suppressed_count,
        )
        await self._coordinator.async_request_refresh()


class ModbusDiagnosticsButton(ButtonEntity):
    """Button to run full Modbus diagnostics and create HA notification."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:stethoscope"
    _attr_has_entity_name = True
    _attr_name = "Run Modbus Diagnostics"

    def __init__(
        self,
        coordinator: ModbusDataUpdateCoordinator,
        entry_id: str,
        device_info: DeviceInfo,
    ) -> None:
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_modbus_run_diagnostics"
        self._attr_device_info = device_info
        self._attr_extra_state_attributes: dict[str, Any] = {}

    async def async_press(self) -> None:
        """Run full diagnostic test and create persistent notification."""
        _LOGGER.info("Modbus diagnostics started (read-only)")
        client = self._coordinator.client

        report_lines: list[str] = []
        report_data: dict[str, Any] = {"timestamp": datetime.now().isoformat()}

        ok_count = 0
        skip_count = 0
        error_count = 0
        register_results: dict[str, dict[str, Any]] = {}

        report_lines.append("## Modbus Diagnose-Report")
        report_lines.append(f"**Zeitpunkt:** {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
        report_lines.append(f"**Inverter:** {client.host}:{client.port}")
        report_lines.append("")

        for reg in ALL_REGISTERS:
            if reg.access == Access.RW and reg.group in (
                RegisterGroup.CONTROL, RegisterGroup.BATTERY_MGMT,
                RegisterGroup.BATTERY_LIMIT_G3, RegisterGroup.IO_BOARD,
            ):
                skip_count += 1
                continue

            try:
                val = await client.read_register(reg)
                display = self._format(reg.name, val)
                register_results[reg.name] = {
                    "address": reg.address,
                    "value": val if not isinstance(val, float) or val == val else None,
                    "display": display,
                    "unit": reg.unit,
                    "status": "ok",
                }
                ok_count += 1
            except ModbusPermanentError:
                register_results[reg.name] = {"address": reg.address, "status": "not_available"}
                skip_count += 1
            except ModbusClientError as err:
                register_results[reg.name] = {"address": reg.address, "status": "error", "error": str(err)}
                error_count += 1

        groups_order = [
            ("Geräte-Info", RegisterGroup.DEVICE_INFO),
            ("Leistung", RegisterGroup.POWER),
            ("Phasen", RegisterGroup.PHASE),
            ("Batterie", RegisterGroup.BATTERY),
            ("Energie", RegisterGroup.ENERGY),
            ("Powermeter", RegisterGroup.POWERMETER),
        ]

        for group_name, group in groups_order:
            group_regs = {
                name: data for name, data in register_results.items()
                if data.get("status") == "ok"
                and any(r.name == name and r.group == group for r in ALL_REGISTERS)
            }
            if not group_regs:
                continue

            report_lines.append(f"### {group_name}")
            for name, data in group_regs.items():
                unit = data.get("unit") or ""
                report_lines.append(f"- **{name}**: {data['display']} {unit}")
            report_lines.append("")

        unavailable = [
            name for name, data in register_results.items()
            if data.get("status") == "not_available"
        ]
        if unavailable:
            report_lines.append(f"### Nicht verfügbar auf diesem Modell ({len(unavailable)})")
            report_lines.append(", ".join(unavailable))
            report_lines.append("")

        errors = [
            (name, data.get("error", "")) for name, data in register_results.items()
            if data.get("status") == "error"
        ]
        if errors:
            report_lines.append(f"### Fehler ({len(errors)})")
            for name, err_msg in errors:
                report_lines.append(f"- **{name}**: {err_msg}")
            report_lines.append("")

        report_lines.append("### Zusammenfassung")
        report_lines.append(f"- Register OK: **{ok_count}**")
        report_lines.append(f"- Nicht verfügbar/übersprungen: **{skip_count}**")
        report_lines.append(f"- Fehler: **{error_count}**")

        if error_count == 0:
            report_lines.append("")
            report_lines.append("**✓ Alle Tests bestanden**")
        else:
            report_lines.append("")
            report_lines.append(f"**✗ {error_count} Fehler gefunden -- siehe oben**")

        report_text = "\n".join(report_lines)

        report_data["registers"] = register_results
        report_data["summary"] = {
            "ok": ok_count, "skipped": skip_count, "errors": error_count,
        }

        self._attr_extra_state_attributes = {
            "last_run": datetime.now().isoformat(),
            "registers_ok": ok_count,
            "registers_skipped": skip_count,
            "registers_errors": error_count,
            "report_json": json.dumps(report_data, default=str),
        }
        self.async_write_ha_state()

        try:
            await self.hass.services.async_call(
                "persistent_notification", "create",
                {
                    "title": f"Modbus Diagnose ({ok_count} OK, {error_count} Fehler)",
                    "message": report_text,
                    "notification_id": f"kostal_modbus_diag_{self._entry_id}",
                },
            )
            _LOGGER.info(
                "Modbus diagnostics complete: %d OK, %d skipped, %d errors. "
                "Report saved as persistent notification.",
                ok_count, skip_count, error_count,
            )
        except Exception as err:
            _LOGGER.warning("Could not create notification: %s", err)

    @staticmethod
    def _format(name: str, val: Any) -> str:
        if name == "inverter_state" and isinstance(val, int):
            return INVERTER_STATES.get(val, str(val))
        if name == "battery_type" and isinstance(val, int):
            return BATTERY_TYPES.get(val, f"0x{val:04X}")
        if name == "battery_mgmt_mode" and isinstance(val, int):
            return BATTERY_MGMT_MODES.get(val, str(val))
        if isinstance(val, float):
            return f"{val:,.2f}" if abs(val) < 100000 else f"{val:,.0f}"
        return str(val)


class BatteryTestButton(ButtonEntity):
    """Button to run the battery charge/discharge test suite."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:battery-sync"
    _attr_has_entity_name = True
    _attr_name = "Battery Charge/Discharge Test"

    def __init__(
        self,
        coordinator: ModbusDataUpdateCoordinator,
        entry_id: str,
        device_info: DeviceInfo,
    ) -> None:
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_battery_test"
        self._attr_device_info = device_info
        self._suite: Any = None
        self._attr_extra_state_attributes: dict[str, Any] = {
            "status": "idle",
        }

    async def async_press(self) -> None:
        """Start the battery test suite."""
        from .battery_test import BatteryTestSuite

        if self._suite is not None and self._suite.running:
            self._suite.request_abort()
            _LOGGER.info("Battery test abort requested")
            self._attr_extra_state_attributes["status"] = "aborting"
            self.async_write_ha_state()
            return

        self._attr_extra_state_attributes["status"] = "running"
        self._attr_extra_state_attributes["started"] = datetime.now().isoformat()
        self.async_write_ha_state()

        self._suite = BatteryTestSuite(self._coordinator, hass=self.hass)

        try:
            results = await self._suite.run()
            passed = sum(1 for r in results if r.success)
            total = len(results)
            self._attr_extra_state_attributes.update({
                "status": "completed",
                "finished": datetime.now().isoformat(),
                "phases_passed": passed,
                "phases_total": total,
                "log_lines": len(self._suite.log_lines),
            })
        except Exception as err:
            _LOGGER.error("Battery test suite failed: %s", err)
            self._attr_extra_state_attributes.update({
                "status": "error",
                "error": str(err),
            })
        finally:
            self.async_write_ha_state()


def create_modbus_buttons(
    coordinator: ModbusDataUpdateCoordinator,
    entry_id: str,
    device_info: DeviceInfo,
) -> list[ButtonEntity]:
    """Create Modbus management button entities."""
    return [
        ModbusResetButton(coordinator, entry_id, device_info),
        ModbusDiagnosticsButton(coordinator, entry_id, device_info),
        BatteryTestButton(coordinator, entry_id, device_info),
    ]
