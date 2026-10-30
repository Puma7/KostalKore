"""REG 1038 reads the limit in effect, not the command (Puma7/KostalKore#87)."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.kostal_kore import modbus_number as mod
from custom_components.kostal_kore import sensor as sensor_mod
from custom_components.kostal_kore.modbus_client import ModbusClientError, ReadbackUnconfirmedError
from Tests.test_wave9_modbus_number_coverage import _coord, _device_info


def _limit_entity(coord, register=mod.REG_BAT_MAX_CHARGE_LIMIT):
    entity = mod.ModbusNumberEntity(
        coordinator=coord, register=register, name="Limit", icon="mdi:battery",
        min_value=0, max_value=12540, step=100, unit="W", device_class=None,
        entity_category=None, entry_id="night", device_info=_device_info(),
    )
    # The coordinator records every command (value and revision); the fixture
    # mirrors that.
    coord._commanded = None
    coord._revision = 0
    coord.async_write_register = AsyncMock(
        side_effect=lambda _reg, value: _command(coord, value))
    coord.last_commanded = MagicMock(side_effect=lambda _name: coord._commanded)
    coord.command_revision = MagicMock(side_effect=lambda _name: coord._revision)
    return entity


def _command(coord, value):
    coord._commanded = value
    coord._revision += 1


# Read-backs from the night of 24/25 October 2026.
@pytest.mark.asyncio
async def test_a_limit_lifted_by_a_forced_charge_is_unconfirmed(caplog) -> None:
    coord = _coord()
    coord.client.read_register = AsyncMock(return_value=21460.23046875)  # Battery Manual Charge on
    entity = _limit_entity(coord)
    with caplog.at_level(logging.WARNING), pytest.raises(ReadbackUnconfirmedError) as err:
        await entity.async_set_native_value(1000)
    assert isinstance(err.value, ModbusClientError)
    assert str(err.value).startswith("Readback is unconfirmed for bat_max_charge_limit")
    assert (err.value.register, err.value.commanded, err.value.observed, err.value.outcome) == (
        "bat_max_charge_limit", 1000, 21460.23046875, mod.READBACK_ABOVE_COMMAND)
    assert mod._READBACK_HINTS[mod.READBACK_ABOVE_COMMAND] in caplog.text
    assert entity.extra_state_attributes == {
        "last_commanded_value": 1000,
        "last_command_outcome": mod.READBACK_ABOVE_COMMAND,
        "last_command_readback": 21460.23046875,
    }


# The BMS lowers the charge limit as the battery fills and the discharge
# limit as it empties (HV8): a read below the command is the command applied.
@pytest.mark.asyncio
@pytest.mark.parametrize("register", [mod.REG_BAT_MAX_CHARGE_LIMIT, mod.REG_BAT_MAX_DISCHARGE_LIMIT])
@pytest.mark.parametrize(("previous", "written", "observed"), [
    (None, 1000, 0.0),                   # ManualCharge off again
    (1000, 1000, 244.8936004638672),     # BMS near full charge
    (1000, 12540, 52.64067840576172),    # restore, limit not yet lifted
])
async def test_a_bms_limited_read_is_an_applied_command(register, previous, written, observed, caplog) -> None:
    coord = _coord()
    coord.client.read_register = AsyncMock(return_value=observed)
    entity = _limit_entity(coord, register=register)
    coord._commanded = previous
    coord.data = {register.name: observed}
    with caplog.at_level(logging.INFO):
        await entity.async_set_native_value(written)
    assert "currently allows" in caplog.text
    assert "Read-back mismatch" not in caplog.text
    assert entity.extra_state_attributes == {
        "last_commanded_value": written,
        "last_command_outcome": mod.COMMAND_LIMITED,
        "last_command_readback": observed,
    }


@pytest.mark.asyncio
async def test_a_limited_first_read_is_confirmed_by_a_matching_refresh() -> None:
    coord = _coord()
    coord.client.read_register = AsyncMock(return_value=245.0)
    entity = _limit_entity(coord)
    coord.data = {mod.REG_BAT_MAX_CHARGE_LIMIT.name: 999.0}
    await entity.async_set_native_value(1000)
    # The pair reports the completed poll the outcome was decided on.
    assert entity.extra_state_attributes == {
        "last_commanded_value": 1000,
        "last_command_outcome": mod.COMMAND_CONFIRMED,
        "last_command_readback": 999.0,
    }


@pytest.mark.asyncio
async def test_a_matching_first_read_can_end_limited_by_the_refresh() -> None:
    coord = _coord()
    coord.client.read_register = AsyncMock(return_value=1000.0)
    entity = _limit_entity(coord)
    coord.data = {mod.REG_BAT_MAX_CHARGE_LIMIT.name: 245.0}
    await entity.async_set_native_value(1000)
    assert entity.extra_state_attributes["last_command_outcome"] == mod.COMMAND_LIMITED
    assert entity.extra_state_attributes["last_command_readback"] == 245.0


@pytest.mark.asyncio
async def test_a_raised_limit_that_reads_the_old_command_was_ignored() -> None:
    coord = _coord()
    coord.client.read_register = AsyncMock(return_value=1000.0)
    entity = _limit_entity(coord)
    coord._commanded = 1000
    with pytest.raises(ReadbackUnconfirmedError) as err:
        await entity.async_set_native_value(12540)
    assert err.value.outcome == mod.READBACK_BELOW_COMMAND


@pytest.mark.asyncio
async def test_a_retried_ignored_raise_is_still_ignored() -> None:
    coord = _coord()
    coord.client.read_register = AsyncMock(return_value=1000.0)
    entity = _limit_entity(coord)
    coord._commanded = 1000
    for _ in range(2):  # the coordinator now records 2000 as the last command
        with pytest.raises(ReadbackUnconfirmedError) as err:
            await entity.async_set_native_value(2000)
        assert err.value.outcome == mod.READBACK_BELOW_COMMAND
    # Once the inverter takes it, the baseline is dropped.
    coord.client.read_register = AsyncMock(return_value=2000.0)
    coord.data = {mod.REG_BAT_MAX_CHARGE_LIMIT.name: 2000.0}
    await entity.async_set_native_value(2000)
    assert entity._ignored_baseline is None


@pytest.mark.asyncio
async def test_another_write_path_drops_the_ignored_baseline() -> None:
    coord = _coord()
    coord.client.read_register = AsyncMock(return_value=1000.0)
    entity = _limit_entity(coord)
    coord._commanded = 1000
    with pytest.raises(ReadbackUnconfirmedError):
        await entity.async_set_native_value(2000)
    _command(coord, 3000)  # e.g. MQTT raises it further
    coord.data = {mod.REG_BAT_MAX_CHARGE_LIMIT.name: 1000.0}
    # Compared with the newer 3000 W command, a read of 1000 W cannot be an
    # ignored 2000 W command: it is limited.
    await entity.async_set_native_value(2000)
    assert entity.extra_state_attributes["last_command_outcome"] == mod.COMMAND_LIMITED


def test_only_1038_and_1040_read_the_limit_in_effect() -> None:
    soc = _limit_entity(_coord(), register=mod.REG_BAT_MIN_SOC)
    assert not soc._readback_limited(70.0, 80.0, None)
    limit = _limit_entity(_coord())
    assert not limit._readback_limited(-1.0, 1000.0, None)
    assert not limit._readback_limited(1001.0, 1000.0, None)
    assert limit._readback_limited(500.0, 1000.0, 2000.0)  # lowered, not raised


@pytest.mark.asyncio
async def test_unreadable_readback_is_its_own_outcome() -> None:
    coord = _coord()
    coord.client.read_register = AsyncMock(side_effect=ModbusClientError("Illegal address"))
    entity = _limit_entity(coord)
    with pytest.raises(ReadbackUnconfirmedError) as err:
        await entity.async_set_native_value(1000)
    assert (err.value.observed, err.value.outcome) == (None, mod.READBACK_UNREADABLE)
    assert "read n/a" in str(err.value)
    assert entity.extra_state_attributes["last_command_outcome"] == mod.READBACK_UNREADABLE


@pytest.mark.asyncio
async def test_cyclic_limit_raises_the_typed_error_too() -> None:
    coord = _coord()
    coord.client.read_register = AsyncMock(return_value=0.0)
    entity = _limit_entity(coord, register=mod.REG_G3_MAX_CHARGE)
    with pytest.raises(ReadbackUnconfirmedError, match="keepalive was not started") as err:
        await entity.async_set_native_value(1000)
    assert err.value.outcome == mod.READBACK_BELOW_COMMAND


@pytest.mark.asyncio
async def test_confirmed_and_invalidated_commands_are_reported() -> None:
    coord = _coord(data={mod.REG_BAT_MAX_CHARGE_LIMIT.name: 999.0})
    coord.client.read_register = AsyncMock(return_value=999.0)
    entity = _limit_entity(coord)
    assert entity.extra_state_attributes is None  # nothing commanded yet
    await entity.async_set_native_value(1000)
    assert entity.extra_state_attributes == {
        "last_commanded_value": 1000,
        "last_command_outcome": mod.COMMAND_CONFIRMED,
        "last_command_readback": 999.0,
    }

    async def drop_link():
        coord.client.connected = False

    coord.async_refresh = AsyncMock(side_effect=drop_link)
    with pytest.raises(ModbusClientError, match="invalidated during refresh"):
        await entity.async_set_native_value(1000)
    assert entity.extra_state_attributes["last_command_outcome"] == mod.COMMAND_INVALIDATED


@pytest.mark.asyncio
async def test_outcome_is_hidden_once_another_path_commands_a_new_value() -> None:
    coord = _coord()
    coord.client.read_register = AsyncMock(return_value=21460.0)
    entity = _limit_entity(coord)
    with pytest.raises(ReadbackUnconfirmedError):
        await entity.async_set_native_value(1000)
    _command(coord, 2000.0)  # e.g. a proxy or MQTT write after the entity write
    assert entity.extra_state_attributes == {"last_commanded_value": 2000.0}


@pytest.mark.asyncio
async def test_a_later_write_of_the_same_value_hides_the_old_outcome() -> None:
    coord = _coord()
    coord.client.read_register = AsyncMock(return_value=21460.0)
    entity = _limit_entity(coord)
    with pytest.raises(ReadbackUnconfirmedError):
        await entity.async_set_native_value(1000)
    _command(coord, 1000)  # the same value, written by MQTT or the proxy
    assert entity.extra_state_attributes == {"last_commanded_value": 1000}


def test_coordinator_counts_every_recorded_command(hass) -> None:
    from custom_components.kostal_kore.modbus_coordinator import ModbusDataUpdateCoordinator
    from Tests.test_wave7_modbus_core_coverage import _modbus_client

    coordinator = ModbusDataUpdateCoordinator(hass, _modbus_client())
    name = mod.REG_BAT_MAX_CHARGE_LIMIT.name
    assert coordinator.command_revision(name) == 0
    coordinator._record_commanded(name, 1000)
    coordinator._record_commanded(name, 1000)
    assert coordinator.command_revision(name) == 2
    coordinator._record_commanded(name, "bad")
    assert coordinator.command_revision(name) == 3
    assert coordinator.last_commanded(name) is None


# ---------------------------------------------------------------------------
# 1046 / 1054 as energy sensors
# ---------------------------------------------------------------------------


def test_energy_counters_are_registered_for_1046_and_1054() -> None:
    assert {key for key, _ in sensor_mod.MODBUS_ENERGY_SENSORS} == {
        "total_dc_charge", "total_ac_charge_grid"}


def test_energy_counter_converts_and_never_runs_backwards() -> None:
    coord = SimpleNamespace(data=None)
    sensor = sensor_mod.ModbusEnergySensor(
        coord, "entry", {"identifiers": {("kostal_kore", "abc")}},
        key="total_ac_charge_grid", name="AC charge")
    assert sensor.unique_id == "entry_modbus_energy_total_ac_charge_grid"
    assert sensor.native_value is None
    coord.data = {}
    assert sensor.native_value is None
    coord.data = {"total_ac_charge_grid": 1_234_567.0}
    assert sensor.native_value == pytest.approx(1234.567)
    # Read faults and a lower reading keep the last good total.
    for glitch in (0.0, -5.0, float("nan"), "bad", None, 1_000_000.0):
        coord.data = {"total_ac_charge_grid": glitch}
        assert sensor.native_value == pytest.approx(1234.567)
    coord.data = {"total_ac_charge_grid": 1_235_000.0}
    assert sensor.native_value == pytest.approx(1235.0)


@pytest.mark.asyncio
@pytest.mark.parametrize(("restored", "expected"), [
    (1234.5, 1234.5),   # a low first reading after a reload is not a new total
    (None, 1000.0),
    ("bad", 1000.0),
    (0.0, 1000.0),
])
async def test_energy_counter_restores_its_total_after_a_reload(restored, expected) -> None:
    from unittest.mock import patch

    coord = SimpleNamespace(data={"total_dc_charge": 1_000_000.0})
    sensor = sensor_mod.ModbusEnergySensor(
        coord, "entry", {"identifiers": {("kostal_kore", "abc")}},
        key="total_dc_charge", name="DC charge")
    last = None if restored is None else SimpleNamespace(native_value=restored)
    with (
        patch.object(sensor_mod.CoordinatorEntity, "async_added_to_hass", AsyncMock()),
        patch.object(sensor, "async_get_last_sensor_data", AsyncMock(return_value=last)),
    ):
        await sensor.async_added_to_hass()
    assert sensor.native_value == pytest.approx(expected)


def test_an_older_write_finishing_late_keeps_the_newer_outcome() -> None:
    coord = _coord()
    entity = _limit_entity(coord)
    _command(coord, 1000)  # revision 1: older write
    _command(coord, 2000)  # revision 2: newer write, finishes first
    entity._record_last_command(2, mod.COMMAND_CONFIRMED, 1999.0)
    entity._record_last_command(1, mod.READBACK_BELOW_COMMAND, 0.0)
    assert entity.extra_state_attributes == {
        "last_commanded_value": 2000,
        "last_command_outcome": mod.COMMAND_CONFIRMED,
        "last_command_readback": 1999.0,
    }


@pytest.mark.asyncio
async def test_keepalive_rewrite_is_not_a_new_command(hass) -> None:
    from custom_components.kostal_kore.modbus_coordinator import ModbusDataUpdateCoordinator
    from Tests.test_wave7_modbus_core_coverage import _modbus_client

    coordinator = ModbusDataUpdateCoordinator(hass, _modbus_client())
    register = mod.REG_G3_MAX_CHARGE
    await coordinator.async_write_register(register, 1000)
    await coordinator.async_write_register(register, 1000, record_command=False)
    assert coordinator.command_revision(register.name) == 1
    assert coordinator.last_commanded(register.name) == 1000



def _info_lines(caplog, text: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.INFO and text in r.getMessage()]


@pytest.mark.asyncio
async def test_a_kept_alive_limit_logs_info_only_for_a_change(caplog) -> None:
    """Night of 29/30 Oct 2026: a controller re-writes 3000 W every ~23 s (Puma7/KostalKore#94)."""
    coord = _coord()
    entity = _limit_entity(coord)

    async def write(observed: float) -> None:
        coord.client.read_register = AsyncMock(return_value=observed)
        coord.data = {mod.REG_BAT_MAX_CHARGE_LIMIT.name: observed}
        await entity.async_set_native_value(3000)

    with caplog.at_level(logging.DEBUG):
        # The battery settles on its raised min SOC: the limit in effect drops to about 50 W.
        for observed in (1550.61, 122.45, 65.23, 56.07, 51.5, 51.5):
            await write(observed)
        assert _info_lines(caplog, "Writing bat_max_charge_limit") == [
            "Writing bat_max_charge_limit = 3000 via Modbus (register 1038)"]
        allows = _info_lines(caplog, "currently allows")
        assert [line.split("currently allows ")[1].split(" ")[0] for line in allows] == ["1550.61", "122.45"]
        assert "the charge limit reads about 50 W while the battery rests at its min SOC" in allows[0]
        assert "BMS taper" not in caplog.text
        assert sum("currently allows" in r.getMessage() for r in caplog.records) == 6

        caplog.clear()
        await write(3000.0)  # the battery charges at the command again
        await write(51.5)
        assert len(_info_lines(caplog, "currently allows")) == 1
        caplog.clear()
        await entity.async_set_native_value(5000)  # a new command
        assert len(_info_lines(caplog, "Writing bat_max_charge_limit")) == 1
        assert len(_info_lines(caplog, "currently allows")) == 1

        caplog.clear()
        coord._commanded = 5000  # replaced by another write path (proxy, MQTT)
        await write(51.5)
        coord._commanded = None  # reconnect: the coordinator forgets the command
        await write(51.5)
        assert len(_info_lines(caplog, "currently allows")) == 2


@pytest.mark.asyncio
async def test_coordinator_logs_a_repeated_write_at_debug(caplog) -> None:
    from custom_components.kostal_kore.modbus_coordinator import ModbusDataUpdateCoordinator

    coordinator = ModbusDataUpdateCoordinator.__new__(ModbusDataUpdateCoordinator)
    coordinator._client = SimpleNamespace(write_register=AsyncMock(), connection_generation=1)
    coordinator._proxy_observations = {}
    coordinator._write_audit = None
    coordinator._last_commanded = {}
    coordinator._command_revision = {}
    reg = mod.REG_BAT_MAX_CHARGE_LIMIT

    with caplog.at_level(logging.DEBUG):
        await coordinator.async_write_register(reg, 3000.0)  # new command
        await coordinator.async_write_register(reg, 3000.0)  # kept alive
        await coordinator.async_write_register(reg, 3000.0, record_command=False)  # keepalive re-write
        coordinator._client.connection_generation = 2  # reconnect: the inverter may have dropped it
        await coordinator.async_write_register(reg, 3000.0)
        await coordinator.async_write_register(reg, 5000.0)  # changed command
    lines = [(r.levelno, r.getMessage()) for r in caplog.records if r.getMessage().startswith("Wrote ")]
    assert [level for level, _ in lines] == [logging.INFO, logging.DEBUG, logging.DEBUG, logging.INFO, logging.INFO]
    assert coordinator._command_revision[reg.name] == 4
