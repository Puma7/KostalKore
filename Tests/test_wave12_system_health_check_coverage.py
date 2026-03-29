"""Coverage tests for system_health_check.py."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers.device_registry import DeviceInfo

import custom_components.kostal_kore.system_health_check as shc


def _device_info() -> DeviceInfo:
    return DeviceInfo(identifiers={(shc.DOMAIN, "serial-1")})


def _client() -> SimpleNamespace:
    return SimpleNamespace(
        get_version=AsyncMock(return_value={"sw": "1.2.3"}),
        get_me=AsyncMock(return_value={"user": "installer"}),
        get_process_data=AsyncMock(return_value={"mod": {"a": 1, "b": 2}}),
        get_settings=AsyncMock(return_value={"set": {"x": 1}}),
    )


def _entry(
    *,
    client: SimpleNamespace | None = None,
    options: dict[str, object] | None = None,
    data: dict[str, object] | None = None,
) -> SimpleNamespace:
    runtime = SimpleNamespace(device_info=_device_info(), client=client or _client())
    return SimpleNamespace(
        entry_id="entry-1",
        runtime_data=runtime,
        options=options or {},
        data=data or {"host": "192.0.2.10", "access_role": "Installer", "installer_access": True},
    )


def _hass(entry: SimpleNamespace, entry_data: dict[str, object] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        data={shc.DOMAIN: {entry.entry_id: entry_data or {}}},
        services=SimpleNamespace(async_call=AsyncMock()),
    )


def _button(
    *,
    entry_data: dict[str, object] | None = None,
    client: SimpleNamespace | None = None,
    options: dict[str, object] | None = None,
    data: dict[str, object] | None = None,
) -> tuple[shc.SystemHealthCheckButton, SimpleNamespace, SimpleNamespace]:
    entry = _entry(client=client, options=options, data=data)
    hass = _hass(entry, entry_data)
    button = shc.SystemHealthCheckButton(entry, hass)
    button.hass = hass
    button.async_write_ha_state = MagicMock()
    return button, hass, entry


def test_health_report_rendering_and_counters() -> None:
    """Report builder should track pass/warn/fail/info counts and serialize correctly."""
    report = shc._HealthReport()
    report.section("Alpha")
    report.check("Passed", True, detail="ok")
    report.check("Warn", False, detail="warn", level="warn")
    report.check("Info", False, detail="info", level="info")
    report.check("Fail", False, detail="fail")

    md = report.to_markdown()
    payload = report.to_json()

    assert report.pass_count == 1
    assert report.warn_count == 1
    assert report.fail_count == 1
    assert report.info_count == 1
    assert "## KOSTAL KORE System Health Check" in md
    assert "### Alpha" in md
    assert payload["summary"] == {"pass": 1, "warn": 1, "fail": 1}
    assert "Alpha" in payload["sections"]
    assert "Fehler" in report.title_summary()

    warn_only = shc._HealthReport()
    warn_only.section("Beta")
    warn_only.check("Warn", False, level="warn")
    assert "Warnungen" in warn_only.title_summary()

    ok_only = shc._HealthReport()
    ok_only.section("Gamma")
    ok_only.check("Ok", True)
    assert "alles OK" in ok_only.title_summary()


@pytest.mark.asyncio
async def test_system_health_check_rest_api_and_async_press_paths() -> None:
    """REST checks and async_press should cover success and notification failure paths."""
    client = _client()
    button, hass, entry = _button(entry_data={}, client=client)
    report = shc._HealthReport()

    await button._check_rest_api(report, entry.runtime_data)
    md = report.to_markdown()
    assert "API-Version" in md
    assert "API-Login" in md
    assert "Prozessdaten verf" in md
    assert "Settings-Daten verf" in md

    timeout_client = SimpleNamespace(
        get_version=AsyncMock(side_effect=RuntimeError("version boom")),
        get_me=AsyncMock(side_effect=RuntimeError("login boom")),
        get_process_data=AsyncMock(side_effect=asyncio.TimeoutError()),
        get_settings=AsyncMock(side_effect=RuntimeError("settings boom")),
    )
    timeout_button, _, timeout_entry = _button(entry_data={}, client=timeout_client)
    timeout_report = shc._HealthReport()
    await timeout_button._check_rest_api(timeout_report, timeout_entry.runtime_data)
    timeout_md = timeout_report.to_markdown()
    assert "version boom" in timeout_md
    assert "login boom" in timeout_md
    assert "Timeout" in timeout_md
    assert "settings boom" in timeout_md

    empty_client = SimpleNamespace(
        get_version=AsyncMock(return_value="v"),
        get_me=AsyncMock(return_value="me"),
        get_process_data=AsyncMock(return_value={}),
        get_settings=AsyncMock(side_effect=asyncio.TimeoutError()),
    )
    empty_button, _, empty_entry = _button(entry_data={}, client=empty_client)
    empty_report = shc._HealthReport()
    await empty_button._check_rest_api(empty_report, empty_entry.runtime_data)
    empty_md = empty_report.to_markdown()
    assert "Keine Module gefunden" in empty_md
    assert "verursacht fehlende Switches/Numbers" in empty_md

    press_button, press_hass, press_entry = _button(entry_data={})

    async def _check_environment(report, plenticore, entry_data):
        report.section("Env")
        report.check("env", True, detail="ok")

    async def _check_rest(report, plenticore):
        report.section("REST")
        report.check("rest", False, detail="warn", level="warn")

    with patch.object(press_button, "_check_environment", side_effect=_check_environment), patch.object(
        press_button, "_check_rest_api", side_effect=_check_rest
    ), patch.object(
        press_button, "_check_modbus_data",
        side_effect=lambda report, entry_data: (report.section("Modbus"), report.check("modbus", False, detail="bad")),
    ), patch.object(
        press_button, "_check_coordinators",
        side_effect=lambda report, entry_data: (report.section("Coord"), report.check("coord", True, detail="ok")),
    ), patch.object(
        press_button, "_check_entity_registry",
        side_effect=lambda report, hass_obj: (report.section("Registry"), report.check("registry", True, detail="ok")),
    ), patch.object(
        press_button, "_check_subsystems",
        side_effect=lambda report, entry_data: (report.section("Subsystems"), report.check("subsys", True, detail="ok")),
    ), patch.object(
        press_button, "_check_known_patterns",
        side_effect=lambda report, entry_data: (report.section("Patterns"), report.check("patterns", True, detail="ok")),
    ):
        await press_button.async_press()

    press_button.async_write_ha_state.assert_called_once()
    call = press_hass.services.async_call.await_args
    assert call.args[:2] == ("persistent_notification", "create")
    assert call.kwargs["blocking"] is True
    payload = call.args[2]
    assert payload["notification_id"] == f"kostal_system_health_{press_entry.entry_id}"
    assert json.loads(press_button.extra_state_attributes["report_json"])["summary"]["fail"] == 1

    failing_button, failing_hass, _ = _button(entry_data={})
    failing_hass.services.async_call.side_effect = RuntimeError("notify failed")
    with patch.object(failing_button, "_check_environment", side_effect=_check_environment), patch.object(
        failing_button, "_check_rest_api", side_effect=_check_rest
    ), patch.object(
        failing_button, "_check_modbus_data", side_effect=lambda report, entry_data: None
    ), patch.object(
        failing_button, "_check_coordinators", side_effect=lambda report, entry_data: None
    ), patch.object(
        failing_button, "_check_entity_registry", side_effect=lambda report, hass_obj: None
    ), patch.object(
        failing_button, "_check_subsystems", side_effect=lambda report, entry_data: None
    ), patch.object(
        failing_button, "_check_known_patterns", side_effect=lambda report, entry_data: None
    ):
        await failing_button.async_press()


def test_system_health_check_environment_registry_subsystems_and_patterns() -> None:
    """Environment-style sections should cover positive, warn and fail paths."""
    modbus_client = SimpleNamespace(
        connected=True,
        host="127.0.0.1",
        port=1502,
        unit_id=71,
        endianness="big",
        unavailable_registers={100, 200},
    )
    modbus_coord = SimpleNamespace(
        client=modbus_client,
        data={
            "generation_energy": 2000.0,
            "total_yield": 100.0,
            "battery_gross_capacity": 1500.0,
            "battery_net_capacity": 0.0,
        },
        last_update_success=True,
    )
    entry_data = {
        "modbus_coordinator": modbus_coord,
        "ksem_coordinator": SimpleNamespace(data={}, last_update_success=False),
        "event_coordinator": SimpleNamespace(data={"evt": 1}, last_update_success=True),
        "health_monitor": SimpleNamespace(
            active_warning_count=SimpleNamespace(samples=[SimpleNamespace(value=2)])
        ),
        "fire_safety": SimpleNamespace(current_risk_level="monitor", alert_count=3),
        "degradation_tracker": object(),
        "soc_controller": SimpleNamespace(active=True),
        "mqtt_bridge": object(),
        "modbus_proxy": object(),
    }

    button, hass, _ = _button(
        entry_data=entry_data,
        options={shc.CONF_MODBUS_ENABLED: True},
    )
    report = shc._HealthReport()
    report.section("Start")

    button._check_coordinators(report, entry_data)
    with patch.object(shc.er, "async_get", return_value=MagicMock()), patch.object(
        shc.er,
        "async_entries_for_config_entry",
        return_value=[
            SimpleNamespace(
                entity_id="sensor.ok",
                disabled_by=None,
                original_name="Ok",
            ),
            SimpleNamespace(
                entity_id="sensor.disabled",
                disabled_by="integration",
                original_name="Disabled",
            ),
        ],
    ):
        button._check_entity_registry(report, hass)
    button._check_subsystems(report, entry_data)
    button._check_known_patterns(report, entry_data)

    md = report.to_markdown()
    assert "Coordinator-Status" in md
    assert "Entity-Registry" in md
    assert "Health Monitor" in md
    assert "Fire Safety" in md
    assert "Degradation Tracker" in md
    assert "Modbus Proxy" in md
    assert "generation_energy" in md
    assert "battery_gross_capacity" in md

    env_report = shc._HealthReport()
    awaitable = button._check_environment(env_report, button._entry.runtime_data, entry_data)
    asyncio.get_event_loop().run_until_complete(awaitable)
    env_md = env_report.to_markdown()
    assert "Integration geladen" in env_md
    assert "Modbus verbunden" in env_md
    assert "Unterdr" in env_md
    assert "Inverter-Host" in env_md
    assert "Zugangsrolle" in env_md

    minimal_button, _, _ = _button(
        entry_data={},
        options={shc.CONF_MODBUS_ENABLED: False},
        data={"host": "?", "access_role": "User", "installer_access": False},
    )
    minimal_report = shc._HealthReport()
    asyncio.get_event_loop().run_until_complete(
        minimal_button._check_environment(minimal_report, None, {})
    )
    minimal_md = minimal_report.to_markdown()
    assert "runtime_data fehlt" in minimal_md
    assert "Modbus aktiviert" in minimal_md
    assert "Inverter-Host" in minimal_md


def test_system_health_check_modbus_data_paths() -> None:
    """Modbus data checks should cover missing, invalid, warn and good branches."""
    button, _, _ = _button(entry_data={})

    no_coord_report = shc._HealthReport()
    button._check_modbus_data(no_coord_report, {})
    assert "nicht aktiviert" in no_coord_report.to_markdown()

    empty_report = shc._HealthReport()
    button._check_modbus_data(
        empty_report, {"modbus_coordinator": SimpleNamespace(data={})}
    )
    assert "erster Poll noch nicht abgeschlossen" in empty_report.to_markdown()

    problem_data = {
        "controller_temp": float("inf"),
        "generation_energy": 5000.0,
        "total_yield": 100.0,
        "isolation_resistance": "bad",
        "inverter_state": "bad",
        "battery_soc": "bad",
    }
    problem_report = shc._HealthReport()
    button._check_modbus_data(
        problem_report,
        {"modbus_coordinator": SimpleNamespace(data=problem_data)},
    )
    problem_md = problem_report.to_markdown()
    assert "NaN/Inf" in problem_md
    assert "Endianness-Problem" in problem_md
    assert "Ung" in problem_md

    warn_report = shc._HealthReport()
    button._check_modbus_data(
        warn_report,
        {
            "modbus_coordinator": SimpleNamespace(
                data={
                    "controller_temp": 20.0,
                    "generation_energy": 100.0,
                    "total_yield": 101.0,
                    "battery_soc": 40.0,
                    "battery_state_of_charge": 41.0,
                    "isolation_resistance": 200_000.0,
                    "inverter_state": 6,
                }
            )
        },
    )
    warn_md = warn_report.to_markdown()
    assert "NIEDRIG" in warn_md
    assert "Batterie SoC" in warn_md
    assert "Inverter-Status" in warn_md

    ok_report = shc._HealthReport()
    button._check_modbus_data(
        ok_report,
        {
            "modbus_coordinator": SimpleNamespace(
                data={
                    "controller_temp": 20.0,
                    "generation_energy": 100.0,
                    "total_yield": 102.0,
                    "battery_soc": 50.0,
                    "battery_state_of_charge": 50.0,
                    "isolation_resistance": 800_000.0,
                    "inverter_state": 6,
                }
            )
        },
    )
    ok_md = ok_report.to_markdown()
    assert "alle plausibel" in ok_md
    assert "Alle Kreuz-Checks bestanden" in ok_md
    assert "0.8 M" in ok_md

    zero_report = shc._HealthReport()
    button._check_modbus_data(
        zero_report,
        {
            "modbus_coordinator": SimpleNamespace(
                data={"generation_energy": 0.0, "total_yield": 0.0}
            )
        },
    )
    assert "Alle Kreuz-Checks bestanden" in zero_report.to_markdown()


def test_system_health_check_remaining_edge_branches() -> None:
    """Cover remaining warning/skip branches across the health-check helpers."""
    button, hass, _ = _button(entry_data={}, options={shc.CONF_MODBUS_ENABLED: True})

    env_report = shc._HealthReport()
    env_entry_data = {
        "modbus_coordinator": SimpleNamespace(
            client=SimpleNamespace(
                connected=False,
                host="198.51.100.10",
                port=502,
                unit_id=1,
                endianness="little",
                unavailable_registers=set(),
            )
        )
    }
    asyncio.run(button._check_environment(env_report, button._entry.runtime_data, env_entry_data))
    assert "keine" in env_report.to_markdown()

    proc_error_client = SimpleNamespace(
        get_version=AsyncMock(return_value="v"),
        get_me=AsyncMock(return_value="me"),
        get_process_data=AsyncMock(side_effect=RuntimeError("proc boom")),
        get_settings=AsyncMock(return_value={"set": {"x": 1}}),
    )
    proc_button, _, proc_entry = _button(entry_data={}, client=proc_error_client)
    proc_report = shc._HealthReport()
    asyncio.run(proc_button._check_rest_api(proc_report, proc_entry.runtime_data))
    assert "proc boom" in proc_report.to_markdown()

    modbus_edge_report = shc._HealthReport()
    button._check_modbus_data(
        modbus_edge_report,
        {
            "modbus_coordinator": SimpleNamespace(
                data={
                    "controller_temp": 150.0,
                    "generation_energy": "broken",
                    "total_yield": 0.0,
                    "battery_state_of_charge": 45.0,
                    "isolation_resistance": None,
                }
            )
        },
    )
    modbus_edge_md = modbus_edge_report.to_markdown()
    assert "erwartet" in modbus_edge_md
    assert "nicht verf" in modbus_edge_md
    assert "45%" in modbus_edge_md

    modbus_critical_report = shc._HealthReport()
    button._check_modbus_data(
        modbus_critical_report,
        {"modbus_coordinator": SimpleNamespace(data={"isolation_resistance": 50_000.0})},
    )
    assert "KRITISCH NIEDRIG" in modbus_critical_report.to_markdown()

    coord_report = shc._HealthReport()
    button._check_coordinators(
        coord_report,
        {
            "modbus_coordinator": None,
            "event_coordinator": SimpleNamespace(data={"ok": 1}, last_update_success=None),
        },
    )
    coord_md = coord_report.to_markdown()
    assert "Event Coordinator" in coord_md
    assert "Daten vorhanden" in coord_md

    registry_report = shc._HealthReport()
    disabled_entries = [
        SimpleNamespace(entity_id=f"sensor.disabled_{idx}", disabled_by="integration", original_name=f"Disabled {idx}")
        for idx in range(12)
    ]
    with patch.object(shc.er, "async_get", return_value=MagicMock()), patch.object(
        shc.er, "async_entries_for_config_entry", return_value=disabled_entries
    ):
        button._check_entity_registry(registry_report, hass)
    registry_md = registry_report.to_markdown()
    assert "und 2 weitere" in registry_md

    registry_empty_report = shc._HealthReport()
    with patch.object(shc.er, "async_get", return_value=MagicMock()), patch.object(
        shc.er, "async_entries_for_config_entry", return_value=[]
    ):
        button._check_entity_registry(registry_empty_report, hass)
    assert "0 Entities" in registry_empty_report.to_markdown()

    subsystem_report = shc._HealthReport()
    button._check_subsystems(
        subsystem_report,
        {
            "health_monitor": SimpleNamespace(active_warning_count=SimpleNamespace(samples=[])),
            "fire_safety": SimpleNamespace(current_risk_level="safe", alert_count=0),
            "soc_controller": SimpleNamespace(active=False),
        },
    )
    subsystem_md = subsystem_report.to_markdown()
    assert "keine Warnungen" in subsystem_md
    assert "Risiko: safe" in subsystem_md
    assert "inaktiv" in subsystem_md

    empty_subsystem_report = shc._HealthReport()
    button._check_subsystems(empty_subsystem_report, {})
    assert "## KOSTAL KORE System Health Check" in empty_subsystem_report.to_markdown()

    known_pattern_report = shc._HealthReport()
    button._check_known_patterns(known_pattern_report, {})
    assert "keine Pr" in known_pattern_report.to_markdown()

    invalid_pattern_report = shc._HealthReport()
    button._check_known_patterns(
        invalid_pattern_report,
        {
            "modbus_coordinator": SimpleNamespace(
                data={
                    "generation_energy": "bad",
                    "total_yield": 10.0,
                    "battery_gross_capacity": "bad",
                    "battery_net_capacity": "bad",
                }
            )
        },
    )
    invalid_pattern_md = invalid_pattern_report.to_markdown()
    assert "generation_energy/total_yield nicht auswertbar" in invalid_pattern_md
    assert "battery_gross_capacity nicht auswertbar" in invalid_pattern_md
    assert "battery_net/gross_capacity nicht auswertbar" in invalid_pattern_md

    ok_pattern_report = shc._HealthReport()
    button._check_known_patterns(
        ok_pattern_report,
        {
            "modbus_coordinator": SimpleNamespace(
                data={
                    "generation_energy": 100.0,
                    "total_yield": 100.0,
                    "battery_gross_capacity": 500.0,
                    "battery_net_capacity": 400.0,
                }
            )
        },
    )
    assert "Keine bekannten Problemmuster erkannt" in ok_pattern_report.to_markdown()

    missing_pattern_report = shc._HealthReport()
    button._check_known_patterns(
        missing_pattern_report,
        {"modbus_coordinator": SimpleNamespace(data={"other": 1})},
    )
    assert "Keine bekannten Problemmuster erkannt" in missing_pattern_report.to_markdown()

    no_flush_report = shc._HealthReport()
    no_flush_report.section("EmptyDetail")
    no_flush_report.check("NoDetail", True)
    no_flush_report.check("InlineDetail", True, detail="inline")
    assert "NoDetail" in no_flush_report.to_markdown()
    assert "**InlineDetail**: inline" in no_flush_report.to_markdown()

    json_report = shc._HealthReport()
    json_report.section("JsonSection")
    json_report.check("JsonCheck", True)
    payload = json_report.to_json()
    assert "JsonSection" in payload["sections"]
