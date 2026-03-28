"""Coverage tests for battery and grid control helpers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.kostal_kore.battery_soc_controller import (
    BatterySocController,
    KEEPALIVE_INTERVAL,
    MAX_CONSECUTIVE_FAILURES,
    POLL_INTERVAL,
)
from custom_components.kostal_kore.grid_charge_limiter import (
    CONTROL_INTERVAL,
    GridFeedInLimiterSwitch,
    FeedInLimitNumber,
    MIN_CHARGE_POWER_W,
)


def _coord_stub() -> SimpleNamespace:
    client = SimpleNamespace(read_register=AsyncMock())
    return SimpleNamespace(client=client, async_write_register=AsyncMock())


def _device_info() -> dict[str, str]:
    return {"identifiers": {("kostal_kore", "abc")}}


class _FalseLike:
    def __bool__(self) -> bool:
        return False


class _WeirdComparableSoc:
    def __lt__(self, _other: object) -> _FalseLike:
        return _FalseLike()

    def __gt__(self, _other: object) -> _FalseLike:
        return _FalseLike()


@pytest.mark.asyncio
async def test_battery_soc_controller_basic_set_target_stop_and_notification_paths() -> None:
    """Controller should cover basic target management and notification behavior."""
    coord = _coord_stub()
    hass = SimpleNamespace(
        async_create_task=MagicMock(),
        services=SimpleNamespace(async_call=AsyncMock()),
    )
    controller = BatterySocController(coord, hass=hass, entry_id="entry1")

    assert controller.target_soc is None
    assert controller.status == "idle"
    assert not controller.active
    assert controller.device_power_limit > 0

    controller.set_max_charge_power(999999)
    assert controller.max_charge_power <= controller.device_power_limit
    controller.set_max_discharge_power(999999)
    assert controller.max_discharge_power <= controller.device_power_limit

    with patch.object(controller, "_stop", new=AsyncMock()) as stop_mock:
        await controller.set_target(5)
    stop_mock.assert_awaited_once()
    assert controller.target_soc is None

    created_tasks: list[object] = []

    def _create_task(coro, *_args):
        created_tasks.append(coro)
        coro.close()
        return SimpleNamespace(done=lambda: False)

    hass.async_create_task.side_effect = _create_task
    with patch.object(controller, "_notify", new=AsyncMock()) as notify_mock:
        await controller.set_target(50)
    notify_mock.assert_awaited_once()
    assert controller.target_soc == 50
    assert created_tasks

    existing_task = SimpleNamespace(done=lambda: False)
    controller._task = existing_task
    with patch.object(controller, "_notify", new=AsyncMock()) as notify_mock:
        await controller.set_target(55)
    notify_mock.assert_awaited_once()
    assert controller._task is existing_task

    no_hass = BatterySocController(coord, hass=None, entry_id="entry2")
    with patch("custom_components.kostal_kore.battery_soc_controller.asyncio.ensure_future") as ensure_future, patch.object(
        no_hass, "_notify", new=AsyncMock()
    ):
        await no_hass.set_target(60)
    ensure_future.assert_called_once()
    ensure_future.call_args.args[0].close()

    with patch.object(controller, "_write_normal", new=AsyncMock()) as write_normal:
        pending = asyncio.create_task(asyncio.sleep(3600))
        controller._task = pending
        controller._target_soc = 40
        await controller.stop()
    assert controller.target_soc is None
    assert controller.status == "idle"
    assert controller._task is None
    write_normal.assert_awaited_once()
    assert pending.cancelled()

    controller._task = None
    controller._target_soc = 25
    controller._write_normal = AsyncMock()
    await controller.stop()
    assert controller.target_soc is None
    controller._write_normal.assert_awaited_once()

    notify_hass = SimpleNamespace(services=SimpleNamespace(async_call=AsyncMock(side_effect=RuntimeError("boom"))))
    controller._hass = notify_hass
    await controller._notify("Title", "Body")
    controller._hass = None
    await controller._notify("Ignored", "No hass")


@pytest.mark.asyncio
async def test_battery_soc_controller_snapshot_read_write_and_normalization_paths() -> None:
    """Controller register helpers should normalize success, missing and error cases."""
    coord = _coord_stub()
    controller = BatterySocController(coord, hass=None, entry_id="entry")

    coord.client.read_register.side_effect = [111.0, 222.0]
    await controller._snapshot_limits()
    assert controller._original_charge_limit == 111.0
    assert controller._original_discharge_limit == 222.0

    controller._original_charge_limit = None
    controller._original_discharge_limit = None
    coord.client.read_register.side_effect = [ValueError("bad"), float("nan")]
    await controller._snapshot_limits()
    assert controller._original_charge_limit is None
    assert controller._original_discharge_limit is None

    with patch("custom_components.kostal_kore.battery_soc_controller.REGISTER_BY_NAME", {}):
        await controller._snapshot_limits()

    coord.async_write_register.reset_mock()
    assert await controller._write_charge(500) is True
    coord.async_write_register.assert_awaited()

    coord.async_write_register = AsyncMock(side_effect=ValueError("bad"))
    assert await controller._write_charge(500) is False

    coord.async_write_register = AsyncMock(side_effect=[None, ValueError("secondary")])
    assert await controller._write_discharge(800) is True

    coord.async_write_register = AsyncMock(side_effect=ValueError("primary"))
    assert await controller._write_discharge(800) is False

    with patch("custom_components.kostal_kore.battery_soc_controller.REGISTER_BY_NAME", {}):
        assert await controller._write_charge(500) is False
        assert await controller._write_discharge(800) is False
        await controller._write_normal()
        assert await controller._read_soc() is None
        assert await controller._read_temp() is None
        assert await controller._read_inv_state() is None

    reg1034 = MagicMock()
    with patch(
        "custom_components.kostal_kore.battery_soc_controller.REGISTER_BY_NAME",
        {"bat_charge_dc_abs_power": reg1034},
    ):
        coord.async_write_register = AsyncMock()
        assert await controller._write_discharge(800) is True

    coord.async_write_register = AsyncMock()
    controller._original_charge_limit = 321.0
    controller._original_discharge_limit = 654.0
    await controller._write_normal()
    assert coord.async_write_register.await_count == 3
    assert controller._original_charge_limit is None
    assert controller._original_discharge_limit is None

    coord.async_write_register = AsyncMock(side_effect=ValueError("reset bad"))
    await controller._write_normal()

    coord.client.read_register.side_effect = ["55.0", "48.5", "10"]
    assert await controller._read_soc() == 55.0
    assert await controller._read_temp() == 48.5
    assert await controller._read_inv_state() == 10

    coord.client.read_register.side_effect = [ValueError("soc"), ValueError("temp"), ValueError("state")]
    assert await controller._read_soc() is None
    assert await controller._read_temp() is None
    assert await controller._read_inv_state() is None


@pytest.mark.asyncio
async def test_battery_soc_controller_run_loop_paths() -> None:
    """Main loop should cover read failures, pauses, target reached and generic errors."""
    coord = _coord_stub()
    controller = BatterySocController(coord, hass=None, entry_id="entry")

    controller._target_soc = 50
    controller._write_normal = AsyncMock()
    with patch.object(controller, "_snapshot_limits", new=AsyncMock()), patch.object(
        controller, "_read_soc", new=AsyncMock(return_value=None)
    ), patch.object(controller, "_notify", new=AsyncMock()) as notify_mock, patch(
        "custom_components.kostal_kore.battery_soc_controller.asyncio.sleep",
        new=AsyncMock(),
    ):
        await controller._run_loop()
    notify_mock.assert_awaited_once()
    assert controller._target_soc is None
    assert "error:" in controller.status

    controller._target_soc = None
    controller._status = "idle"
    controller._write_normal = AsyncMock()
    with patch.object(controller, "_snapshot_limits", new=AsyncMock()):
        await controller._run_loop()
    controller._write_normal.assert_awaited_once()

    controller._target_soc = 50
    controller._status = "idle"
    controller._write_normal = AsyncMock()
    with patch.object(controller, "_snapshot_limits", new=AsyncMock()), patch.object(
        controller, "_read_soc", new=AsyncMock(return_value=50)
    ), patch.object(controller, "_notify", new=AsyncMock()) as notify_mock:
        await controller._run_loop()
    notify_mock.assert_awaited_once()
    assert controller.status == "idle"

    controller._target_soc = 50
    controller._status = "idle"
    controller._write_normal = AsyncMock()
    with patch.object(controller, "_snapshot_limits", new=AsyncMock()), patch.object(
        controller, "_read_soc", new=AsyncMock(side_effect=[60, 50])
    ), patch.object(
        controller, "_read_temp", new=AsyncMock(side_effect=[60, None])
    ), patch.object(
        controller, "_read_inv_state", new=AsyncMock(return_value=None)
    ), patch.object(controller, "_notify", new=AsyncMock()) as notify_mock, patch(
        "custom_components.kostal_kore.battery_soc_controller.asyncio.sleep",
        new=AsyncMock(),
    ):
        await controller._run_loop()
    assert controller._write_normal.await_count >= 1
    notify_mock.assert_awaited_once()

    controller._target_soc = 50
    controller._status = "idle"
    controller._write_normal = AsyncMock()

    async def _charge_once(_power: float) -> bool:
        controller._last_write = 100.0
        return True

    with patch.object(controller, "_snapshot_limits", new=AsyncMock()), patch.object(
        controller, "_read_soc", new=AsyncMock(side_effect=[40, 51])
    ), patch.object(
        controller, "_read_temp", new=AsyncMock(return_value=None)
    ), patch.object(
        controller, "_read_inv_state", new=AsyncMock(return_value=None)
    ), patch.object(
        controller, "_write_charge", new=AsyncMock(side_effect=_charge_once)
    ), patch.object(controller, "_notify", new=AsyncMock()) as notify_mock, patch(
        "custom_components.kostal_kore.battery_soc_controller.asyncio.sleep",
        new=AsyncMock(),
    ), patch(
        "custom_components.kostal_kore.battery_soc_controller.time.monotonic",
        return_value=103.0,
    ):
        await controller._run_loop()
    notify_mock.assert_awaited_once()

    controller._target_soc = 50
    controller._status = "idle"
    controller._write_normal = AsyncMock()
    with patch.object(controller, "_snapshot_limits", new=AsyncMock()), patch.object(
        controller, "_read_soc", new=AsyncMock(side_effect=[60, 50])
    ), patch.object(
        controller, "_read_temp", new=AsyncMock(return_value=None)
    ), patch.object(
        controller, "_read_inv_state", new=AsyncMock(side_effect=[10, None])
    ), patch.object(controller, "_notify", new=AsyncMock()) as notify_mock, patch(
        "custom_components.kostal_kore.battery_soc_controller.asyncio.sleep",
        new=AsyncMock(),
    ):
        await controller._run_loop()
    notify_mock.assert_awaited_once()

    controller._target_soc = 50
    controller._status = "idle"
    controller._write_normal = AsyncMock()
    with patch.object(controller, "_snapshot_limits", new=AsyncMock()), patch.object(
        controller, "_read_soc", new=AsyncMock(side_effect=[float("nan")] * MAX_CONSECUTIVE_FAILURES)
    ), patch.object(controller, "_notify", new=AsyncMock()) as notify_mock, patch(
        "custom_components.kostal_kore.battery_soc_controller.asyncio.sleep",
        new=AsyncMock(),
    ):
        await controller._run_loop()
    notify_mock.assert_awaited_once()

    controller._target_soc = 50
    controller._status = "idle"
    controller._write_normal = AsyncMock()
    controller._last_write = 0.0
    with patch.object(controller, "_snapshot_limits", new=AsyncMock()), patch.object(
        controller, "_read_soc", new=AsyncMock(side_effect=[60] * MAX_CONSECUTIVE_FAILURES)
    ), patch.object(
        controller, "_read_temp", new=AsyncMock(return_value=None)
    ), patch.object(
        controller, "_read_inv_state", new=AsyncMock(return_value=None)
    ), patch.object(
        controller, "_write_discharge", new=AsyncMock(return_value=False)
    ), patch.object(controller, "_notify", new=AsyncMock()) as notify_mock, patch(
        "custom_components.kostal_kore.battery_soc_controller.asyncio.sleep",
        new=AsyncMock(),
    ):
        await controller._run_loop()
    notify_mock.assert_awaited_once()
    assert controller.status == "idle"

    controller._target_soc = 50
    controller._status = "idle"
    controller._write_normal = AsyncMock()
    controller._last_write = 0.0
    with patch.object(controller, "_snapshot_limits", new=AsyncMock()), patch.object(
        controller, "_read_soc", new=AsyncMock(side_effect=[60, 49])
    ), patch.object(
        controller, "_read_temp", new=AsyncMock(return_value=None)
    ), patch.object(
        controller, "_read_inv_state", new=AsyncMock(return_value=None)
    ), patch.object(
        controller, "_write_discharge", new=AsyncMock(return_value=True)
    ), patch.object(controller, "_notify", new=AsyncMock()) as notify_mock, patch(
        "custom_components.kostal_kore.battery_soc_controller.asyncio.sleep",
        new=AsyncMock(),
    ):
        await controller._run_loop()
    notify_mock.assert_awaited_once()

    controller._target_soc = 50
    controller._status = "idle"
    controller._write_normal = AsyncMock()
    with patch.object(controller, "_snapshot_limits", new=AsyncMock()), patch.object(
        controller, "_read_soc", new=AsyncMock(side_effect=[_WeirdComparableSoc(), asyncio.CancelledError])
    ), patch.object(
        controller, "_read_temp", new=AsyncMock(return_value=None)
    ), patch.object(
        controller, "_read_inv_state", new=AsyncMock(return_value=None)
    ), patch(
        "custom_components.kostal_kore.battery_soc_controller.asyncio.sleep",
        new=AsyncMock(),
    ):
        await controller._run_loop()
    controller._write_normal.assert_awaited_once()

    controller._target_soc = 50
    controller._status = "idle"
    controller._write_normal = AsyncMock()
    with patch.object(controller, "_snapshot_limits", new=AsyncMock()), patch.object(
        controller, "_read_soc", new=AsyncMock(side_effect=RuntimeError("boom"))
    ):
        await controller._run_loop()
    assert controller.status.startswith("error:")

    controller._target_soc = 50
    controller._status = "idle"
    controller._write_normal = AsyncMock()
    with patch.object(controller, "_snapshot_limits", new=AsyncMock()), patch.object(
        controller, "_read_soc", new=AsyncMock(side_effect=asyncio.CancelledError)
    ):
        await controller._run_loop()
    controller._write_normal.assert_awaited_once()


@pytest.mark.asyncio
async def test_grid_feed_in_limiter_switch_and_number_paths() -> None:
    """Grid limiter switch and number should cover control, restore and read paths."""
    coord = _coord_stub()
    limiter = GridFeedInLimiterSwitch(coord, "entry", _device_info(), hass=SimpleNamespace())
    limiter.hass = SimpleNamespace(async_create_task=MagicMock())
    limiter.async_write_ha_state = MagicMock()

    assert not limiter.is_on
    assert limiter.extra_state_attributes["current_battery_charge_limit_w"] == 0

    limiter.set_feed_in_limit(-1)
    assert limiter._feed_in_limit_w == 0.0
    limiter.set_feed_in_limit(999999)
    assert limiter._feed_in_limit_w == limiter._device_power_limit_w

    coord.client.read_register.return_value = 777.0
    await limiter._snapshot_charge_limit()
    assert limiter._original_charge_limit == 777.0
    assert limiter._restore_limit() == 777.0
    assert limiter._restore_limit() == limiter._device_power_limit_w

    limiter._original_charge_limit = 123.0
    await limiter._snapshot_charge_limit()
    assert limiter._original_charge_limit == 123.0
    limiter._original_charge_limit = None

    with patch.object(limiter, "_start_control") as start_control:
        await limiter.async_turn_on()
    start_control.assert_called_once()
    assert limiter.is_on

    limiter._original_charge_limit = 500.0
    with patch.object(limiter, "_cancel_control") as cancel_control, patch.object(
        limiter, "_write_charge_limit", new=AsyncMock()
    ) as write_limit:
        await limiter.async_turn_off()
    cancel_control.assert_called_once()
    write_limit.assert_awaited_once_with(500.0)
    assert not limiter.is_on

    limiter._task = None
    task_stub = MagicMock()

    def _create_task(coro, *_args):
        coro.close()
        return task_stub

    limiter.hass.async_create_task.side_effect = _create_task
    limiter._start_control()
    assert limiter._task is task_stub

    pending = MagicMock()
    pending.done.return_value = False
    limiter._task = pending
    limiter._cancel_control()
    pending.cancel.assert_called_once()
    assert limiter._task is None

    with patch(
        "custom_components.kostal_kore.grid_charge_limiter.REGISTER_BY_NAME",
        {},
    ):
        await limiter._snapshot_charge_limit()
        assert await limiter._read_float("missing") is None
        await limiter._write_charge_limit(100.0)

    coord.client.read_register.return_value = float("nan")
    assert await limiter._read_float("total_dc_power") is None
    coord.client.read_register.side_effect = ValueError("bad")
    assert await limiter._read_float("total_dc_power") is None
    coord.client.read_register.side_effect = None
    coord.client.read_register.return_value = 123.0
    assert await limiter._read_float("total_dc_power") == 123.0

    coord.client.read_register.side_effect = ValueError("snapshot bad")
    await limiter._snapshot_charge_limit()
    coord.client.read_register.side_effect = None
    coord.client.read_register.return_value = float("inf")
    await limiter._snapshot_charge_limit()
    assert limiter._original_charge_limit is None

    coord.async_write_register = AsyncMock(side_effect=ValueError("bad"))
    await limiter._write_charge_limit(111.0)
    coord.async_write_register = AsyncMock()

    limiter._is_on = True
    limiter._current_charge_limit = 0.0
    limiter._write_charge_limit = AsyncMock(side_effect=lambda watts: setattr(limiter, "_is_on", False))
    limiter._read_float = AsyncMock(side_effect=[15000.0, 1000.0])
    with patch(
        "custom_components.kostal_kore.grid_charge_limiter.asyncio.sleep",
        new=AsyncMock(),
    ):
        await limiter._control_loop()
    assert limiter._current_charge_limit >= MIN_CHARGE_POWER_W

    limiter._is_on = True
    limiter._write_charge_limit = AsyncMock(side_effect=lambda watts: setattr(limiter, "_is_on", False))
    limiter._read_float = AsyncMock(side_effect=[1200.0, 1000.0])
    with patch(
        "custom_components.kostal_kore.grid_charge_limiter.asyncio.sleep",
        new=AsyncMock(),
    ):
        await limiter._control_loop()
    assert limiter._current_charge_limit == 0.0

    limiter._is_on = True

    async def _stop_sleep(_delay: float) -> None:
        limiter._is_on = False

    limiter._read_float = AsyncMock(return_value=None)
    limiter._write_charge_limit = AsyncMock()
    with patch(
        "custom_components.kostal_kore.grid_charge_limiter.asyncio.sleep",
        new=AsyncMock(side_effect=_stop_sleep),
    ):
        await limiter._control_loop()
    limiter._write_charge_limit.assert_awaited_once_with(limiter._device_power_limit_w)

    limiter._is_on = True
    limiter._write_charge_limit = AsyncMock()
    with patch.object(
        limiter, "_read_float", new=AsyncMock(side_effect=RuntimeError("boom"))
    ):
        await limiter._control_loop()

    limiter._is_on = True
    limiter._write_charge_limit = AsyncMock()
    with patch.object(
        limiter, "_read_float", new=AsyncMock(side_effect=asyncio.CancelledError)
    ):
        await limiter._control_loop()

    limiter._is_on = True
    limiter._write_charge_limit = AsyncMock()
    with patch.object(limiter, "_cancel_control"), patch(
        "homeassistant.helpers.entity.Entity.async_will_remove_from_hass",
        new=AsyncMock(),
    ) as super_remove:
        await limiter.async_will_remove_from_hass()
    limiter._write_charge_limit.assert_awaited()
    super_remove.assert_awaited_once()

    limiter._is_on = False
    limiter._write_charge_limit = AsyncMock()
    with patch.object(limiter, "_cancel_control"), patch(
        "homeassistant.helpers.entity.Entity.async_will_remove_from_hass",
        new=AsyncMock(),
    ) as super_remove:
        await limiter.async_will_remove_from_hass()
    limiter._write_charge_limit.assert_not_awaited()
    super_remove.assert_awaited_once()

    number = FeedInLimitNumber(limiter, "entry", _device_info())
    number.async_write_ha_state = MagicMock()
    assert number.native_value == limiter._feed_in_limit_w
    await number.async_set_native_value(1200.0)
    assert limiter._feed_in_limit_w == 1200.0
    number.async_write_ha_state.assert_called_once()
