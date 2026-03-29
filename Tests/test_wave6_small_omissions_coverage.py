"""Coverage and regression tests for smaller omitted modules."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.components.number import NumberMode
from homeassistant.helpers.update_coordinator import UpdateFailed
from pymodbus.exceptions import ModbusException as PyModbusException
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kostal_kore.charge_block_switch import (
    BatteryChargeBlockSwitch,
    KEEPALIVE_INTERVAL,
)
from custom_components.kostal_kore.const import DOMAIN
from custom_components.kostal_kore.degradation_entities import (
    DegradationAlertSensor,
    DegradationSensor,
    create_degradation_sensors,
)
from custom_components.kostal_kore.degradation_tracker import DegradationTracker
from custom_components.kostal_kore.ksem_coordinator import (
    KsemDataUpdateCoordinator,
)
from custom_components.kostal_kore.soc_controller_entities import (
    MaxChargePowerNumber,
    MaxDischargePowerNumber,
    TargetSocNumber,
    create_soc_controller_entities,
)


def _device_info() -> dict[str, str]:
    return {"identifiers": {("kostal_kore", "abc")}}


def _controller_stub() -> SimpleNamespace:
    return SimpleNamespace(
        target_soc=None,
        status="idle",
        active=False,
        set_target=AsyncMock(),
        set_max_charge_power=MagicMock(),
        set_max_discharge_power=MagicMock(),
        device_power_limit=8123.4,
        max_charge_power=4100.0,
        max_discharge_power=5200.0,
    )


def _coordinator_stub() -> SimpleNamespace:
    client = SimpleNamespace(read_register=AsyncMock(return_value=1234.0))
    return SimpleNamespace(client=client, async_write_register=AsyncMock())


def _tracker_with_days() -> DegradationTracker:
    tracker = DegradationTracker()
    start = 10 * 86400.0
    for idx in range(8):
        now = start + idx * 86400.0
        tracker.battery_soh.record(100.0 - idx, now=now)
        tracker.isolation.record(500.0 - idx * 10.0, now=now)
    tracker.battery_soh.record(90.0, now=start + 8 * 86400.0)
    return tracker


@pytest.mark.asyncio
async def test_soc_controller_entities_cover_primary_paths() -> None:
    """SoC number entities should reflect controller state and route writes."""
    controller = _controller_stub()
    target = TargetSocNumber(controller, "entry", _device_info())
    target.async_write_ha_state = MagicMock()

    assert target.native_value == 0
    assert target.extra_state_attributes == {
        "controller_status": "idle",
        "controller_active": False,
    }
    assert target._attr_mode == NumberMode.SLIDER

    await target.async_set_native_value(5)
    controller.set_target.assert_awaited_once_with(None)
    target.async_write_ha_state.assert_called_once()

    controller.set_target.reset_mock()
    target.async_write_ha_state.reset_mock()
    controller.target_soc = 55
    controller.status = "charging"
    controller.active = True

    assert target.native_value == 55
    assert target.extra_state_attributes == {
        "controller_status": "charging",
        "controller_active": True,
    }

    await target.async_set_native_value(55)
    controller.set_target.assert_awaited_once_with(55)
    target.async_write_ha_state.assert_called_once()

    max_charge = MaxChargePowerNumber(controller, "entry", _device_info())
    max_charge.async_write_ha_state = MagicMock()
    assert max_charge.native_value == 4100.0
    assert max_charge._attr_native_max_value == round(controller.device_power_limit)
    await max_charge.async_set_native_value(4700)
    controller.set_max_charge_power.assert_called_once_with(4700)
    max_charge.async_write_ha_state.assert_called_once()

    max_discharge = MaxDischargePowerNumber(controller, "entry", _device_info())
    max_discharge.async_write_ha_state = MagicMock()
    assert max_discharge.native_value == 5200.0
    assert max_discharge._attr_native_max_value == round(controller.device_power_limit)
    await max_discharge.async_set_native_value(3600)
    controller.set_max_discharge_power.assert_called_once_with(3600)
    max_discharge.async_write_ha_state.assert_called_once()

    created = create_soc_controller_entities(controller, "entry", _device_info())
    assert [type(entity) for entity in created] == [
        TargetSocNumber,
        MaxChargePowerNumber,
        MaxDischargePowerNumber,
    ]


@pytest.mark.asyncio
async def test_charge_block_switch_turn_on_off_keepalive_and_restore_paths() -> None:
    """Charge block switch should snapshot, restore, notify and manage keepalive."""
    coordinator = _coordinator_stub()
    hass_ref = SimpleNamespace(services=SimpleNamespace(async_call=AsyncMock()))
    entity = BatteryChargeBlockSwitch(coordinator, "entry42", _device_info(), hass=hass_ref)
    entity.hass = SimpleNamespace(async_create_task=MagicMock(return_value="task"))
    entity.async_write_ha_state = MagicMock()

    assert not entity.is_on
    assert entity.extra_state_attributes["keepalive_interval"] == f"{KEEPALIVE_INTERVAL}s"

    await entity._snapshot_charge_limit()
    assert entity._original_charge_limit == 1234.0
    assert entity._restore_limit() == 1234.0
    assert entity._original_charge_limit is None
    assert entity._restore_limit() == entity._normal_limit_w

    with patch.object(entity, "_write_block", new=AsyncMock()) as write_block, patch.object(
        entity, "_start_keepalive"
    ) as start_keepalive:
        await entity.async_turn_on()
    write_block.assert_awaited_once()
    start_keepalive.assert_called_once()
    assert entity.is_on
    hass_ref.services.async_call.assert_awaited()
    create_call = hass_ref.services.async_call.await_args_list[-1]
    assert create_call.args[:2] == ("persistent_notification", "create")
    assert create_call.args[2]["notification_id"] == "kostal_charge_block_entry42"

    entity._original_charge_limit = 777.0
    with patch.object(entity, "_cancel_keepalive") as cancel_keepalive, patch.object(
        entity, "_write_normal", new=AsyncMock()
    ) as write_normal:
        await entity.async_turn_off()
    cancel_keepalive.assert_called_once()
    write_normal.assert_awaited_once_with(777.0)
    assert not entity.is_on
    dismiss_call = hass_ref.services.async_call.await_args_list[-1]
    assert dismiss_call.args[:2] == ("persistent_notification", "dismiss")
    assert dismiss_call.args[2]["notification_id"] == "kostal_charge_block_entry42"

    reg = coordinator.async_write_register
    reg.reset_mock()
    await entity._write_block()
    reg.assert_awaited_once()

    reg.reset_mock()
    await entity._write_normal()
    reg.assert_awaited_once()
    assert reg.await_args.args[1] == entity._normal_limit_w

    reg.side_effect = ValueError("boom")
    await entity._write_normal(999.0)
    reg.side_effect = None

    cancel_task = MagicMock()
    cancel_task.done.return_value = False
    entity._keepalive_task = cancel_task
    entity._cancel_keepalive()
    cancel_task.cancel.assert_called_once()
    assert entity._keepalive_task is None
    entity._cancel_keepalive()

    entity._keepalive_task = None
    task_stub = MagicMock()

    def _create_task(coro, *_args):
        coro.close()
        return task_stub

    entity.hass.async_create_task = MagicMock(side_effect=_create_task)
    entity._start_keepalive()
    entity.hass.async_create_task.assert_called_once()
    assert entity._keepalive_task is task_stub

    entity._write_block = AsyncMock()
    entity._is_on = True
    entity._write_block.side_effect = lambda: setattr(entity, "_is_on", False)
    with patch("custom_components.kostal_kore.charge_block_switch.asyncio.sleep", new=AsyncMock()):
        await entity._run_keepalive()
    entity._write_block.assert_awaited_once()
    entity._write_block.side_effect = None

    entity._write_block.reset_mock()
    entity._is_on = True
    with patch(
        "custom_components.kostal_kore.charge_block_switch.asyncio.sleep",
        new=AsyncMock(side_effect=asyncio.CancelledError),
    ):
        await entity._run_keepalive()
    entity._write_block.assert_not_awaited()

    entity._is_on = True
    entity._restore_limit = MagicMock(return_value=654.0)
    entity._write_normal = AsyncMock()
    with patch.object(entity, "_cancel_keepalive") as cancel_keepalive, patch(
        "homeassistant.helpers.entity.Entity.async_will_remove_from_hass",
        new=AsyncMock(),
    ) as super_remove:
        await entity.async_will_remove_from_hass()
    cancel_keepalive.assert_called_once()
    entity._write_normal.assert_awaited_once_with(654.0)
    super_remove.assert_awaited_once()
    assert not entity.is_on

    entity._write_normal.reset_mock()
    entity._is_on = False
    with patch.object(entity, "_cancel_keepalive") as cancel_keepalive, patch(
        "homeassistant.helpers.entity.Entity.async_will_remove_from_hass",
        new=AsyncMock(),
    ) as super_remove:
        await entity.async_will_remove_from_hass()
    cancel_keepalive.assert_called_once()
    entity._write_normal.assert_not_awaited()
    super_remove.assert_awaited_once()


@pytest.mark.asyncio
async def test_charge_block_switch_snapshot_handles_invalid_or_missing_values() -> None:
    """Snapshot should ignore invalid data and missing registers."""
    coordinator = _coordinator_stub()
    entity = BatteryChargeBlockSwitch(coordinator, "entryX", _device_info())

    entity._original_charge_limit = 321.0
    await entity._snapshot_charge_limit()
    assert coordinator.client.read_register.await_count == 0
    entity._original_charge_limit = None

    coordinator.client.read_register.return_value = float("nan")
    await entity._snapshot_charge_limit()
    assert entity._original_charge_limit is None

    coordinator.client.read_register.side_effect = ValueError("bad")
    await entity._snapshot_charge_limit()
    assert entity._original_charge_limit is None
    coordinator.client.read_register.side_effect = None

    with patch(
        "custom_components.kostal_kore.charge_block_switch.REGISTER_BY_NAME",
        {},
    ):
        await entity._snapshot_charge_limit()
        await entity._write_block()
        await entity._write_normal(1000.0)


@pytest.mark.asyncio
async def test_charge_block_switch_turn_on_write_failure_raises() -> None:
    """async_turn_on should raise HomeAssistantError when _write_block fails."""
    from homeassistant.exceptions import HomeAssistantError

    coordinator = _coordinator_stub()
    entity = BatteryChargeBlockSwitch(coordinator, "entryFail", _device_info())
    entity.async_write_ha_state = MagicMock()

    # Pre-set a snapshot so we can verify it gets discarded
    entity._original_charge_limit = 500.0

    coordinator.async_write_register.side_effect = OSError("modbus down")
    with pytest.raises(HomeAssistantError, match="blockiert"):
        await entity.async_turn_on()

    # Snapshot discarded, switch not on, no keepalive
    assert entity._original_charge_limit is None
    assert not entity.is_on
    assert entity._keepalive_task is None


@pytest.mark.asyncio
async def test_charge_block_switch_no_notification_and_notification_error_paths() -> None:
    """Charge block switch should tolerate absent or failing notifications."""
    coordinator = _coordinator_stub()

    no_notify = BatteryChargeBlockSwitch(coordinator, "entryNone", _device_info(), hass=None)
    no_notify.async_write_ha_state = MagicMock()
    with patch.object(no_notify, "_start_keepalive"), patch.object(
        no_notify, "_write_block", new=AsyncMock()
    ):
        await no_notify.async_turn_on()
    with patch.object(no_notify, "_cancel_keepalive"), patch.object(
        no_notify, "_write_normal", new=AsyncMock()
    ):
        await no_notify.async_turn_off()

    no_notify._is_on = True
    no_notify._write_normal = AsyncMock()
    with patch.object(no_notify, "_cancel_keepalive"), patch(
        "homeassistant.helpers.entity.Entity.async_will_remove_from_hass",
        new=AsyncMock(),
    ):
        await no_notify.async_will_remove_from_hass()

    failing_hass = SimpleNamespace(
        services=SimpleNamespace(async_call=AsyncMock(side_effect=RuntimeError("notify failed")))
    )
    entity = BatteryChargeBlockSwitch(coordinator, "entryErr", _device_info(), hass=failing_hass)
    entity.async_write_ha_state = MagicMock()

    with patch.object(entity, "_start_keepalive"), patch.object(
        entity, "_write_block", new=AsyncMock()
    ):
        await entity.async_turn_on()

    with patch.object(entity, "_cancel_keepalive"), patch.object(
        entity, "_write_normal", new=AsyncMock()
    ):
        await entity.async_turn_off()

    entity._is_on = True
    entity._write_normal = AsyncMock()
    with patch.object(entity, "_cancel_keepalive"), patch(
        "homeassistant.helpers.entity.Entity.async_will_remove_from_hass",
        new=AsyncMock(),
    ):
        await entity.async_will_remove_from_hass()

    entity._write_block = AsyncMock()
    entity._is_on = True

    async def _stop_during_sleep(_delay: float) -> None:
        entity._is_on = False

    with patch(
        "custom_components.kostal_kore.charge_block_switch.asyncio.sleep",
        new=AsyncMock(side_effect=_stop_during_sleep),
    ):
        await entity._run_keepalive()
    entity._write_block.assert_not_awaited()


@pytest.mark.asyncio
async def test_degradation_entities_restore_current_and_legacy_formats() -> None:
    """Degradation entities should restore isolated state and persist only their parameter."""
    tracker = _tracker_with_days()
    sensor = DegradationSensor(
        tracker, "battery_soh", "Battery SoH", "mdi:battery", "entry", _device_info()
    )

    assert sensor.native_value
    attrs = sensor.extra_state_attributes
    assert attrs["days_tracked"] >= 8
    assert attrs["baseline_avg"] is not None
    assert attrs["current_avg"] is not None
    assert attrs["seasonal_trend"] is not None

    restore_payload = json.loads(sensor.extra_restore_state_data.as_dict()["snapshot_data"])
    assert list(restore_payload) == ["battery_soh"]

    current_data = {
        "snapshot_data": json.dumps(
            {"battery_soh": tracker.battery_soh.to_dict()}
        )
    }
    legacy_data = {
        "snapshot_data": json.dumps(tracker.isolation.to_dict())
    }

    restored_tracker = DegradationTracker()
    restored_sensor = DegradationSensor(
        restored_tracker,
        "battery_soh",
        "Battery SoH",
        "mdi:battery",
        "entry",
        _device_info(),
    )
    with patch(
        "homeassistant.helpers.restore_state.RestoreEntity.async_added_to_hass",
        new=AsyncMock(),
    ):
        restored_sensor.async_get_last_extra_data = AsyncMock(
            return_value=SimpleNamespace(as_dict=lambda: current_data)
        )
        await restored_sensor.async_added_to_hass()
    assert restored_tracker.battery_soh.days_tracked >= 8

    legacy_tracker = DegradationTracker()
    legacy_sensor = DegradationSensor(
        legacy_tracker,
        "isolation",
        "Isolation",
        "mdi:resistor",
        "entry",
        _device_info(),
    )
    with patch(
        "homeassistant.helpers.restore_state.RestoreEntity.async_added_to_hass",
        new=AsyncMock(),
    ):
        legacy_sensor.async_get_last_extra_data = AsyncMock(
            return_value=SimpleNamespace(as_dict=lambda: legacy_data)
        )
        await legacy_sensor.async_added_to_hass()
    assert legacy_tracker.isolation.days_tracked >= 8

    bad_sensor = DegradationSensor(
        DegradationTracker(),
        "battery_soh",
        "Battery SoH",
        "mdi:battery",
        "entry",
        _device_info(),
    )
    with patch(
        "homeassistant.helpers.restore_state.RestoreEntity.async_added_to_hass",
        new=AsyncMock(),
    ):
        bad_sensor.async_get_last_extra_data = AsyncMock(
            return_value=SimpleNamespace(as_dict=lambda: {"snapshot_data": "{bad json"})
        )
        await bad_sensor.async_added_to_hass()

    alert_sensor = DegradationAlertSensor(tracker, "entry", _device_info())
    assert alert_sensor.native_value == len(tracker.get_alerts())
    assert alert_sensor.extra_state_attributes["alert_count"] == len(tracker.get_alerts())

    created = create_degradation_sensors(tracker, "entry", _device_info())
    assert len(created) == 1 + 8
    assert isinstance(created[0], DegradationAlertSensor)


@pytest.mark.asyncio
async def test_degradation_entities_ignore_empty_or_unrelated_restore_state() -> None:
    """Restore should be a no-op for empty or unrelated stored payloads."""
    for payload in (
        None,
        SimpleNamespace(as_dict=lambda: {}),
        SimpleNamespace(as_dict=lambda: {"snapshot_data": ""}),
        SimpleNamespace(as_dict=lambda: {"snapshot_data": json.dumps({"other": {}})}),
    ):
        sensor = DegradationSensor(
            DegradationTracker(),
            "battery_soh",
            "Battery SoH",
            "mdi:battery",
            "entry",
            _device_info(),
        )
        with patch(
            "homeassistant.helpers.restore_state.RestoreEntity.async_added_to_hass",
            new=AsyncMock(),
        ):
            sensor.async_get_last_extra_data = AsyncMock(return_value=payload)
            await sensor.async_added_to_hass()


@pytest.mark.asyncio
async def test_ksem_coordinator_connection_read_and_update_paths(hass) -> None:
    """KSEM coordinator should normalize connect/read failures and decode values."""
    config_entry = MockConfigEntry(domain=DOMAIN, data={"host": "1.2.3.4"})
    coordinator = KsemDataUpdateCoordinator(
        hass=hass,
        config_entry=config_entry,
        host="1.2.3.4",
        port=1502,
        unit_id=71,
    )

    assert not coordinator.connected

    connected_client = MagicMock()
    connected_client.connected = True
    coordinator._client = connected_client
    assert coordinator.connected

    coordinator._client = None
    good_client = MagicMock()
    good_client.connected = False
    good_client.connect = AsyncMock(return_value=True)
    with patch(
        "custom_components.kostal_kore.ksem_coordinator.AsyncModbusTcpClient",
        return_value=good_client,
    ):
        await coordinator.async_setup()
    assert coordinator._client is good_client

    coordinator._client = SimpleNamespace(connected=True)
    with patch(
        "custom_components.kostal_kore.ksem_coordinator.AsyncModbusTcpClient",
    ) as client_cls:
        await coordinator._ensure_connected()
    client_cls.assert_not_called()
    coordinator._client = good_client

    with patch.object(coordinator, "_ensure_connected", new=AsyncMock()) as ensure_connected:
        await coordinator.async_setup()
    ensure_connected.assert_awaited_once()

    with patch(
        "custom_components.kostal_kore.ksem_coordinator.DataUpdateCoordinator.async_shutdown",
        new=AsyncMock(),
    ) as base_shutdown:
        await coordinator.async_shutdown()
    base_shutdown.assert_awaited_once()
    good_client.close.assert_called_once()
    assert coordinator._client is None

    with patch(
        "custom_components.kostal_kore.ksem_coordinator.DataUpdateCoordinator.async_shutdown",
        new=AsyncMock(),
    ) as base_shutdown:
        await coordinator.async_shutdown()
    base_shutdown.assert_awaited_once()

    error_client = MagicMock()
    error_client.connect = AsyncMock(side_effect=OSError("boom"))
    with patch(
        "custom_components.kostal_kore.ksem_coordinator.AsyncModbusTcpClient",
        return_value=error_client,
    ):
        with pytest.raises(UpdateFailed):
            await coordinator._ensure_connected()
    error_client.close.assert_called_once()

    timeout_client = MagicMock()
    timeout_client.connect = AsyncMock(side_effect=asyncio.TimeoutError())
    with patch(
        "custom_components.kostal_kore.ksem_coordinator.AsyncModbusTcpClient",
        return_value=timeout_client,
    ):
        with pytest.raises(UpdateFailed):
            await coordinator._ensure_connected()

    pymodbus_client = MagicMock()
    pymodbus_client.connect = AsyncMock(side_effect=PyModbusException("modbus"))
    with patch(
        "custom_components.kostal_kore.ksem_coordinator.AsyncModbusTcpClient",
        return_value=pymodbus_client,
    ):
        with pytest.raises(UpdateFailed):
            await coordinator._ensure_connected()

    false_client = MagicMock()
    false_client.connect = AsyncMock(return_value=False)
    with patch(
        "custom_components.kostal_kore.ksem_coordinator.AsyncModbusTcpClient",
        return_value=false_client,
    ):
        with pytest.raises(UpdateFailed):
            await coordinator._ensure_connected()
    false_client.close.assert_called_once()

    coordinator._client = None
    coordinator._ensure_connected = AsyncMock()
    with pytest.raises(UpdateFailed):
        await coordinator._read_registers(0, 2)

    read_client = MagicMock()
    read_client.read_holding_registers = AsyncMock(
        return_value=SimpleNamespace(isError=lambda: False, registers=[0x1234, 0x5678])
    )
    coordinator._client = read_client
    coordinator._ensure_connected = AsyncMock()
    assert await coordinator._read_registers(0, 2) == [0x1234, 0x5678]

    read_client.read_holding_registers = AsyncMock(
        return_value=SimpleNamespace(isError=lambda: True, registers=[1, 2])
    )
    with pytest.raises(UpdateFailed):
        await coordinator._read_registers(0, 2)

    read_client.read_holding_registers = AsyncMock(
        return_value=SimpleNamespace(isError=lambda: False, registers=[1])
    )
    with pytest.raises(UpdateFailed):
        await coordinator._read_registers(0, 2)

    coordinator._client = read_client
    read_client.read_holding_registers = AsyncMock(side_effect=OSError("offline"))
    with pytest.raises(UpdateFailed):
        await coordinator._read_registers(0, 2)
    read_client.close.assert_called()
    assert coordinator._client is None

    coordinator._read_registers = AsyncMock(return_value=[0x0001, 0x0002])
    assert await coordinator._read_u32(1, 0.5) == float(0x00010002) * 0.5

    coordinator._read_registers = AsyncMock(return_value=[0xFFFF, 0xFFFE])
    assert await coordinator._read_i32(1, 1.0) == -2.0

    coordinator._ensure_connected = AsyncMock()
    coordinator._read_u32 = AsyncMock(
        side_effect=[3200.0, 400.0, 49.99, 230.1, 229.9, 230.0]
    )
    coordinator._read_i32 = AsyncMock(side_effect=[0.995, -150.0, -50.0, -75.0])
    data = await coordinator._async_update_data()
    assert data["net_active_power_w"] == 2800.0
    assert data["power_factor"] == 0.995
    assert data["l3_active_power_w"] == -75.0
