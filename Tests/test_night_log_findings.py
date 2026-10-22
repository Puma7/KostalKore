"""Regressions from a G3 night log (2026-10-21/22, Puma7/KostalKore#85)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.kostal_kore import modbus_number as mod
from custom_components.kostal_kore.modbus_client import (
    KostalModbusClient,
    ModbusExceptionCode,
    ModbusTransientError,
)
from custom_components.kostal_kore.modbus_coordinator import ModbusDataUpdateCoordinator
from Tests.test_wave7_modbus_core_coverage import _modbus_client
from Tests.test_wave9_modbus_number_coverage import _coord, _device_info


def _limit_entity(coord, register=mod.REG_BAT_MAX_CHARGE_LIMIT):
    entity = mod.ModbusNumberEntity(
        coordinator=coord, register=register, name="Limit", icon="mdi:battery",
        min_value=0, max_value=10000, step=100, unit="W", device_class=None,
        entity_category=None, entry_id="night", device_info=_device_info(),
    )
    return entity


# ---------------------------------------------------------------------------
# REG 1038 read-back: the inverter reports current x voltage, not the command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("observed", [997.873046875, 998.4373779296875, 999.9298706054688])
async def test_power_limit_readback_drift_confirms_the_write(observed) -> None:
    """1000 W read back as 997.9-999.9 W all night; that is an applied limit."""
    coord = _coord(data={mod.REG_BAT_MAX_CHARGE_LIMIT.name: observed})
    coord.client.read_register = AsyncMock(return_value=observed)
    await _limit_entity(coord).async_set_native_value(1000)
    coord.async_write_register.assert_awaited_once_with(mod.REG_BAT_MAX_CHARGE_LIMIT, 1000)


@pytest.mark.asyncio
@pytest.mark.parametrize(("requested", "observed"), [
    (1000, 51.49608612060547),   # the inverter really held ~51 W from 23:31 on
    (1000, 141.90097045898438),
    (2000, 1900),                # an ignored one-step command
    (1000, 1006),                # beyond 0.5 % / 5 W
])
async def test_power_limit_readback_still_rejects_real_mismatches(requested, observed) -> None:
    coord = _coord()
    coord.client.read_register = AsyncMock(return_value=observed)
    with pytest.raises(mod.ModbusClientError, match="unconfirmed"):
        await _limit_entity(coord).async_set_native_value(requested)


def test_readback_tolerance_is_capped_below_half_a_step() -> None:
    entity = _limit_entity(_coord())
    entity._attr_native_step = 20
    # 0.5 % of 20 kW would be 100 W; the step caps it at 10 W.
    assert entity._readback_matches(20009.0, 20000)
    assert not entity._readback_matches(20011.0, 20000)
    entity._attr_native_step = 0
    assert entity._readback_matches(20090.0, 20000)
    # Non-power registers (SoC %) stay exact; 0 must read back as 0.
    entity._register = mod.REG_BAT_MIN_SOC
    assert not entity._readback_matches(21.9, 22)
    assert entity._readback_matches(0, 0) and not entity._readback_matches(1, 0)


# ---------------------------------------------------------------------------
# 03:10 "Server device failure": no 0 Hz / 0 V / 0 % published
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_raw_read_counts_server_device_failures() -> None:
    c = KostalModbusClient("127.0.0.1")
    client = AsyncMock()
    client.connected = True
    err = MagicMock()
    err.isError.return_value = True
    err.exception_code = ModbusExceptionCode.SERVER_DEVICE_FAILURE
    err.function_code = 0x83
    client.read_holding_registers = AsyncMock(return_value=err)
    c._client = client
    with pytest.raises(ModbusTransientError):
        await c._raw_read_inner(586, 2)
    assert c.device_failure_count == 1
    err.exception_code = ModbusExceptionCode.SERVER_DEVICE_BUSY
    with pytest.raises(ModbusTransientError):
        await c._raw_read_inner(586, 2)
    assert c.device_failure_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("device_failure", [True, False])
async def test_zero_readings_in_a_device_failure_poll_are_dropped(hass, device_failure) -> None:
    client = _modbus_client()
    client.device_failure_count = 0
    sample = {
        "grid_frequency": 0.0, "phase1_voltage": 0.0, "phase2_voltage": 231.0,
        "battery_soc": 0, "total_dc_power": 0.0,
    }

    async def _batch(registers):
        if device_failure:
            client.device_failure_count += 1  # 586 / 1076 answered 0x04
        return dict(sample)

    client.read_registers_batch = AsyncMock(side_effect=_batch)
    coordinator = ModbusDataUpdateCoordinator(hass, client)
    coordinator._proxy_observations["grid_frequency"] = (0.0, 0.0, 0)
    data = await coordinator._async_poll_data()
    if device_failure:
        assert not {"grid_frequency", "phase1_voltage", "battery_soc"} & set(data)
        assert data["phase2_voltage"] == 231.0 and data["total_dc_power"] == 0.0
        assert "grid_frequency" not in coordinator._proxy_observations
    else:
        # Without the inverter's own error report a 0 is passed on as read.
        assert data["grid_frequency"] == 0.0 and data["battery_soc"] == 0
