"""Regression tests: battery features only restore what they changed.

Each case is a path where a feature used to write REG 1034/1038/1040 although
it did not hold them: a stop while idle, a refused turn-on, a turn-off while
off, a restore after the Modbus client had closed, or a NaN set-point.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.kostal_kore import _release_reg_1038_features
from custom_components.kostal_kore.battery_reg_1038_owner import (
    OWNER_CHARGE_BLOCK,
    OWNER_GRID_FEEDIN,
    get_reg_1038_owner_manager,
)
from custom_components.kostal_kore.battery_soc_controller import BatterySocController
from custom_components.kostal_kore.battery_test import BatteryTestSuite
from custom_components.kostal_kore.charge_block_switch import BatteryChargeBlockSwitch
from custom_components.kostal_kore.const import DOMAIN
from custom_components.kostal_kore.grid_charge_limiter import GridFeedInLimiterSwitch
from custom_components.kostal_kore.power_limits import clamp_control_power_w

ENTRY = "entry1"
DEVICE_MAX = 10_000.0


class _Registers:
    """Fake Modbus coordinator that records writes by register name."""

    def __init__(self, values: dict[str, float] | None = None) -> None:
        self.values = dict(values or {})
        self.writes: list[tuple[str, float]] = []
        self.client = SimpleNamespace(
            read_register=AsyncMock(side_effect=self._read),
            closing=False,
            connected=True,
        )
        self.async_write_register = AsyncMock(side_effect=self._write)
        self.device_info_data = {"inverter_max_power": DEVICE_MAX}
        self.data: dict[str, float] = {}

    async def _read(self, reg: object) -> float:
        return self.values.get(reg.name, 0.0)

    async def _write(self, reg: object, value: float) -> None:
        await asyncio.sleep(0)  # yield like a real Modbus round trip
        self.writes.append((reg.name, value))
        self.values[reg.name] = value


def _hass() -> SimpleNamespace:
    def _create_task(coro: object, *_args: object) -> asyncio.Task[None]:
        return asyncio.ensure_future(coro)  # type: ignore[arg-type]

    return SimpleNamespace(
        data={DOMAIN: {ENTRY: {}}},
        async_create_task=MagicMock(side_effect=_create_task),
        services=SimpleNamespace(async_call=AsyncMock()),
    )


def _charge_block(coord: _Registers, hass: SimpleNamespace) -> BatteryChargeBlockSwitch:
    switch = BatteryChargeBlockSwitch(coord, ENTRY, {"identifiers": {(DOMAIN, "x")}})
    switch.hass = hass
    switch.async_write_ha_state = MagicMock()
    return switch


def _grid_limiter(coord: _Registers, hass: SimpleNamespace) -> GridFeedInLimiterSwitch:
    limiter = GridFeedInLimiterSwitch(coord, ENTRY, {"identifiers": {(DOMAIN, "x")}})
    limiter.hass = hass
    limiter.async_write_ha_state = MagicMock()
    return limiter


# --- SoC controller -------------------------------------------------------


@pytest.mark.asyncio
async def test_idle_soc_controller_stop_writes_nothing() -> None:
    """Unload/reload and Target SoC 0 stop an idle controller: no writes."""
    coord = _Registers({"bat_max_charge_limit": 3000.0, "bat_max_discharge_limit": 2000.0})
    controller = BatterySocController(coord, hass=_hass(), entry_id=ENTRY)  # type: ignore[arg-type]

    await controller.stop()
    await controller.set_target(0)

    assert coord.writes == []


@pytest.mark.asyncio
async def test_stopping_a_discharge_session_restores_the_snapshot_once() -> None:
    """The loop's finally restores; stop() must not write device max after it."""
    coord = _Registers({
        "battery_soc": 80.0,
        "battery_temperature": 25.0,
        "inverter_state": 6.0,
        "bat_max_charge_limit": 3000.0,
        "bat_max_discharge_limit": 4000.0,
    })
    controller = BatterySocController(coord, hass=_hass(), entry_id=ENTRY)  # type: ignore[arg-type]

    with patch.object(controller, "_notify", new=AsyncMock()):
        await controller.set_target(50)
        for _ in range(50):
            if ("bat_max_charge_limit", 0.0) in coord.writes:
                break
            await asyncio.sleep(0)
        assert ("bat_max_charge_limit", 0.0) in coord.writes
        coord.writes.clear()
        await controller.set_target(0)

    assert coord.writes == [
        ("bat_charge_dc_abs_power", 0.0),
        ("bat_max_charge_limit", 3000.0),
    ]
    assert coord.values["bat_max_discharge_limit"] == 4000.0
    assert get_reg_1038_owner_manager(controller._hass, ENTRY).current is None


@pytest.mark.asyncio
async def test_temperature_pause_keeps_the_snapshot_for_the_final_restore() -> None:
    coord = _Registers({"bat_max_charge_limit": 3000.0})
    controller = BatterySocController(coord, hass=None, entry_id=ENTRY)  # type: ignore[arg-type]
    controller._original_charge_limit = 3000.0
    controller._wrote_1034 = True
    controller._wrote_1038 = True

    await controller._write_normal(keep_snapshot=True)  # the pause
    await controller._restore_session_writes()  # the end of the session

    assert coord.writes == [
        ("bat_charge_dc_abs_power", 0.0),
        ("bat_max_charge_limit", 3000.0),
        ("bat_charge_dc_abs_power", 0.0),
        ("bat_max_charge_limit", 3000.0),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [float("nan"), float("inf")])
async def test_non_finite_target_soc_is_rejected(value: float) -> None:
    coord = _Registers()
    controller = BatterySocController(coord, hass=_hass(), entry_id=ENTRY)  # type: ignore[arg-type]

    with pytest.raises(ServiceValidationError):
        await controller.set_target(value)

    assert controller.target_soc is None
    assert coord.writes == []


@pytest.mark.parametrize("value", [float("nan"), float("-inf")])
def test_non_finite_power_is_rejected(value: float) -> None:
    with pytest.raises(ServiceValidationError):
        clamp_control_power_w(value, device_limit_w=DEVICE_MAX)
    limiter = _grid_limiter(_Registers(), _hass())
    before = limiter._feed_in_limit_w
    with pytest.raises(ServiceValidationError):
        limiter.set_feed_in_limit(value)
    assert limiter._feed_in_limit_w == before


# --- Block Battery Charging ----------------------------------------------


@pytest.mark.asyncio
async def test_refused_charge_block_keeps_no_snapshot() -> None:
    """A refused turn-on must not remember the other owner's 0 W."""
    coord = _Registers({"bat_max_charge_limit": 0.0})
    hass = _hass()
    get_reg_1038_owner_manager(hass, ENTRY).try_acquire(OWNER_GRID_FEEDIN)
    switch = _charge_block(coord, hass)

    with pytest.raises(HomeAssistantError):
        await switch.async_turn_on()

    assert switch._original_charge_limit is None
    coord.client.read_register.assert_not_awaited()


@pytest.mark.asyncio
async def test_charge_block_turn_off_while_off_writes_nothing() -> None:
    coord = _Registers()
    hass = _hass()
    get_reg_1038_owner_manager(hass, ENTRY).try_acquire(OWNER_GRID_FEEDIN)
    switch = _charge_block(coord, hass)

    await switch.async_turn_off()

    assert coord.writes == []
    assert get_reg_1038_owner_manager(hass, ENTRY).current == OWNER_GRID_FEEDIN


@pytest.mark.asyncio
async def test_charge_block_keepalive_survives_a_failed_write() -> None:
    coord = _Registers()
    switch = _charge_block(coord, _hass())
    switch._is_on = True
    outcomes: list[object] = [OSError("server device failure"), None, None]

    async def _write_block() -> None:
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if not outcomes:
            switch._is_on = False

    with patch.object(switch, "_write_block", side_effect=_write_block), patch(
        "custom_components.kostal_kore.charge_block_switch.asyncio.sleep",
        new=AsyncMock(),
    ):
        await switch._run_keepalive()

    assert outcomes == []


@pytest.mark.asyncio
async def test_charge_block_restores_before_the_client_closes() -> None:
    coord = _Registers({"bat_max_charge_limit": 3000.0})
    hass = _hass()
    switch = _charge_block(coord, hass)
    with patch.object(switch, "_start_keepalive"):
        await switch.async_turn_on()
    coord.writes.clear()

    await _release_reg_1038_features({"charge_block_switch": switch})

    assert coord.writes == [("bat_max_charge_limit", 3000.0)]
    assert not switch.is_on
    assert get_reg_1038_owner_manager(hass, ENTRY).current is None
    with patch(
        "homeassistant.components.switch.SwitchEntity.async_will_remove_from_hass",
        new=AsyncMock(),
    ):
        await switch.async_will_remove_from_hass()
    assert coord.writes == [("bat_max_charge_limit", 3000.0)]


# --- Grid Feed-In Optimizer ----------------------------------------------


@pytest.mark.asyncio
async def test_refused_grid_limiter_keeps_no_snapshot() -> None:
    coord = _Registers({"bat_max_charge_limit": 0.0})
    hass = _hass()
    get_reg_1038_owner_manager(hass, ENTRY).try_acquire(OWNER_CHARGE_BLOCK)
    limiter = _grid_limiter(coord, hass)

    with pytest.raises(HomeAssistantError):
        await limiter.async_turn_on()

    assert limiter._original_charge_limit is None
    coord.client.read_register.assert_not_awaited()


@pytest.mark.asyncio
async def test_grid_limiter_turn_off_while_off_writes_nothing() -> None:
    coord = _Registers()
    hass = _hass()
    get_reg_1038_owner_manager(hass, ENTRY).try_acquire(OWNER_CHARGE_BLOCK)
    limiter = _grid_limiter(coord, hass)

    await limiter.async_turn_off()
    await _release_reg_1038_features({"grid_feedin_limiter": limiter})

    assert coord.writes == []
    assert get_reg_1038_owner_manager(hass, ENTRY).current == OWNER_CHARGE_BLOCK


@pytest.mark.asyncio
async def test_grid_limiter_restores_before_the_client_closes() -> None:
    coord = _Registers({"bat_max_charge_limit": 6000.0})
    hass = _hass()
    limiter = _grid_limiter(coord, hass)
    with patch.object(limiter, "_start_control"):
        await limiter.async_turn_on()

    await _release_reg_1038_features(
        {"grid_feedin_limiter": limiter, "charge_block_switch": None}
    )

    assert coord.writes == [("bat_max_charge_limit", 6000.0)]
    assert not limiter.is_on
    assert get_reg_1038_owner_manager(hass, ENTRY).current is None


# --- Battery test ---------------------------------------------------------


@pytest.mark.asyncio
async def test_refused_battery_test_writes_nothing() -> None:
    coord = _Registers()
    hass = _hass()
    get_reg_1038_owner_manager(hass, ENTRY).try_acquire(OWNER_CHARGE_BLOCK)
    suite = BatteryTestSuite(coord, hass=hass, entry_id=ENTRY)  # type: ignore[arg-type]

    with patch.object(suite, "_notify", new=AsyncMock()), patch.object(
        suite, "_flush_debug", new=AsyncMock()
    ):
        await suite.run()

    assert coord.writes == []
    assert get_reg_1038_owner_manager(hass, ENTRY).current == OWNER_CHARGE_BLOCK


@pytest.mark.asyncio
async def test_battery_test_rejected_after_its_write_check_restores_the_snapshot() -> None:
    """The pre-flight writes 1034/1038/1040 itself, so a rejection must restore."""
    from custom_components.kostal_kore.battery_test import PreFlightResult

    coord = _Registers()
    hass = _hass()
    suite = BatteryTestSuite(coord, hass=hass, entry_id=ENTRY)  # type: ignore[arg-type]

    async def _preflight(_phases: object) -> PreFlightResult:
        suite._original_charge_limit = 3000.0
        suite._original_discharge_limit = 2000.0
        await suite._wr("bat_charge_dc_abs_power", 0.0)
        await suite._wr("bat_max_charge_limit", 20000.0)
        await suite._wr("bat_max_discharge_limit", 20000.0)
        return PreFlightResult(ok=False, errors=["SoC 99% → zu hoch für Laden"])

    with patch.object(suite, "_preflight", side_effect=_preflight), patch.object(
        suite, "_notify", new=AsyncMock()
    ), patch.object(suite, "_flush_debug", new=AsyncMock()):
        await suite.run()

    assert coord.values["bat_max_charge_limit"] == 3000.0
    assert coord.values["bat_max_discharge_limit"] == 2000.0
    assert coord.values["bat_charge_dc_abs_power"] == 0.0
    assert get_reg_1038_owner_manager(hass, ENTRY).current is None


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [asyncio.CancelledError(), OSError("timeout")])
async def test_charge_block_turn_on_that_fails_or_is_cancelled_releases_reg_1038(
    error: BaseException,
) -> None:
    coord = _Registers()
    hass = _hass()
    switch = _charge_block(coord, hass)

    with patch.object(
        switch, "_snapshot_charge_limit", new=AsyncMock(side_effect=error)
    ), pytest.raises((asyncio.CancelledError, HomeAssistantError)):
        await switch.async_turn_on()

    assert not switch.is_on
    assert get_reg_1038_owner_manager(hass, ENTRY).current is None


@pytest.mark.asyncio
async def test_failed_repeat_turn_on_keeps_an_active_charge_block() -> None:
    coord = _Registers({"bat_max_charge_limit": 3000.0})
    hass = _hass()
    switch = _charge_block(coord, hass)
    with patch.object(switch, "_start_keepalive"):
        await switch.async_turn_on()

    with patch.object(
        switch, "_write_block", new=AsyncMock(side_effect=OSError("busy"))
    ), pytest.raises(HomeAssistantError):
        await switch.async_turn_on()

    assert switch.is_on
    assert switch._original_charge_limit == 3000.0
    assert get_reg_1038_owner_manager(hass, ENTRY).current == OWNER_CHARGE_BLOCK


@pytest.mark.asyncio
async def test_temperature_pause_before_any_command_writes_nothing() -> None:
    coord = _Registers()
    controller = BatterySocController(coord, hass=None, entry_id=ENTRY)  # type: ignore[arg-type]

    await controller._write_normal(keep_snapshot=True)

    assert coord.writes == []


@pytest.mark.asyncio
async def test_target_set_during_a_stop_is_not_lost() -> None:
    """stop() and set_target() run one at a time."""
    coord = _Registers({"battery_soc": 40.0, "battery_temperature": 25.0, "inverter_state": 6.0})
    hass = _hass()
    controller = BatterySocController(coord, hass=hass, entry_id=ENTRY)  # type: ignore[arg-type]

    with patch.object(controller, "_notify", new=AsyncMock()):
        await controller.set_target(80)
        await asyncio.sleep(0)
        stop = asyncio.ensure_future(controller.stop())
        await asyncio.sleep(0)
        await controller.set_target(60)
        await stop
        assert controller.target_soc == 60
        assert controller.active
        assert get_reg_1038_owner_manager(hass, ENTRY).current is not None
        await controller.stop()

    assert get_reg_1038_owner_manager(hass, ENTRY).current is None


@pytest.mark.asyncio
async def test_second_charge_block_turn_off_during_the_restore_writes_nothing() -> None:
    coord = _Registers({"bat_max_charge_limit": 3000.0})
    hass = _hass()
    switch = _charge_block(coord, hass)
    with patch.object(switch, "_start_keepalive"):
        await switch.async_turn_on()
    coord.writes.clear()

    await asyncio.gather(switch.async_turn_off(), switch.async_turn_off())

    assert coord.writes == [("bat_max_charge_limit", 3000.0)]


@pytest.mark.asyncio
async def test_repeated_grid_limiter_turn_on_keeps_the_running_loop() -> None:
    coord = _Registers({"bat_max_charge_limit": 6000.0})
    hass = _hass()
    limiter = _grid_limiter(coord, hass)
    running = asyncio.ensure_future(asyncio.sleep(3600))
    with patch.object(limiter, "_start_control") as start:
        await limiter.async_turn_on()
        limiter._task = running
        await limiter.async_turn_on()

    start.assert_called_once()
    assert limiter.is_on
    assert not running.cancelled()
    assert get_reg_1038_owner_manager(hass, ENTRY).current == OWNER_GRID_FEEDIN
    running.cancel()


@pytest.mark.asyncio
async def test_unload_waits_for_a_restore_already_in_progress() -> None:
    limiter = _grid_limiter(_Registers(), _hass())
    finished: list[bool] = []

    async def _finishing_restore() -> None:
        await asyncio.sleep(0)
        finished.append(True)

    limiter._task = asyncio.ensure_future(_finishing_restore())
    await limiter.async_release_for_unload()

    assert finished == [True]
    assert limiter._task is None


@pytest.mark.asyncio
async def test_switches_refuse_turn_on_once_unloading() -> None:
    coord = _Registers()
    hass = _hass()
    switch = _charge_block(coord, hass)
    limiter = _grid_limiter(coord, hass)
    await _release_reg_1038_features(
        {"charge_block_switch": switch, "grid_feedin_limiter": limiter}
    )

    with pytest.raises(HomeAssistantError):
        await switch.async_turn_on()
    with pytest.raises(HomeAssistantError):
        await limiter.async_turn_on()

    assert coord.writes == []
    assert get_reg_1038_owner_manager(hass, ENTRY).current is None
