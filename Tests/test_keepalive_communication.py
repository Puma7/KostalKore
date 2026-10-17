"""A cyclic limit must not resurrect an old owner or pre-outage setpoint."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.kostal_kore.modbus_client import ModbusClientError
from custom_components.kostal_kore.modbus_number import ModbusNumberEntity
from custom_components.kostal_kore.modbus_registers import REG_G3_MAX_CHARGE
from custom_components.kostal_kore.number import PlenticoreDataNumber


def entity():
    result = object.__new__(ModbusNumberEntity)
    result._register = REG_G3_MAX_CHARGE
    result._read_only = False
    result._attr_native_min_value, result._attr_native_max_value = 0, 20000
    result._attr_native_step = 100
    result._keepalive_value, result._keepalive_generation = 5000, 1
    result._keepalive_task = None
    result._get_keepalive_interval = lambda: 1
    result.coordinator = SimpleNamespace(
        client=SimpleNamespace(connected=True, connection_generation=1), last_update_success=True,
        last_commanded=lambda name: 5000, async_write_register=AsyncMock(),
    )
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["offline", "poll_failed", "reconnected", "other_owner"])
async def test_lost_lease_prevents_stale_keepalive_writes(failure):
    item = entity()
    if failure == "offline":
        item.coordinator.client.connected = False
    elif failure == "poll_failed":
        item.coordinator.last_update_success = False
    elif failure == "reconnected":
        item.coordinator.client.connection_generation = 2
    else:
        item.coordinator.last_commanded = lambda name: 0
    with patch("custom_components.kostal_kore.modbus_number.asyncio.sleep", AsyncMock()):
        await item._run_keepalive()
    item.coordinator.async_write_register.assert_not_awaited()
    assert item._keepalive_value is None


@pytest.mark.asyncio
async def test_current_owner_keeps_one_current_limit_alive():
    item = entity()

    async def accepted(*args):
        item._keepalive_value = None

    item.coordinator.async_write_register.side_effect = accepted
    with patch("custom_components.kostal_kore.modbus_number.asyncio.sleep", AsyncMock()):
        await item._run_keepalive()
    item.coordinator.async_write_register.assert_awaited_once_with(REG_G3_MAX_CHARGE, 5000)


@pytest.mark.asyncio
async def test_failed_replacement_cancels_old_command_before_trying_new_zero():
    item = entity()
    task = MagicMock()
    task.done.return_value = False
    item._keepalive_task = task
    item.coordinator.async_write_register.side_effect = TimeoutError("no acknowledgement")
    with pytest.raises(TimeoutError):
        await item.async_set_native_value(0)
    task.cancel.assert_called_once()
    assert item._keepalive_value is None
    item.coordinator.async_write_register.assert_awaited_once_with(REG_G3_MAX_CHARGE, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("feedback", [5000, float("nan")])
async def test_acknowledged_but_unconfirmed_limit_never_starts_keepalive(feedback):
    item = entity()
    item.coordinator.client.read_register = AsyncMock(return_value=feedback)
    with pytest.raises(ModbusClientError, match="unconfirmed"):
        await item.async_set_native_value(0)
    assert item._keepalive_value is None


@pytest.mark.asyncio
async def test_keepalive_failure_is_not_retried_after_communication_returns():
    item = entity()
    item.coordinator.async_write_register.side_effect = TimeoutError()
    with patch("custom_components.kostal_kore.modbus_number.asyncio.sleep", AsyncMock()):
        await item._run_keepalive()
    item.coordinator.async_write_register.side_effect = None
    await item._run_keepalive()
    item.coordinator.async_write_register.assert_awaited_once()
    assert item._keepalive_value is None


@pytest.mark.asyncio
async def test_rest_keepalive_does_not_cross_an_outage_even_after_recovery():
    item = object.__new__(PlenticoreDataNumber)
    item._keepalive_value, item._keepalive_incidents = 5000, 0
    item._get_keepalive_interval = lambda: 1
    item.coordinator = SimpleNamespace(last_update_success=True, communication_stale=False,
        _plenticore=SimpleNamespace(rest_communication=SimpleNamespace(incidents=1)),
        async_write_data=AsyncMock())
    with patch("custom_components.kostal_kore.number.asyncio.sleep", AsyncMock()):
        await item._run_keepalive()
    assert item._keepalive_value is None
    item.coordinator.async_write_data.assert_not_awaited()
