"""Energy counter magnitudes must not turn a working poll into a link fault."""
from __future__ import annotations

import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.kostal_kore.modbus_client import KostalModbusClient, ModbusReadError
from custom_components.kostal_kore.modbus_coordinator import ModbusDataUpdateCoordinator
from custom_components.kostal_kore.modbus_number import create_modbus_number_entities
from custom_components.kostal_kore.modbus_registers import (
    MONITORING_REGISTERS,
    REG_BAT_MAX_CHARGE_LIMIT,
    REG_BAT_MIN_SOC,
    REG_BATTERY_SOC,
    REG_BATTERY_WORK_CAPACITY,
    REG_TOTAL_DC_POWER,
    REG_TOTAL_YIELD,
    RegisterGroup,
)

ENERGY_COUNTERS = [r for r in MONITORING_REGISTERS if r.group == RegisterGroup.ENERGY and r.unit == "Wh"]


@pytest.mark.parametrize("register", ENERGY_COUNTERS, ids=lambda r: r.name)
@pytest.mark.parametrize("cached", [False, True], ids=["cold", "crossing-10MWh"])
def test_large_energy_totals_are_fresh_instead_of_rejected_or_frozen(register, cached):
    client = KostalModbusClient("127.0.0.1")
    if cached:
        assert client._apply_quality_filter(register, 8_000_000.0) == 8_000_000.0
        client._last_good_values[register.address] = 8_000_000.0
        client.retained_quality_addresses.add(register.address)
    assert client._apply_quality_filter(register, 20_000_000.0) == 20_000_000.0
    assert register.address not in client.retained_quality_addresses


@pytest.mark.parametrize("value", [-1.0, float("nan"), float("inf"), float("-inf"), 1e12])
@pytest.mark.parametrize("cached", [False, True])
def test_invalid_energy_still_fails_or_retains_explicitly_stale_feedback(value, cached):
    client = KostalModbusClient("127.0.0.1")
    if cached:
        client._last_good_values[REG_TOTAL_YIELD.address] = 1000.0
        assert client._apply_quality_filter(REG_TOTAL_YIELD, value) == 1000.0
        assert REG_TOTAL_YIELD.address in client.retained_quality_addresses
    else:
        with pytest.raises(ModbusReadError):
            client._apply_quality_filter(REG_TOTAL_YIELD, value)


@pytest.mark.parametrize("register", [REG_TOTAL_DC_POWER, REG_BATTERY_WORK_CAPACITY], ids=lambda r: r.name)
def test_power_and_capacity_do_not_inherit_the_cumulative_energy_allowance(register):
    client = KostalModbusClient("127.0.0.1")
    with pytest.raises(ModbusReadError, match="absolute outlier limit"):
        client._apply_quality_filter(register, 20_000_000.0)


@pytest.mark.asyncio
async def test_real_client_quality_controls_partial_poll_and_number_availability(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    client = KostalModbusClient("127.0.0.1", endianness="big")
    client._client = SimpleNamespace(connected=True)
    client._endianness_confirmed = True
    client._raw_write = AsyncMock()
    energy = 20_000_000.0

    async def raw_read(address, count):
        if address == REG_BATTERY_SOC.address:
            assert count == 1
            return struct.pack(">H", 20)
        assert count == 2
        values = {REG_TOTAL_YIELD.address: energy, REG_BAT_MAX_CHARGE_LIMIT.address: 5000.0,
                  REG_BAT_MIN_SOC.address: 8.0}
        return struct.pack(">f", values[address])

    client._raw_read = AsyncMock(side_effect=raw_read)
    with patch("homeassistant.helpers.frame.report_usage", create=True):
        coordinator = ModbusDataUpdateCoordinator(hass, client)
    coordinator._save_register_capability_state_if_changed = AsyncMock()
    coordinator._report_communication = AsyncMock()
    entities = await create_modbus_number_entities(coordinator, "synthetic", {})
    charge = next(e for e in entities if e._register == REG_BAT_MAX_CHARGE_LIMIT)
    registers = (REG_BATTERY_SOC, REG_TOTAL_YIELD, REG_BAT_MAX_CHARGE_LIMIT, REG_BAT_MIN_SOC)

    with patch("custom_components.kostal_kore.modbus_coordinator.MONITORING_REGISTERS", registers):
        for energy, stale in [(20_000_000.0, False), (float("nan"), True), (20_000_000.0, False)]:
            coordinator._slow_tick = 5
            data = await coordinator._async_update_data()
            coordinator.async_set_updated_data(data)
            assert coordinator.slow_poll_stale is stale
            assert charge.available is not stale
            assert charge.native_value == 5000.0
            coordinator._report_communication.assert_awaited_with(["modbus_partial"] if stale else [])
            if not stale:
                assert data[REG_TOTAL_YIELD.name] == 20_000_000.0
    client._raw_write.assert_not_awaited()
