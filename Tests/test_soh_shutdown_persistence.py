"""Pending SoH samples must survive shutdown and entry unload without waiting."""
import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.util.file import WriteError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kostal_kore.battery_soh_calculator import BatterySohCalculator
from custom_components.kostal_kore.const import CONF_MODBUS_ENABLED, DOMAIN


@pytest.mark.parametrize("started", [False, True])
async def test_stop_flushes_pending_soh_before_debounce_expires(hass, started):
    with patch("custom_components.kostal_kore.battery_soh_calculator._BatterySohStore") as store_cls:
        store = store_cls.return_value
        store.async_load = AsyncMock(return_value=None)
        store.async_save = AsyncMock()
        calc = BatterySohCalculator(hass, "shutdown")
        await calc.async_load()
        calc.register_stop_listener()
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
    calc.register_stop_listener()
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
        calc.register_stop_listener()
        listener = calc._remove_stop_listener
        await calc.async_load()
        calc.register_stop_listener()
        assert calc._remove_stop_listener is listener
        await calc.async_shutdown()
        await calc.async_shutdown()
        calc.schedule_save()
        assert calc._save_task is None
        assert calc._remove_stop_listener is None
        store.async_save.assert_not_awaited()


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
    calc.register_stop_listener()
    calc.schedule_save()
    hass.data[DOMAIN] = {entry.entry_id: {"battery_soh_calc": calc}}
    integration = importlib.import_module("custom_components.kostal_kore.__init__")
    with patch.object(integration, "_async_unload_loaded_platforms", AsyncMock(side_effect=RuntimeError("platform teardown"))):
        with pytest.raises(RuntimeError, match="platform teardown"):
            await integration._rollback_setup(hass, entry, SimpleNamespace(async_unload=AsyncMock()))
    assert calc._save_task is None
    assert calc._remove_stop_listener is None


async def test_shutdown_quiesces_producer_before_final_snapshot_write(hass):
    calc = BatterySohCalculator(hass, "final_snapshot")
    calc.update_from_modbus({"battery_work_capacity": 35000})
    snapshots, accepted = [], []

    async def save_snapshot(data):
        snapshots.append(data)
        # A refresh started before STOP may finish while Store writes.
        accepted.append(calc.update_from_modbus({"battery_work_capacity": 36000}))
        calc.schedule_save()
        await asyncio.sleep(0)

    with patch.object(calc._store, "async_save", side_effect=save_snapshot):
        await calc.async_shutdown()
    assert accepted == [False]
    assert calc.baseline_capacity_wh == 35000
    assert snapshots[0]["baseline_capacity_wh"] == 35000
    assert calc._save_task is None


async def test_shutdown_awaits_active_store_write_without_cancelling_it(hass):
    calc = BatterySohCalculator(hass, "active_write")
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
            writer = calc._save_task
            await asyncio.wait_for(entered.wait(), 1)
            calc.update_from_modbus({"battery_work_capacity": 36000})
            calc.schedule_save()
            shutdown = asyncio.create_task(calc.async_shutdown())
            try:
                await asyncio.sleep(0)
                assert not writer.cancelling()
                assert not shutdown.done()
                assert len(snapshots) == 1
            finally:
                finish.set()
                await asyncio.gather(writer, shutdown, return_exceptions=True)
    assert [data["baseline_capacity_wh"] for data in snapshots] == [35000, 36000]
    assert calc._save_task is None


@pytest.mark.parametrize("shutdown", [False, True], ids=["direct_save", "final_save"])
async def test_cancelled_save_owner_waits_until_store_write_finishes(hass, shutdown):
    calc = BatterySohCalculator(hass, "cancelled_owner")
    calc.update_from_modbus({"battery_work_capacity": 35000})
    entered, finish, written = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def store_write(data):
        entered.set()
        await finish.wait()
        written.set()

    with patch.object(calc._store, "async_save", side_effect=store_write) as save:
        owner = asyncio.create_task(calc.async_shutdown() if shutdown else calc.async_save())
        await asyncio.wait_for(entered.wait(), 1)
        owner.cancel()  # Same cancellation used by the unload cleanup timeout.
        try:
            await asyncio.sleep(0)
            assert not owner.done()
            assert not written.is_set()
        finally:
            finish.set()
            await asyncio.gather(owner, return_exceptions=True)
        assert owner.cancelled()
        assert written.is_set()
        save.assert_awaited_once()


async def test_shutdown_propagates_owner_cancellation_after_flushing_active_writer(hass):
    calc = BatterySohCalculator(hass, "cancelled_shutdown")
    entered, finish = asyncio.Event(), asyncio.Event()
    snapshots = []

    async def store_write(data):
        snapshots.append(data)
        if len(snapshots) == 1:
            entered.set()
            await finish.wait()

    with patch.object(calc._store, "async_save", side_effect=store_write):
        calc.update_from_modbus({"battery_work_capacity": 35000})
        with patch("custom_components.kostal_kore.battery_soh_calculator._SAVE_DEBOUNCE_S", 0):
            calc.schedule_save()
            writer = calc._save_task
            await entered.wait()
            calc.update_from_modbus({"battery_work_capacity": 36000})
            calc.schedule_save()
            shutdown = asyncio.create_task(calc.async_shutdown())
            await asyncio.sleep(0)
            shutdown.cancel()
            try:
                await asyncio.sleep(0)
                assert not shutdown.done()
            finally:
                finish.set()
                await asyncio.gather(shutdown, writer, return_exceptions=True)
    assert shutdown.cancelled(), "shutdown must preserve its owner's timeout cancellation"
    assert [data["baseline_capacity_wh"] for data in snapshots] == [35000, 36000]


@pytest.mark.parametrize("new_sample", [False, True])
async def test_failed_load_never_overwrites_existing_history(hass, hass_storage, new_sample):
    key = "failed_restore"
    original = {"baseline_capacity_wh": 35000, "baseline_set_at": 1700000000,
                "samples": [[.4, 35000, 1700000000]]}
    hass_storage[key] = {"version": 2, "minor_version": 1, "key": key, "data": original}
    calc = BatterySohCalculator(hass, key)
    with patch.object(calc._store, "async_load", side_effect=OSError("transient read failure")):
        await calc.async_load()
    if new_sample:
        calc.update_from_modbus({"battery_work_capacity": 36000})
        calc.schedule_save()
    await calc.async_shutdown()
    assert hass_storage[key]["data"] == original
    restored = BatterySohCalculator(hass, key)
    try:
        await restored.async_load()
        assert restored.baseline_capacity_wh == 35000
        assert restored.sample_count == 1
    finally:
        restored.cancel_pending_save()


@pytest.mark.parametrize("shutdown", [False, True], ids=["direct_save", "final_save"])
async def test_cancelled_save_preserves_cancellation_when_store_fails(hass, shutdown):
    calc = BatterySohCalculator(hass, "cancelled_failed_write")
    calc.update_from_modbus({"battery_work_capacity": 35000})
    entered, finish = asyncio.Event(), asyncio.Event()

    async def failed_write(data):
        entered.set()
        await finish.wait()
        raise OSError("disk unavailable")

    with patch.object(calc._store, "async_save", side_effect=failed_write):
        owner = asyncio.create_task(calc.async_shutdown() if shutdown else calc.async_save())
        await entered.wait()
        owner.cancel()
        await asyncio.sleep(0)
        assert not owner.done()
        finish.set()
        await asyncio.gather(owner, return_exceptions=True)
    assert owner.cancelled(), "a storage failure must not consume the owner's cancellation"


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_setup_failure_before_runtime_handoff_does_not_retain_stop_listener(hass, failure):
    entry = MockConfigEntry(domain=DOMAIN, title="early failure", data={"host": "127.0.0.1"},
                            options={CONF_MODBUS_ENABLED: True})
    entry.add_to_hass(hass)
    runtime = SimpleNamespace(async_setup=AsyncMock(return_value=True))
    events = SimpleNamespace(async_config_entry_first_refresh=AsyncMock())
    modbus = SimpleNamespace(async_setup=AsyncMock(), device_info_data={},
                             _restore_isolation_sample=AsyncMock(side_effect=failure))
    integration = importlib.import_module("custom_components.kostal_kore.__init__")
    before = hass.bus.async_listeners().get(EVENT_HOMEASSISTANT_STOP, 0)
    with patch.object(integration, "Plenticore", return_value=runtime), \
         patch.object(integration, "EventDataUpdateCoordinator", return_value=events), \
         patch.object(integration, "ModbusDataUpdateCoordinator", return_value=modbus), \
         patch("custom_components.kostal_kore.battery_soc_controller.BatterySocController"):
        for _ in range(2):
            with pytest.raises(failure):
                await integration.async_setup_entry(hass, entry)
            assert hass.bus.async_listeners().get(EVENT_HOMEASSISTANT_STOP, 0) == before


async def test_shutdown_retries_a_store_write_error_hidden_by_home_assistant(hass, hass_storage):
    key = "suppressed_write_error"
    original = {"baseline_capacity_wh": 34000, "baseline_set_at": 1700000000, "samples": []}
    hass_storage[key] = {"version": 2, "minor_version": 1, "key": key, "data": original}
    calc = BatterySohCalculator(hass, key)
    await calc.async_load()
    calc.update_from_modbus({"battery_work_capacity": 35000})
    try:
        # Store catches WriteError and returns normally: this is not a durable ack.
        with patch.object(calc._store, "_async_write_data", side_effect=WriteError("disk full")), \
             patch("custom_components.kostal_kore.battery_soh_calculator._SAVE_DEBOUNCE_S", 0):
            calc.schedule_save()
            await calc._save_task
        assert hass_storage[key]["data"] == original
        await calc.async_shutdown()
        assert hass_storage[key]["data"]["baseline_capacity_wh"] == 35000
    finally:
        calc.cancel_pending_save()
