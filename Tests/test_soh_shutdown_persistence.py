"""Pending SoH samples must survive shutdown and entry unload without waiting."""
import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kostal_kore.battery_soh_calculator import BatterySohCalculator
from custom_components.kostal_kore.const import DOMAIN


@pytest.mark.parametrize("started", [False, True])
async def test_stop_flushes_pending_soh_before_debounce_expires(hass, started):
    with patch("custom_components.kostal_kore.battery_soh_calculator._BatterySohStore") as store_cls:
        store = store_cls.return_value
        store.async_load = AsyncMock(return_value=None)
        store.async_save = AsyncMock()
        calc = BatterySohCalculator(hass, "shutdown")
        await calc.async_load()
        calc.update_from_modbus({"battery_work_capacity": 35000, "total_dc_discharge": 400})
        calc.schedule_save()
        task = calc._save_task
        try:
            if started:
                await asyncio.sleep(0)
            hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
            for _ in range(8):
                await asyncio.sleep(0)
            assert task.done(), "SoH debounce still runs after the stop event"
            store.async_save.assert_awaited_once()
            saved = store.async_save.call_args.args[0]
            assert saved["baseline_capacity_wh"] == 35000
            assert saved["samples"][0][:2] == [0.4, 35000]
            calc.schedule_save()
            assert calc._save_task is None
        finally:
            calc.cancel_pending_save()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("rollback", [False, True])
async def test_entry_cleanup_flushes_real_store_and_detaches_listener(hass, hass_storage, rollback):
    entry = MockConfigEntry(domain=DOMAIN, title="SoH cleanup")
    entry.add_to_hass(hass)
    runtime = SimpleNamespace(async_unload=AsyncMock())
    entry.runtime_data = runtime
    calc = BatterySohCalculator(hass, "soh_reload")
    await calc.async_load()
    calc.update_from_modbus({"battery_work_capacity": 35000, "total_dc_discharge": 400})
    calc.schedule_save()
    task = calc._save_task
    hass.data[DOMAIN] = {entry.entry_id: {"battery_soh_calc": calc}}
    integration = importlib.import_module("custom_components.kostal_kore.__init__")
    with patch.object(integration, "_async_unload_loaded_platforms", AsyncMock(return_value=True)):
        if rollback:
            await integration._rollback_setup(hass, entry, runtime)
        else:
            assert await integration.async_unload_entry(hass, entry)
    assert task.done()
    assert calc._remove_stop_listener is None
    assert calc._save_task is None
    restored = BatterySohCalculator(hass, "soh_reload")
    try:
        await restored.async_load()
        assert restored.baseline_capacity_wh == 35000
        assert restored.sample_count == 1
        assert hass_storage["soh_reload"]["data"]["samples"][0][:2] == [0.4, 35000]
        await calc.async_shutdown()
        calc.schedule_save()
        assert calc._save_task is None
    finally:
        restored.cancel_pending_save()


async def test_shutdown_after_debounce_has_already_finished(hass):
    calc = BatterySohCalculator(hass, "completed")
    with patch.object(calc, "async_save", AsyncMock()) as save:
        task = hass.async_create_background_task(calc.async_save(), "completed SoH save")
        await task
        calc._save_task = task
        await calc.async_shutdown()
        save.assert_awaited()
        assert calc._save_task is None
        await calc.async_shutdown()


async def test_idle_shutdown_is_idempotent_and_load_does_not_duplicate_stop_listener(hass):
    with patch("custom_components.kostal_kore.battery_soh_calculator._BatterySohStore") as store_cls:
        store = store_cls.return_value
        store.async_load = AsyncMock(return_value=None)
        store.async_save = AsyncMock()
        calc = BatterySohCalculator(hass, "idle")
        await calc.async_load()
        listener = calc._remove_stop_listener
        await calc.async_load()
        assert calc._remove_stop_listener is listener
        await calc.async_shutdown()
        await calc.async_shutdown()
        calc.schedule_save()
        assert calc._save_task is None
        assert calc._remove_stop_listener is None
        store.async_save.assert_awaited_once()


async def test_shutdown_saves_update_arriving_during_previous_snapshot_write(hass):
    calc = BatterySohCalculator(hass, "inflight")
    entered, finish = asyncio.Event(), asyncio.Event()
    snapshots = []

    async def save_snapshot(data):
        snapshots.append(data)
        if len(snapshots) == 1:
            entered.set()
            await finish.wait()

    with patch.object(calc._store, "async_save", side_effect=save_snapshot):
        calc.update_from_modbus({"battery_work_capacity": 35000})
        with patch("custom_components.kostal_kore.battery_soh_calculator._SAVE_DEBOUNCE_S", 0):
            calc.schedule_save()
            task = calc._save_task
            await entered.wait()
            calc.update_from_modbus({"battery_work_capacity": 36000})
            calc.schedule_save()  # Coalesced while the old snapshot is writing.
            finish.set()
            await task
        assert calc._save_task is None
        await asyncio.gather(calc.async_shutdown(), calc.async_shutdown())
    assert [data["baseline_capacity_wh"] for data in snapshots] == [35000, 36000]


async def test_rollback_closes_soh_before_platform_teardown_can_raise(hass):
    entry = MockConfigEntry(domain=DOMAIN, title="failed rollback")
    entry.add_to_hass(hass)
    calc = BatterySohCalculator(hass, "rollback_failure")
    await calc.async_load()
    calc.schedule_save()
    hass.data[DOMAIN] = {entry.entry_id: {"battery_soh_calc": calc}}
    integration = importlib.import_module("custom_components.kostal_kore.__init__")
    with patch.object(integration, "_async_unload_loaded_platforms", AsyncMock(side_effect=RuntimeError("platform teardown"))):
        with pytest.raises(RuntimeError, match="platform teardown"):
            await integration._rollback_setup(hass, entry, SimpleNamespace(async_unload=AsyncMock()))
    assert calc._save_task is None
    assert calc._remove_stop_listener is None
