"""A readable Modbus limit must not acknowledge an unconfirmed command."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.kostal_kore import modbus_number as mod
from Tests.test_wave9_modbus_number_coverage import _coord, _device_info


def entity(coord, *, read_only=False):
    return mod.ModbusNumberEntity(
        coordinator=coord, register=mod.REG_BAT_MAX_CHARGE_LIMIT,
        name="Synthetic limit", icon="mdi:battery", min_value=0,
        max_value=10000, step=100, unit="W", device_class=None,
        entity_category=None, entry_id="synthetic", device_info=_device_info(),
        read_only=read_only,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("feedback", [8000, float("nan"), float("inf"), None])
async def test_unconfirmed_readable_limit_fails_the_service(feedback):
    coord = _coord()
    coord.client.read_register = AsyncMock(return_value=feedback)
    e = entity(coord)
    with pytest.raises(mod.ModbusClientError, match="Readback is unconfirmed"):
        await e.async_set_native_value(2000)
    coord.async_write_register.assert_awaited_once_with(mod.REG_BAT_MAX_CHARGE_LIMIT, 2000)
    if feedback == 8000:
        coord.async_request_refresh.assert_awaited_once()
    else:
        coord.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirmed_readable_limit_succeeds():
    coord = _coord()
    coord.client.read_register = AsyncMock(return_value=2000)
    await entity(coord).async_set_native_value(2000)
    coord.async_write_register.assert_awaited_once_with(mod.REG_BAT_MAX_CHARGE_LIMIT, 2000)
    coord.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(("register", "requested", "observed"), [
    (mod.REG_BAT_MAX_CHARGE_LIMIT, 2000, 1900),
    (mod.REG_BAT_MIN_SOC, 80, 79),
])
async def test_an_ignored_single_step_is_not_confirmation(register, requested, observed):
    coord = _coord()
    e = entity(coord)
    e._register = register
    coord.client.read_register = AsyncMock(return_value=observed)
    with pytest.raises(mod.ModbusClientError, match="unconfirmed"):
        await e.async_set_native_value(requested)
    coord.async_write_register.assert_awaited_once_with(register, requested)


@pytest.mark.asyncio
async def test_float32_rounding_is_not_mistaken_for_a_refused_write():
    coord = _coord()
    coord.client.read_register = AsyncMock(return_value=2000.0999755859375)
    await entity(coord).async_set_native_value(2000.1)
    coord.async_write_register.assert_awaited_once_with(mod.REG_BAT_MAX_CHARGE_LIMIT, 2000.1)


@pytest.mark.asyncio
async def test_a_terminal_client_read_failure_is_not_retried_or_refreshed():
    coord = _coord()
    coord.client.read_register = AsyncMock(side_effect=mod.ModbusClientError("Illegal address"))
    with pytest.raises(mod.ModbusClientError, match="unconfirmed"):
        await entity(coord).async_set_native_value(2000)
    coord.client.read_register.assert_awaited_once_with(mod.REG_BAT_MAX_CHARGE_LIMIT)
    coord.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["reconnect", "offline", "refresh_failed"])
@pytest.mark.parametrize("cyclic", [False, True])
async def test_refresh_cannot_reuse_confirmation_from_an_invalidated_connection(failure, cyclic):
    coord = _coord()
    coord.client.read_register = AsyncMock(return_value=2000)
    e = entity(coord)
    if cyclic:
        e._register = mod.REG_G3_MAX_CHARGE
    e._start_keepalive = MagicMock()

    async def invalidate():
        if failure == "reconnect":
            coord.client.connection_generation += 1
        elif failure == "offline":
            coord.client.connected = False
        else:
            coord.last_update_success = False

    coord.async_request_refresh = AsyncMock(side_effect=invalidate)
    with pytest.raises(mod.ModbusClientError, match="invalidated during refresh"):
        await e.async_set_native_value(2000)
    coord.async_write_register.assert_awaited_once_with(e._register, 2000)
    e._start_keepalive.assert_not_called()


@pytest.mark.asyncio
async def test_delayed_device_feedback_can_confirm_within_bounded_retry():
    coord = _coord()
    coord.client.read_register = AsyncMock(side_effect=[8000, 2000])
    await entity(coord).async_set_native_value(2000)
    assert coord.client.read_register.await_count == 2
    coord.async_write_register.assert_awaited_once_with(mod.REG_BAT_MAX_CHARGE_LIMIT, 2000)
    coord.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("quality", ["retained", "reconnected"])
async def test_matching_but_untrusted_feedback_cannot_confirm(quality):
    coord = _coord()
    coord.client.retained_quality_addresses = (
        {mod.REG_BAT_MAX_CHARGE_LIMIT.address} if quality == "retained" else set()
    )

    async def read(_register):
        if quality == "reconnected":
            coord.client.connection_generation = 2
        return 2000

    coord.client.read_register = AsyncMock(side_effect=read)
    with pytest.raises(mod.ModbusClientError, match="unconfirmed"):
        await entity(coord).async_set_native_value(2000)
    coord.async_write_register.assert_awaited_once_with(mod.REG_BAT_MAX_CHARGE_LIMIT, 2000)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 10001])
async def test_invalid_commands_raise_without_writing(value):
    coord = _coord()
    with pytest.raises(ServiceValidationError):
        await entity(coord).async_set_native_value(value)
    coord.async_write_register.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_only_command_is_not_reported_as_success():
    coord = _coord()
    with pytest.raises(ServiceValidationError, match="external Modbus"):
        await entity(coord, read_only=True).async_set_native_value(2000)
    coord.async_write_register.assert_not_awaited()
