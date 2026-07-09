"""Coverage tests for modbus_number.py."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers.update_coordinator import CoordinatorEntity

import custom_components.kostal_kore.modbus_number as mod


def _coord(
    *,
    device_info_data: dict[str, object] | None = None,
    data: dict[str, object] | None = None,
) -> MagicMock:
    coord = MagicMock()
    coord.client = SimpleNamespace(read_register=AsyncMock())
    coord.device_info_data = device_info_data or {}
    coord.data = data
    coord.last_update_success = True
    coord.async_write_register = AsyncMock()
    coord.async_request_refresh = AsyncMock()
    coord.hass = SimpleNamespace()
    coord.async_add_listener = MagicMock(return_value=lambda: None)
    coord.last_commanded = MagicMock(return_value=None)
    return coord


def _device_info() -> dict[str, object]:
    return {"identifiers": {("kostal_kore", "abc")}}


@pytest.mark.asyncio
async def test_probe_modbus_access_and_build_descriptions() -> None:
    """Probe helper and description builder should cover success and fallback paths."""
    coord = _coord()
    coord.client.read_register.return_value = "42"
    assert await mod._probe_modbus_access(coord) is True

    coord.client.read_register.side_effect = mod.ModbusClientError("boom")
    assert await mod._probe_modbus_access(coord) is False

    descriptions = mod._build_descriptions(777)
    assert len(descriptions) == 8
    assert descriptions[0]["min_value"] == -777
    assert descriptions[0]["max_value"] == 777
    assert descriptions[-1]["register"] == mod.REG_G3_MAX_DISCHARGE


@pytest.mark.asyncio
async def test_create_modbus_number_entities_mode_variants() -> None:
    """Entity factory should cover external, probe-success, probe-fail and invalid modes."""
    with patch(
        "custom_components.kostal_kore.notifications.notify_modbus_probe_success",
        new=AsyncMock(),
    ) as notify_success, patch(
        "custom_components.kostal_kore.notifications.notify_modbus_probe_failed",
        new=AsyncMock(),
    ) as notify_failed:
        ext = _coord(
            device_info_data={
                mod.REG_INVERTER_MAX_POWER.name: "5500",
                mod.REG_BATTERY_MGMT_MODE.name: "2",
            }
        )
        entities = await mod.create_modbus_number_entities(ext, "entry1", _device_info())
        assert len(entities) == 8
        assert entities[0]._attr_native_max_value == 5500
        assert entities[0]._read_only is False
        notify_success.assert_awaited_once_with(ext.hass)
        notify_failed.assert_not_awaited()

    with patch(
        "custom_components.kostal_kore.notifications.notify_modbus_probe_success",
        new=AsyncMock(),
    ) as notify_success, patch(
        "custom_components.kostal_kore.notifications.notify_modbus_probe_failed",
        new=AsyncMock(),
    ) as notify_failed, patch(
        "custom_components.kostal_kore.modbus_number._probe_modbus_access",
        new=AsyncMock(return_value=True),
    ) as probe:
        probe_ok = _coord(device_info_data={mod.REG_BATTERY_MGMT_MODE.name: "0"})
        entities = await mod.create_modbus_number_entities(probe_ok, "entry2", _device_info())
        assert entities[0]._read_only is False
        assert entities[0]._attr_native_max_value == mod.FALLBACK_MAX_POWER
        probe.assert_awaited_once_with(probe_ok)
        notify_success.assert_awaited_once_with(probe_ok.hass)
        notify_failed.assert_not_awaited()

    with patch(
        "custom_components.kostal_kore.notifications.notify_modbus_probe_success",
        new=AsyncMock(),
    ) as notify_success, patch(
        "custom_components.kostal_kore.notifications.notify_modbus_probe_failed",
        new=AsyncMock(),
    ) as notify_failed, patch(
        "custom_components.kostal_kore.modbus_number._probe_modbus_access",
        new=AsyncMock(return_value=False),
    ) as probe:
        probe_fail = _coord(
            device_info_data={
                mod.REG_INVERTER_MAX_POWER.name: "bad",
                mod.REG_BATTERY_MGMT_MODE.name: "0",
            }
        )
        entities = await mod.create_modbus_number_entities(probe_fail, "entry3", _device_info())
        assert entities[0]._read_only is True
        assert entities[0]._attr_native_max_value == mod.FALLBACK_MAX_POWER
        probe.assert_awaited_once_with(probe_fail)
        notify_success.assert_not_awaited()
        notify_failed.assert_awaited_once_with(probe_fail.hass)

    with patch(
        "custom_components.kostal_kore.notifications.notify_modbus_probe_success",
        new=AsyncMock(),
    ), patch(
        "custom_components.kostal_kore.notifications.notify_modbus_probe_failed",
        new=AsyncMock(),
    ), patch(
        "custom_components.kostal_kore.modbus_number._probe_modbus_access",
        new=AsyncMock(return_value=True),
    ) as probe:
        invalid = _coord(device_info_data={mod.REG_BATTERY_MGMT_MODE.name: "weird"})
        entities = await mod.create_modbus_number_entities(invalid, "entry4", _device_info())
        assert entities[0]._read_only is True
        probe.assert_awaited_once_with(invalid)

    with patch(
        "custom_components.kostal_kore.notifications.notify_modbus_probe_success",
        new=AsyncMock(),
    ) as notify_success, patch(
        "custom_components.kostal_kore.notifications.notify_modbus_probe_failed",
        new=AsyncMock(),
    ) as notify_failed:
        no_mode = _coord(device_info_data={mod.REG_INVERTER_MAX_POWER.name: "1234"})
        entities = await mod.create_modbus_number_entities(no_mode, "entry5", _device_info())
        assert entities[0]._read_only is False
        notify_success.assert_not_awaited()
        notify_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_modbus_number_entity_paths() -> None:
    """Number entity should cover read/write, keepalive and cleanup branches."""
    coord = _coord(
        data={
            mod.REG_G3_MAX_CHARGE.name: "123.5",
            mod.REG_G3_FALLBACK_TIME.name: "30",
        }
    )
    entity = mod.ModbusNumberEntity(
        coordinator=coord,
        register=mod.REG_G3_MAX_CHARGE,
        name="G3 Charge",
        icon="mdi:battery",
        min_value=0,
        max_value=1000,
        step=50,
        unit="W",
        device_class=None,
        entity_category=None,
        entry_id="entry",
        device_info=_device_info(),
        read_only=False,
    )
    created: list[object] = []

    def _create_task(coro, *_args):
        created.append(coro)
        coro.close()
        return SimpleNamespace(done=lambda: False)

    entity.hass = SimpleNamespace(async_create_task=MagicMock(side_effect=_create_task))

    assert entity.available is True
    assert entity.native_value == 123.5

    coord.data = None
    assert entity.available is False
    assert entity.native_value is None

    coord.data = {mod.REG_G3_FALLBACK_TIME.name: "30"}
    assert entity.native_value is None

    coord.data = {mod.REG_G3_MAX_CHARGE.name: "bad"}
    assert entity.native_value is None

    read_only = mod.ModbusNumberEntity(
        coordinator=coord,
        register=mod.REG_BAT_MIN_SOC,
        name="Read Only",
        icon="mdi:lock",
        min_value=0,
        max_value=100,
        step=1,
        unit="%",
        device_class=None,
        entity_category=None,
        entry_id="entry",
        device_info=_device_info(),
        read_only=True,
    )
    await read_only.async_set_native_value(10)
    coord.async_write_register.assert_not_awaited()

    coord.data = {mod.REG_G3_MAX_CHARGE.name: "1", mod.REG_G3_FALLBACK_TIME.name: "30"}
    coord.async_write_register.reset_mock()
    await entity.async_set_native_value(float("nan"))
    await entity.async_set_native_value(float("inf"))
    await entity.async_set_native_value(5000)
    coord.async_write_register.assert_not_awaited()

    coord.client.read_register = AsyncMock(return_value=300.0)
    await entity.async_set_native_value(100)
    coord.async_write_register.assert_awaited_once_with(mod.REG_G3_MAX_CHARGE, 100)
    coord.async_request_refresh.assert_awaited_once()
    assert created

    coord.async_write_register.reset_mock()
    coord.async_request_refresh.reset_mock()
    entity._keepalive_task = None
    coord.client.read_register = AsyncMock(side_effect=ValueError("skip verify"))
    await entity.async_set_native_value(150)
    coord.async_write_register.assert_awaited_once_with(mod.REG_G3_MAX_CHARGE, 150)
    coord.async_request_refresh.assert_awaited_once()

    non_cyclic = mod.ModbusNumberEntity(
        coordinator=coord,
        register=mod.REG_BAT_MAX_SOC,
        name="Max SoC",
        icon="mdi:battery-high",
        min_value=0,
        max_value=100,
        step=1,
        unit="%",
        device_class=None,
        entity_category=None,
        entry_id="entry",
        device_info=_device_info(),
        read_only=False,
    )
    non_cyclic.hass = entity.hass
    coord.client.read_register = AsyncMock(return_value=80.5)
    coord.async_write_register.reset_mock()
    coord.async_request_refresh.reset_mock()
    await non_cyclic.async_set_native_value(80)
    coord.async_write_register.assert_awaited_once_with(mod.REG_BAT_MAX_SOC, 80)
    coord.async_request_refresh.assert_awaited_once()

    entity._keepalive_task = SimpleNamespace(done=lambda: False)
    entity.hass.async_create_task.reset_mock()
    entity._start_keepalive(200)
    entity.hass.async_create_task.assert_not_called()

    coord.data = {}
    assert entity._get_keepalive_interval() == 30
    coord.data = {mod.REG_G3_FALLBACK_TIME.name: "10"}
    assert entity._get_keepalive_interval() == mod.G3_KEEPALIVE_MIN_SECONDS
    coord.data = {mod.REG_G3_FALLBACK_TIME.name: "9999"}
    assert entity._get_keepalive_interval() == mod.G3_KEEPALIVE_MAX_SECONDS
    coord.data = {mod.REG_G3_FALLBACK_TIME.name: "oops"}
    assert entity._get_keepalive_interval() == 30
    coord.data = {"something_else": "1"}
    assert entity._get_keepalive_interval() == 30

    entity._keepalive_value = 111
    entity._keepalive_task = None
    coord.async_write_register = AsyncMock(
        side_effect=lambda *_args, **_kwargs: setattr(entity, "_keepalive_value", None)
    )
    with patch.object(entity, "_get_keepalive_interval", return_value=1), patch(
        "custom_components.kostal_kore.modbus_number.asyncio.sleep",
        new=AsyncMock(),
    ):
        await entity._run_keepalive()
    coord.async_write_register.assert_awaited_once_with(mod.REG_G3_MAX_CHARGE, 111)

    entity._keepalive_value = 222
    coord.async_write_register = AsyncMock(side_effect=mod.ModbusClientError("bad"))
    with patch.object(entity, "_get_keepalive_interval", return_value=1), patch(
        "custom_components.kostal_kore.modbus_number.asyncio.sleep",
        new=AsyncMock(side_effect=[None, asyncio.CancelledError]),
    ):
        await entity._run_keepalive()

    entity._keepalive_value = 333
    coord.async_write_register = AsyncMock(
        side_effect=lambda *_args, **_kwargs: setattr(entity, "_keepalive_value", None)
    )
    with patch.object(entity, "_get_keepalive_interval", side_effect=ValueError("bad")), patch(
        "custom_components.kostal_kore.modbus_number.asyncio.sleep",
        new=AsyncMock(),
    ):
        await entity._run_keepalive()

    async def _clear_on_sleep(_interval: float) -> None:
        entity._keepalive_value = None

    entity._keepalive_value = 555
    coord.async_write_register = AsyncMock()
    with patch.object(entity, "_get_keepalive_interval", return_value=1), patch(
        "custom_components.kostal_kore.modbus_number.asyncio.sleep",
        new=AsyncMock(side_effect=_clear_on_sleep),
    ):
        await entity._run_keepalive()
    coord.async_write_register.assert_not_awaited()

    entity._keepalive_value = 444
    with patch(
        "custom_components.kostal_kore.modbus_number.asyncio.sleep",
        new=AsyncMock(side_effect=asyncio.CancelledError),
    ):
        await entity._run_keepalive()

    pending = MagicMock()
    pending.done.return_value = False
    entity._keepalive_task = pending
    entity._keepalive_value = 10
    entity._cancel_keepalive()
    pending.cancel.assert_called_once()
    assert entity._keepalive_task is None
    assert entity._keepalive_value is None

    with patch.object(CoordinatorEntity, "async_will_remove_from_hass", AsyncMock()) as super_remove:
        await entity.async_will_remove_from_hass()
    super_remove.assert_awaited_once()


def _curtail_entity(coord: MagicMock) -> "mod.ModbusNumberEntity":
    return mod.ModbusNumberEntity(
        coordinator=coord,
        register=mod.REG_ACTIVE_POWER_SETPOINT,
        name="Active Power Setpoint (Modbus)",
        icon="mdi:transmission-tower-export",
        min_value=1,
        max_value=100,
        step=1,
        unit="%",
        device_class=None,
        entity_category=None,
        entry_id="entry",
        device_info=_device_info(),
        read_only=False,
    )


def test_active_power_setpoint_exposes_static_curtailment_attributes() -> None:
    """Register 533 surfaces the static feed-in-curtailment semantics, and with
    no command yet reports an honest unknown (not a false curtailed/full signal).
    Other registers surface no extra attributes. The dynamic flags are validated
    against the coordinator's last-commanded cache — the real production path —
    in test_active_power_setpoint_reads_coordinator_last_commanded."""
    coord = _coord(data={})
    coord.last_commanded = MagicMock(return_value=None)
    entity = _curtail_entity(coord)
    attrs = entity.extra_state_attributes
    assert attrs is not None
    assert attrs["role"] == "feed_in_curtailment"
    assert attrs["minimum_percent"] == 1
    assert attrs["zero_export_via_this_entity"] is False
    assert attrs["volatile_resets_to_full_power"] is True
    assert attrs["curtailment_active"] is False
    assert attrs["at_full_power"] is False
    assert attrs["last_commanded_percent"] is None

    # a non-curtailment register returns no extra attributes
    other = mod.ModbusNumberEntity(
        coordinator=_coord(data={mod.REG_BAT_MAX_SOC.name: "90"}),
        register=mod.REG_BAT_MAX_SOC,
        name="Battery Max SoC",
        icon="mdi:battery",
        min_value=5,
        max_value=100,
        step=1,
        unit="%",
        device_class=None,
        entity_category=None,
        entry_id="entry",
        device_info=_device_info(),
        read_only=False,
    )
    assert other.extra_state_attributes is None


def test_active_power_setpoint_is_not_polled() -> None:
    """Proof the cache is load-bearing (not injected test data): reg 533 is a
    write-only RW CONTROL register, so it is excluded from MONITORING_REGISTERS
    and never lands in coordinator.data — hence the last-commanded fallback."""
    from custom_components.kostal_kore.modbus_registers import (
        MONITORING_REGISTERS,
        Access,
    )

    assert mod.REG_ACTIVE_POWER_SETPOINT.access is Access.RW
    assert mod.REG_ACTIVE_POWER_SETPOINT not in MONITORING_REGISTERS


def test_active_power_setpoint_reads_coordinator_last_commanded() -> None:
    """533 is write-only (not polled), so native_value + flags come from the
    coordinator's shared last-commanded cache (populated by every write path,
    cleared on reconnect)."""
    name = mod.REG_ACTIVE_POWER_SETPOINT.name
    coord = _coord(data={})  # 533 never appears in the polled data
    entity = _curtail_entity(coord)

    # Commanded 80 % (via any path) → curtailment active.
    coord.last_commanded = MagicMock(side_effect=lambda n: 80.0 if n == name else None)
    assert entity.native_value == 80.0
    attrs = entity.extra_state_attributes
    assert attrs is not None
    assert attrs["curtailment_active"] is True
    assert attrs["at_full_power"] is False
    assert attrs["last_commanded_percent"] == 80.0

    # Released to 100 % → full power.
    coord.last_commanded = MagicMock(side_effect=lambda n: 100.0 if n == name else None)
    attrs = entity.extra_state_attributes
    assert attrs is not None
    assert attrs["curtailment_active"] is False
    assert attrs["at_full_power"] is True

    # Nothing commanded (fresh start / post-reconnect) → unknown.
    coord.last_commanded = MagicMock(return_value=None)
    assert entity.native_value is None
    attrs = entity.extra_state_attributes
    assert attrs is not None
    assert attrs["curtailment_active"] is False
    assert attrs["at_full_power"] is False
    assert attrs["last_commanded_percent"] is None


@pytest.mark.asyncio
async def test_active_power_setpoint_write_delegates_to_coordinator() -> None:
    """The entity delegates the write to the coordinator (which owns the cache);
    a read-only-gated write must not reach the coordinator at all."""
    coord = _coord(data={})
    entity = _curtail_entity(coord)
    await entity.async_set_native_value(80)
    coord.async_write_register.assert_awaited_once_with(
        mod.REG_ACTIVE_POWER_SETPOINT, 80
    )

    blocked_coord = _coord(data={})
    blocked = _curtail_entity(blocked_coord)
    blocked._read_only = True
    await blocked.async_set_native_value(50)
    blocked_coord.async_write_register.assert_not_awaited()
