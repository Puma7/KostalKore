"""Coverage tests for battery_test.py."""

from __future__ import annotations

import asyncio
from itertools import count
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

import custom_components.kostal_kore.battery_test as bt


def _coord(device_info_data: dict[str, object] | None = None) -> MagicMock:
    coord = MagicMock()
    coord.client = SimpleNamespace(read_register=AsyncMock())
    coord.async_write_register = AsyncMock()
    coord.device_info_data = device_info_data or {}
    return coord


def _hass() -> SimpleNamespace:
    async def _executor(func, *args):
        return func(*args)

    return SimpleNamespace(
        services=SimpleNamespace(async_call=AsyncMock()),
        async_add_executor_job=AsyncMock(side_effect=_executor),
    )


@pytest.mark.asyncio
async def test_battery_test_helpers_logging_and_notification_paths() -> None:
    """Helper methods should cover read/write, formatting, restore and notify branches."""
    coord = _coord()
    suite = bt.BatteryTestSuite(coord, hass=_hass(), entry_id="entry1")

    assert suite.running is False
    assert suite.log_lines == []
    suite.request_abort()
    assert suite._abort_requested is True

    suite._d("dbg")
    suite._emit("hello")
    assert suite.log_lines[-1] == "hello"

    coord.client.read_register.return_value = 55
    assert await suite._rd("battery_soc") == 55
    coord.client.read_register.side_effect = RuntimeError("boom")
    assert await suite._rd("battery_soc") is None
    coord.client.read_register.side_effect = None
    assert await suite._rd("does_not_exist") is None

    assert await suite._wr("does_not_exist", 1.0) is False
    assert await suite._wr("battery_soc", 1.0) is True
    coord.async_write_register.side_effect = RuntimeError("bad write")
    assert await suite._wr("battery_soc", 1.0) is False
    coord.async_write_register.side_effect = None

    async def _rd_values(name: str):
        return {
            "battery_cd_power": "-1000",
            "battery_soc": "50",
            "battery_temperature": "text",
            "controller_temp": "48.5",
        }.get(name)

    with patch.object(suite, "_rd", side_effect=_rd_values):
        sample_fast = await suite._read_sample()
        sample_full = await suite._read_sample(full=True)
    assert sample_fast["battery_cd_power"] == -1000.0
    assert sample_fast["battery_temperature"] == "text"
    assert sample_full["controller_temp"] == 48.5

    with patch.object(suite, "_wr", new=AsyncMock(return_value=True)) as wr_mock:
        assert await suite._write_charge(1000) is True
        assert await suite._write_discharge(-1200) is True
        suite._original_charge_limit = "321"
        suite._original_discharge_limit = object()
        await suite._write_normal()
        assert await suite._keepalive(bt.TestPhase("charge", 1000, 1, "")) is True
        assert await suite._keepalive(bt.TestPhase("discharge", -1000, 1, "")) is True
    written_names = [call.args[0] for call in wr_mock.await_args_list]
    assert "bat_charge_dc_abs_power" in written_names
    assert "bat_max_charge_limit" in written_names
    assert "bat_max_discharge_limit" in written_names

    suite._dbg = ["line1", "line2"]
    with patch("builtins.open", mock_open()) as open_mock:
        suite._flush_debug_sync()
    open_mock.assert_called_once()

    await suite._flush_debug()
    suite._hass.services.async_call.reset_mock()
    await suite._notify("Title", "Body")
    suite._hass.services.async_call.assert_awaited_once()

    failing_hass = _hass()
    failing_hass.services.async_call.side_effect = RuntimeError("notify fail")
    suite_fail_notify = bt.BatteryTestSuite(coord, hass=failing_hass, entry_id="entry2")
    await suite_fail_notify._notify("Title", "Body")

    no_hass_suite = bt.BatteryTestSuite(coord, hass=None, entry_id="entry3")
    await no_hass_suite._notify("Ignored", "No hass")
    await no_hass_suite._flush_debug()

    summary = suite._summary(
        [
            bt.PhaseResult(
                phase=bt.TestPhase("phase1", 1000, 10, ""),
                success=True,
                avg_actual_power=950,
                power_match=True,
                duration_actual_s=10,
                keepalive_writes=2,
            ),
            bt.PhaseResult(
                phase=bt.TestPhase("phase2", -1000, 10, ""),
                success=False,
                abort_reason="boom",
                duration_actual_s=5,
            ),
        ]
    )
    assert "phase1" in summary
    assert "phase2" in summary


@pytest.mark.asyncio
async def test_battery_test_preflight_success_and_failure_paths() -> None:
    """Preflight should cover happy-path checks and failure accumulation."""
    coord = _coord({"inverter_max_power": "9000"})
    suite = bt.BatteryTestSuite(coord, hass=None, entry_id="entry")

    ok_sample = {
        "battery_soc": 55.0,
        "battery_temperature": 25.0,
    }

    async def _rd_ok(name: str):
        return {
            "battery_mgmt_mode": 2,
            "bat_max_charge_limit": 111.0,
            "bat_max_discharge_limit": 222.0,
            "bat_charge_dc_abs_power": 0.0,
        }.get(name)

    async def _wr_ok(name: str, value: float) -> bool:
        return True

    with patch.object(suite, "_read_sample", new=AsyncMock(return_value=ok_sample)), patch.object(
        suite, "_rd", side_effect=_rd_ok
    ), patch.object(
        suite, "_wr", side_effect=_wr_ok
    ):
        result = await suite._preflight([bt.TestPhase("charge", 1000, 1, "")])
    assert result.ok is True
    assert result.inverter_max_w == 9000
    assert result.battery_soc == 55.0
    assert suite._original_charge_limit == 111.0
    assert suite._original_discharge_limit == 222.0

    coord_bad = _coord({"inverter_max_power": "bad"})
    suite_bad = bt.BatteryTestSuite(coord_bad, hass=None, entry_id="entry")
    bad_sample = {"battery_temperature": 55.0}

    async def _rd_bad(name: str):
        return {
            "battery_mgmt_mode": 0,
            "bat_max_charge_limit": None,
            "bat_max_discharge_limit": None,
        }.get(name)

    with patch.object(suite_bad, "_read_sample", new=AsyncMock(return_value=bad_sample)), patch.object(
        suite_bad, "_rd", side_effect=_rd_bad
    ), patch.object(
        suite_bad, "_wr", new=AsyncMock(return_value=False)
    ):
        result = await suite_bad._preflight(
            [
                bt.TestPhase("charge", 15000, 1, ""),
                bt.TestPhase("discharge", -1000, 1, ""),
            ]
        )
    assert result.ok is False
    assert result.inverter_max_w == 10000
    assert any("Test 15000W > WR 10000W" in error for error in result.errors)
    assert any("SoC nicht lesbar" in error for error in result.errors)
    assert any("nicht beschreibbar" in error for error in result.errors)


@pytest.mark.asyncio
async def test_battery_test_run_phase_success_and_abort_paths() -> None:
    """Phase runner should cover success, initial failure and abort conditions."""
    coord = _coord()
    suite = bt.BatteryTestSuite(coord, hass=None, entry_id="entry")
    real_sleep = asyncio.sleep

    async def _scaled_sleep(delay: float) -> None:
        await real_sleep(0.01 if delay > 0 else 0)

    with patch.object(suite, "_write_charge", new=AsyncMock(return_value=False)):
        failed = await suite._run_phase(bt.TestPhase("charge", 1000, 20, ""))
    assert failed.success is False
    assert failed.abort_reason == "REG 1034 Schreibfehler"

    phase = bt.TestPhase("charge", 1000, 0.05, "")
    with patch.object(suite, "_write_charge", new=AsyncMock(return_value=True)), patch.object(
        suite, "_keepalive", new=AsyncMock(return_value=True)
    ), patch.object(
        suite, "_rd", new=AsyncMock(side_effect=[-1000.0, -950.0])
    ), patch.object(
        suite, "_read_sample",
        new=AsyncMock(return_value={"battery_cd_power": -1000.0, "battery_soc": 50.0, "pm_total_active": 0.0}),
    ), patch.object(bt, "KEEPALIVE_INTERVAL", 0.005), patch.object(
        bt, "MONITOR_INTERVAL", 0.005
    ), patch(
        "custom_components.kostal_kore.battery_test.asyncio.sleep",
        side_effect=_scaled_sleep,
    ):
        success = await suite._run_phase(phase)
    assert success.success is True
    assert success.keepalive_writes >= 1
    assert success.power_match is True

    suite._abort_requested = True
    with patch.object(suite, "_write_charge", new=AsyncMock(return_value=True)), patch.object(
        suite, "_rd", new=AsyncMock(side_effect=[-1000.0, -950.0])
    ), patch.object(
        suite, "_read_sample",
        new=AsyncMock(return_value={"battery_cd_power": -1000.0, "battery_soc": 50.0, "pm_total_active": 0.0}),
    ), patch(
        "custom_components.kostal_kore.battery_test.asyncio.sleep",
        side_effect=_scaled_sleep,
    ):
        aborted = await suite._run_phase(phase)
    assert aborted.abort_reason == "Abbruch"
    suite._abort_requested = False

    keepalive_phase = bt.TestPhase("charge", 1000, 0.08, "")
    with patch.object(suite, "_write_charge", new=AsyncMock(return_value=True)), patch.object(
        suite, "_keepalive", new=AsyncMock(return_value=False)
    ), patch.object(
        suite, "_rd", new=AsyncMock(side_effect=[-1000.0, -1000.0])
    ), patch.object(
        suite, "_read_sample",
        new=AsyncMock(return_value={"battery_cd_power": -1000.0, "battery_soc": 50.0, "pm_total_active": 0.0}),
    ), patch.object(bt, "KEEPALIVE_INTERVAL", 0.005), patch.object(
        bt, "MONITOR_INTERVAL", 0.005
    ), patch(
        "custom_components.kostal_kore.battery_test.asyncio.sleep",
        side_effect=_scaled_sleep,
    ):
        keepalive_fail = await suite._run_phase(keepalive_phase)
    assert "Keepalive" in keepalive_fail.abort_reason

    discharge = bt.TestPhase("discharge", -1000, 0.03, "")
    for sample, expected in [
        ({"battery_cd_power": 1000.0, "battery_soc": 9.0, "pm_total_active": 0.0}, "Batterie leer"),
        ({"battery_cd_power": 1000.0, "battery_soc": 50.0, "battery_temperature": 55.0, "pm_total_active": 0.0}, "Temp 55.0"),
        ({"battery_cd_power": 1000.0, "battery_soc": 50.0, "inverter_state": 10, "pm_total_active": 0.0}, "WR off"),
    ]:
        with patch.object(suite, "_write_discharge", new=AsyncMock(return_value=True)), patch.object(
            suite, "_rd", new=AsyncMock(side_effect=[1000.0, 1000.0])
        ), patch.object(
            suite, "_read_sample", new=AsyncMock(return_value=sample)
        ), patch(
            "custom_components.kostal_kore.battery_test.asyncio.sleep",
            side_effect=_scaled_sleep,
        ):
            result = await suite._run_phase(discharge)
        assert expected in result.abort_reason


@pytest.mark.asyncio
async def test_battery_test_run_orchestration_paths() -> None:
    """Top-level runner should cover preflight fail, skips, success and abort-driven stop."""
    coord = _coord()
    hass = _hass()
    suite = bt.BatteryTestSuite(coord, hass=hass, entry_id="entry")

    suite._running = True
    with pytest.raises(RuntimeError):
        await suite.run()
    suite._running = False

    with patch.object(
        suite,
        "_preflight",
        new=AsyncMock(return_value=bt.PreFlightResult(ok=False, errors=["fatal"])),
    ), patch.object(suite, "_write_normal", new=AsyncMock()), patch.object(
        suite, "_flush_debug", new=AsyncMock()
    ), patch.object(suite, "_notify", new=AsyncMock()) as notify_mock:
        result = await suite.run([bt.TestPhase("charge", 1000, 1, "")])
    assert result == []
    notify_mock.assert_any_await("Test abgebrochen", "• fatal")

    phases = [
        bt.TestPhase("skip discharge", -1000, 1, ""),
        bt.TestPhase("good", 1000, 1, ""),
        bt.TestPhase("bad", 1000, 1, ""),
    ]
    phase_results = [
        bt.PhaseResult(phases[1], success=True, avg_actual_power=1000.0, power_match=True, duration_actual_s=1, keepalive_writes=1),
        bt.PhaseResult(phases[2], success=False, abort_reason="broken"),
    ]

    async def _rd_values(name: str):
        if name == "inverter_state":
            return 10 if len(read_calls) == 1 else 6
        return None

    read_calls: list[str] = []

    async def _rd(name: str):
        read_calls.append(name)
        return await _rd_values(name)

    async def _run_phase(phase: bt.TestPhase):
        result = phase_results.pop(0)
        if phase.name == "good":
            suite._abort_requested = True
        return result

    with patch.object(
        suite,
        "_preflight",
        new=AsyncMock(return_value=bt.PreFlightResult(ok=True, checks=["ok"], battery_soc=55.0)),
    ), patch.object(suite, "_rd", side_effect=_rd), patch.object(
        suite, "_run_phase", side_effect=_run_phase
    ), patch.object(
        suite, "_write_normal", new=AsyncMock()
    ), patch.object(
        suite, "_flush_debug", new=AsyncMock()
    ), patch.object(
        suite, "_notify", new=AsyncMock()
    ), patch(
        "custom_components.kostal_kore.battery_test.asyncio.sleep", new=AsyncMock()
    ):
        result = await suite.run(phases)
    assert len(result) == 2
    assert result[0].success is False
    assert result[0].abort_reason.startswith("WR nicht aktiv")
    assert result[1].success is True
    assert suite.running is False


@pytest.mark.asyncio
async def test_battery_test_edge_branches_and_defaults() -> None:
    """Cover remaining battery-test edge branches and default execution paths."""
    coord = _coord({})
    suite = bt.BatteryTestSuite(coord, hass=None, entry_id="entry")
    real_sleep = asyncio.sleep

    async def _scaled_sleep(delay: float) -> None:
        await real_sleep(0.01 if delay > 0 else 0)

    suite._original_charge_limit = "broken"
    suite._original_discharge_limit = None
    with patch.object(suite, "_wr", new=AsyncMock(return_value=True)):
        await suite._write_normal()
    suite._original_charge_limit = "123"
    suite._original_discharge_limit = "456"
    with patch.object(suite, "_wr", new=AsyncMock(return_value=True)):
        await suite._write_normal()
    suite._original_charge_limit = None
    suite._original_discharge_limit = None
    with patch.object(suite, "_wr", new=AsyncMock(return_value=True)):
        await suite._write_normal()
    suite._original_charge_limit = object()
    suite._original_discharge_limit = 789
    with patch.object(suite, "_wr", new=AsyncMock(return_value=True)):
        await suite._write_normal()

    phase_fail = bt.PhaseResult(bt.TestPhase("phase", 1000, 1, ""), success=False, abort_reason="kaputt")
    with patch.object(
        suite,
        "_preflight",
        new=AsyncMock(return_value=bt.PreFlightResult(ok=True, checks=["ok"], battery_soc=60.0)),
    ), patch.object(
        suite, "_rd", new=AsyncMock(return_value=6)
    ), patch.object(
        suite, "_run_phase", new=AsyncMock(return_value=phase_fail)
    ), patch.object(
        suite, "_write_normal", new=AsyncMock()
    ), patch.object(
        suite, "_flush_debug", new=AsyncMock()
    ), patch.object(
        suite, "_notify", new=AsyncMock()
    ):
        results = await suite.run([bt.TestPhase("only", 1000, 1, "")])
    assert results == [phase_fail]

    async def _preflight_abort(phases: list[bt.TestPhase]) -> bt.PreFlightResult:
        suite._abort_requested = True
        return bt.PreFlightResult(ok=True, checks=["ok"], battery_soc=42.0)

    with patch.object(suite, "_preflight", side_effect=_preflight_abort), patch.object(
        suite, "_write_normal", new=AsyncMock()
    ), patch.object(
        suite, "_flush_debug", new=AsyncMock()
    ), patch.object(
        suite, "_notify", new=AsyncMock()
    ):
        default_results = await suite.run()
    assert default_results == []

    with patch.object(
        suite,
        "_preflight",
        new=AsyncMock(return_value=bt.PreFlightResult(ok=True, checks=["ok"], battery_soc=50.0)),
    ), patch.object(
        suite, "_write_normal", new=AsyncMock()
    ), patch.object(
        suite, "_flush_debug", new=AsyncMock()
    ), patch.object(
        suite, "_notify", new=AsyncMock()
    ):
        empty_results = await suite.run([])
    assert empty_results == []

    high_soc_suite = bt.BatteryTestSuite(_coord({}), hass=None, entry_id="entry")

    async def _rd_high(name: str):
        return {
            "battery_mgmt_mode": 1,
            "bat_max_charge_limit": 100.0,
            "bat_max_discharge_limit": 200.0,
            "bat_charge_dc_abs_power": 0.0,
        }.get(name)

    with patch.object(
        high_soc_suite,
        "_read_sample",
        new=AsyncMock(return_value={"ts": 1.0, "battery_soc": 99.0}),
    ), patch.object(
        high_soc_suite, "_rd", side_effect=_rd_high
    ), patch.object(
        high_soc_suite, "_wr", new=AsyncMock(return_value=True)
    ), patch(
        "custom_components.kostal_kore.battery_test.asyncio.sleep", side_effect=_scaled_sleep
    ):
        high_soc_result = await high_soc_suite._preflight([bt.TestPhase("charge", 1000, 1, "")])
    assert any("Mgmt-Mode: 1" in check for check in high_soc_result.checks)
    assert any("zu hoch" in error for error in high_soc_result.errors)

    low_soc_suite = bt.BatteryTestSuite(_coord({}), hass=None, entry_id="entry")

    async def _rd_low(name: str):
        return {
            "battery_mgmt_mode": None,
            "bat_max_charge_limit": 100.0,
            "bat_max_discharge_limit": 200.0,
            "bat_charge_dc_abs_power": 0.0,
        }.get(name)

    async def _wr_partial(name: str, value: float) -> bool:
        return name != "bat_max_charge_limit"

    with patch.object(
        low_soc_suite,
        "_read_sample",
        new=AsyncMock(return_value={"ts": 1.0, "battery_soc": 5.0}),
    ), patch.object(
        low_soc_suite, "_rd", side_effect=_rd_low
    ), patch.object(
        low_soc_suite, "_wr", side_effect=_wr_partial
    ), patch(
        "custom_components.kostal_kore.battery_test.asyncio.sleep", side_effect=_scaled_sleep
    ):
        low_soc_result = await low_soc_suite._preflight([bt.TestPhase("discharge", -1000, 1, "")])
    assert any("zu niedrig" in error for error in low_soc_result.errors)
    assert any("1038/1040 nicht beschreibbar" in check for check in low_soc_result.checks)

    transition_suite = bt.BatteryTestSuite(_coord({}), hass=None, entry_id="entry")
    success_result = bt.PhaseResult(
        bt.TestPhase("ok", 1000, 1, ""), success=True, avg_actual_power=1000.0, power_match=True, duration_actual_s=1, keepalive_writes=1
    )
    with patch.object(
        transition_suite,
        "_preflight",
        new=AsyncMock(return_value=bt.PreFlightResult(ok=True, checks=["ok"], battery_soc=50.0)),
    ), patch.object(
        transition_suite, "_rd", new=AsyncMock(side_effect=[10, 6])
    ), patch.object(
        transition_suite, "_run_phase", new=AsyncMock(side_effect=[success_result, success_result])
    ), patch.object(
        transition_suite, "_write_normal", new=AsyncMock()
    ), patch.object(
        transition_suite, "_flush_debug", new=AsyncMock()
    ), patch.object(
        transition_suite, "_notify", new=AsyncMock()
    ), patch(
        "custom_components.kostal_kore.battery_test.asyncio.sleep", side_effect=_scaled_sleep
    ):
        transitioned = await transition_suite.run(
            [bt.TestPhase("one", 1000, 1, ""), bt.TestPhase("two", 1000, 1, "")]
        )
    assert len(transitioned) == 2

    parse_suite = bt.BatteryTestSuite(_coord({}), hass=None, entry_id="entry")
    with patch.object(
        parse_suite,
        "_preflight",
        new=AsyncMock(return_value=bt.PreFlightResult(ok=True, checks=["ok"], battery_soc=50.0)),
    ), patch.object(
        parse_suite, "_rd", new=AsyncMock(return_value="bad")
    ), patch.object(
        parse_suite, "_run_phase", new=AsyncMock(return_value=success_result)
    ), patch.object(
        parse_suite, "_write_normal", new=AsyncMock()
    ), patch.object(
        parse_suite, "_flush_debug", new=AsyncMock()
    ), patch.object(
        parse_suite, "_notify", new=AsyncMock()
    ):
        parsed = await parse_suite.run([bt.TestPhase("one", 1000, 1, "")])
    assert len(parsed) == 1

    none_state_suite = bt.BatteryTestSuite(_coord({}), hass=None, entry_id="entry")
    with patch.object(
        none_state_suite,
        "_preflight",
        new=AsyncMock(return_value=bt.PreFlightResult(ok=True, checks=["ok"], battery_soc=50.0)),
    ), patch.object(
        none_state_suite, "_rd", new=AsyncMock(return_value=None)
    ), patch.object(
        none_state_suite, "_run_phase", new=AsyncMock(return_value=success_result)
    ), patch.object(
        none_state_suite, "_write_normal", new=AsyncMock()
    ), patch.object(
        none_state_suite, "_flush_debug", new=AsyncMock()
    ), patch.object(
        none_state_suite, "_notify", new=AsyncMock()
    ):
        none_state_results = await none_state_suite.run([bt.TestPhase("one", 1000, 1, "")])
    assert len(none_state_results) == 1

    nonmatching_state_suite = bt.BatteryTestSuite(_coord({}), hass=None, entry_id="entry")
    with patch.object(
        nonmatching_state_suite,
        "_preflight",
        new=AsyncMock(return_value=bt.PreFlightResult(ok=True, checks=["ok"], battery_soc=50.0)),
    ), patch.object(
        nonmatching_state_suite, "_rd", new=AsyncMock(return_value=6)
    ), patch.object(
        nonmatching_state_suite, "_run_phase", new=AsyncMock(return_value=success_result)
    ), patch.object(
        nonmatching_state_suite, "_write_normal", new=AsyncMock()
    ), patch.object(
        nonmatching_state_suite, "_flush_debug", new=AsyncMock()
    ), patch.object(
        nonmatching_state_suite, "_notify", new=AsyncMock()
    ):
        nonmatching_results = await nonmatching_state_suite.run([bt.TestPhase("one", 1000, 1, "")])
    assert len(nonmatching_results) == 1

    run_phase_suite = bt.BatteryTestSuite(_coord({}), hass=None, entry_id="entry")
    phase = bt.TestPhase("charge", 1000, 5.0, "")
    battery_full_ticks = count()
    with patch.object(run_phase_suite, "_write_charge", new=AsyncMock(return_value=True)), patch.object(
        run_phase_suite, "_rd", new=AsyncMock(side_effect=[-1000.0, -1000.0])
    ), patch.object(
        run_phase_suite,
        "_read_sample",
        new=AsyncMock(return_value={"battery_cd_power": -1000.0, "battery_soc": 99.0, "pm_total_active": 0.0}),
    ), patch(
        "custom_components.kostal_kore.battery_test.time.monotonic",
        side_effect=lambda: float(next(battery_full_ticks)),
    ), patch(
        "custom_components.kostal_kore.battery_test.asyncio.sleep", new=AsyncMock()
    ):
        battery_full = await run_phase_suite._run_phase(phase)
    assert "Batterie voll" in battery_full.abort_reason

    invalid_phase = bt.TestPhase("charge", 1000, 0.05, "")
    with patch.object(run_phase_suite, "_write_charge", new=AsyncMock(return_value=True)), patch.object(
        run_phase_suite, "_rd", new=AsyncMock(side_effect=[-1000.0, -1000.0])
    ), patch.object(
        run_phase_suite,
        "_read_sample",
        new=AsyncMock(return_value={"battery_cd_power": -1000.0, "battery_soc": 50.0, "inverter_state": "bad", "pm_total_active": 0.0}),
    ), patch.object(bt, "KEEPALIVE_INTERVAL", 0.005), patch.object(
        bt, "MONITOR_INTERVAL", 0.005
    ), patch(
        "custom_components.kostal_kore.battery_test.asyncio.sleep", side_effect=_scaled_sleep
    ):
        invalid_state = await run_phase_suite._run_phase(invalid_phase)
    assert invalid_state.success is True
    assert invalid_state.samples

    normal_phase = bt.TestPhase("charge", 1000, 0.05, "")
    with patch.object(run_phase_suite, "_write_charge", new=AsyncMock(return_value=True)), patch.object(
        run_phase_suite, "_rd", new=AsyncMock(side_effect=[-1000.0, -1000.0])
    ), patch.object(
        run_phase_suite,
        "_read_sample",
        new=AsyncMock(return_value={"battery_cd_power": -1000.0, "battery_soc": 50.0, "inverter_state": 6, "pm_total_active": 0.0}),
    ), patch.object(bt, "KEEPALIVE_INTERVAL", 0.005), patch.object(
        bt, "MONITOR_INTERVAL", 0.005
    ), patch(
        "custom_components.kostal_kore.battery_test.asyncio.sleep", side_effect=_scaled_sleep
    ):
        normal_state = await run_phase_suite._run_phase(normal_phase)
    assert normal_state.success is True
    assert normal_state.samples

    monotonic_suite = bt.BatteryTestSuite(_coord({}), hass=None, entry_id="entry")
    monotonic_ticks = count()
    with patch.object(monotonic_suite, "_write_charge", new=AsyncMock(return_value=True)), patch.object(
        monotonic_suite, "_rd", new=AsyncMock(side_effect=[-1000.0, -1000.0])
    ), patch.object(
        monotonic_suite,
        "_read_sample",
        new=AsyncMock(return_value={"battery_cd_power": -1000.0, "battery_soc": 50.0, "inverter_state": 6, "pm_total_active": 0.0}),
    ), patch.object(
        bt, "KEEPALIVE_INTERVAL", 100.0
    ), patch.object(
        bt, "MONITOR_INTERVAL", 1.0
    ), patch(
        "custom_components.kostal_kore.battery_test.time.monotonic",
        side_effect=lambda: float(next(monotonic_ticks)),
    ), patch(
        "custom_components.kostal_kore.battery_test.asyncio.sleep", new=AsyncMock()
    ):
        monotonic_state = await monotonic_suite._run_phase(bt.TestPhase("charge", 1000, 5, ""))
    assert monotonic_state.success is True

    invalid_monotonic_suite = bt.BatteryTestSuite(_coord({}), hass=None, entry_id="entry")
    invalid_ticks = count()
    with patch.object(
        invalid_monotonic_suite, "_write_charge", new=AsyncMock(return_value=True)
    ), patch.object(
        invalid_monotonic_suite, "_rd", new=AsyncMock(side_effect=[-1000.0, -1000.0])
    ), patch.object(
        invalid_monotonic_suite,
        "_read_sample",
        new=AsyncMock(
            return_value={
                "battery_cd_power": -1000.0,
                "battery_soc": 50.0,
                "inverter_state": object(),
                "pm_total_active": 0.0,
            }
        ),
    ), patch.object(
        bt, "KEEPALIVE_INTERVAL", 100.0
    ), patch.object(
        bt, "MONITOR_INTERVAL", 1.0
    ), patch(
        "custom_components.kostal_kore.battery_test.time.monotonic",
        side_effect=lambda: float(next(invalid_ticks)),
    ), patch(
        "custom_components.kostal_kore.battery_test.asyncio.sleep", new=AsyncMock()
    ):
        invalid_monotonic_state = await invalid_monotonic_suite._run_phase(
            bt.TestPhase("charge", 1000, 5, "")
        )
    assert invalid_monotonic_state.success is True

    zero_phase = bt.TestPhase("hold", 0, 0.0, "")
    with patch.object(run_phase_suite, "_write_discharge", new=AsyncMock(return_value=True)), patch(
        "custom_components.kostal_kore.battery_test.asyncio.sleep", side_effect=_scaled_sleep
    ), patch.object(
        run_phase_suite, "_rd", new=AsyncMock(side_effect=[0.0, 0.0])
    ):
        zero_result = await run_phase_suite._run_phase(zero_phase)
    assert zero_result.success is True
    assert zero_result.avg_actual_power == 0.0
    assert zero_result.power_match is False

    fail_flush_hass = SimpleNamespace(async_add_executor_job=AsyncMock(side_effect=RuntimeError("disk bad")))
    fail_flush_suite = bt.BatteryTestSuite(coord, hass=fail_flush_hass, entry_id="entry")
    await fail_flush_suite._flush_debug()
