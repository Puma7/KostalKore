"""Regressions from a G3 night log (2026-10-21/22, Puma7/KostalKore#85)."""

from __future__ import annotations

import time
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
    # Exactly half a step off never confirms (isclose is inclusive).
    entity._attr_native_step = 100
    assert not entity._readback_matches(9950.0, 10000)
    assert entity._readback_matches(9951.0, 10000)
    entity._attr_native_step = 0
    # A zero read-back cannot confirm a small nonzero limit inside the 5 W floor.
    assert not entity._readback_matches(0.0, 5)
    assert entity._readback_matches(0.0, 0)
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


@pytest.mark.asyncio
async def test_proxy_never_serves_a_zero_rejected_later_in_the_poll(hass) -> None:
    """Fast batch reads 0 Hz, the slow batch then answers 0x04: the proxy must
    not publish the zero while the slow batch is still in flight."""
    client = _modbus_client()
    client.device_failure_count = 0
    client.retained_quality_addresses = set()
    client.observation_generations = {}
    seen_by_proxy: list[dict] = []
    coordinator = ModbusDataUpdateCoordinator(hass, client)
    coordinator._slow_tick = 5  # this poll also runs the slow batch
    calls = {"n": 0}

    async def _batch(registers):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"grid_frequency": 0.0, "phase2_voltage": 231.0}
        seen_by_proxy.append(coordinator.proxy_cache_data)
        client.device_failure_count += 1
        return {}

    client.read_registers_batch = AsyncMock(side_effect=_batch)
    data = await coordinator._async_poll_data()
    assert calls["n"] == 2
    assert "grid_frequency" not in data and data["phase2_voltage"] == 231.0
    assert "grid_frequency" not in seen_by_proxy[0]
    assert coordinator.proxy_cache_data == {"phase2_voltage": 231.0}
    assert "grid_frequency" not in coordinator._proxy_observations
    assert coordinator._proxy_observations["phase2_voltage"][0] == 231.0


@pytest.mark.asyncio
async def test_device_failure_during_device_info_read_still_drops_zeros(hass) -> None:
    client = _modbus_client()
    client.device_failure_count = 0
    client.retained_quality_addresses = set()
    client.observation_generations = {}
    client.read_registers_batch = AsyncMock(return_value={"grid_frequency": 0.0})

    async def _info(_register):
        client.device_failure_count += 1  # the scheduled device-info read hits 0x04
        return 0

    client.read_register = AsyncMock(side_effect=_info)
    coordinator = ModbusDataUpdateCoordinator(hass, client)
    coordinator._device_info_tick = 59
    data = await coordinator._async_poll_data()
    client.read_register.assert_awaited()
    assert "grid_frequency" not in data
    assert "grid_frequency" not in coordinator.proxy_cache_data


@pytest.mark.asyncio
async def test_persisting_zero_with_device_failures_is_passed_on(hass) -> None:
    """A real outage can coincide with 0x04 answers: drop at most two polls."""
    client = _modbus_client()
    client.device_failure_count = 0
    client.retained_quality_addresses = set()
    client.observation_generations = {}
    sample = {"grid_frequency": 0.0}
    failing = {"on": True}

    async def _batch(registers):
        if failing["on"]:
            client.device_failure_count += 1
        names = {r.name for r in registers}
        return {k: v for k, v in sample.items() if k in names}

    client.read_registers_batch = AsyncMock(side_effect=_batch)
    coordinator = ModbusDataUpdateCoordinator(hass, client)
    seen = [("grid_frequency" in await coordinator._async_poll_data()) for _ in range(4)]
    assert seen == [False, False, True, True]
    assert coordinator.proxy_cache_data["grid_frequency"] == 0.0
    # A plausible reading resets the streak; the next glitch is dropped again.
    sample["grid_frequency"] = 50.0
    assert (await coordinator._async_poll_data())["grid_frequency"] == 50.0
    sample["grid_frequency"] = 0.0
    assert "grid_frequency" not in await coordinator._async_poll_data()
    # Without a device-failure answer a zero passes and resets the streak too.
    failing["on"] = False
    assert (await coordinator._async_poll_data())["grid_frequency"] == 0.0
    assert "grid_frequency" not in coordinator._zero_drop_streak


@pytest.mark.asyncio
async def test_only_suspect_zeros_are_held_back_from_the_proxy(hass) -> None:
    """Other registers are published at once; a suspect zero keeps the previous
    observation until the filter ran, so the proxy does not forward a raw read."""
    client = _modbus_client()
    client.device_failure_count = 0
    client.retained_quality_addresses = set()
    client.observation_generations = {}
    coordinator = ModbusDataUpdateCoordinator(hass, client)
    coordinator._proxy_observations["grid_frequency"] = (50.0, time.monotonic(), 0)
    coordinator._slow_tick = 5
    seen: list[dict] = []
    calls = {"n": 0}

    async def _batch(registers):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"grid_frequency": 0.0, "phase2_voltage": 231.0}
        seen.append(coordinator.proxy_cache_data)
        return {}

    client.read_registers_batch = AsyncMock(side_effect=_batch)
    data = await coordinator._async_poll_data()
    assert seen[0]["phase2_voltage"] == 231.0 and seen[0]["grid_frequency"] == 50.0
    # No device failure in this poll: the zero is real and now published.
    assert data["grid_frequency"] == 0.0
    assert coordinator.proxy_cache_data["grid_frequency"] == 0.0
