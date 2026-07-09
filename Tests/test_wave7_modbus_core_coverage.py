"""Coverage tests for omitted Modbus coordinator and button modules."""

from __future__ import annotations

import json
import math
from datetime import timedelta  # noqa: F401
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.kostal_kore.const import DOMAIN
from custom_components.kostal_kore.modbus_button import (
    BatteryTestButton,
    ModbusDiagnosticsButton,
    ModbusResetButton,
    create_modbus_buttons,
)
from custom_components.kostal_kore.modbus_client import (
    ModbusClientError,
    ModbusConnectionError,
    ModbusPermanentError,
)
from custom_components.kostal_kore.modbus_coordinator import ModbusDataUpdateCoordinator
from custom_components.kostal_kore.modbus_registers import (
    REG_ACTIVE_POWER_SETPOINT,
    Access,
    DataType,
    ModbusRegister,
    RegisterGroup,
)


def _device_info() -> dict[str, str]:
    return {"identifiers": {("kostal_kore", "abc")}}


def _modbus_reg(
    address: int,
    name: str,
    *,
    group: RegisterGroup,
    access: Access = Access.RO,
    data_type: DataType = DataType.FLOAT32,
    count: int = 2,
    unit: str | None = None,
) -> ModbusRegister:
    return ModbusRegister(address, name, name, data_type, count, access, group, unit)


def _modbus_client() -> MagicMock:
    client = MagicMock()
    client.host = "1.2.3.4"
    client.port = 1502
    client.unit_id = 71
    client.connected = True
    client.closing = False
    client.connection_generation = 0
    client.connect = AsyncMock()
    client.detect_endianness = AsyncMock()
    client.disconnect = AsyncMock()
    client.async_shutdown = AsyncMock()
    client.read_register = AsyncMock()
    client.read_registers_batch = AsyncMock(return_value={})
    client.write_register = AsyncMock()
    client.write_by_name = AsyncMock()
    client.write_by_address = AsyncMock()
    client.import_unavailable_state = MagicMock()
    client.export_unavailable_state = MagicMock(return_value={})
    client.reset_unavailable = MagicMock()
    client.unavailable_registers = set()
    return client


@pytest.mark.asyncio
async def test_modbus_coordinator_setup_shutdown_and_capability_cache(hass) -> None:
    """Coordinator setup/shutdown and cache helpers should handle all main branches."""
    client = _modbus_client()
    coordinator = ModbusDataUpdateCoordinator(hass, client)
    assert coordinator.client is client
    assert coordinator.device_info_data == {}

    with patch.object(coordinator, "_read_device_info", new=AsyncMock()) as read_info, patch.object(
        coordinator, "_load_register_capability_state", new=AsyncMock()
    ) as load_caps:
        await coordinator.async_setup()
    client.connect.assert_awaited_once()
    client.detect_endianness.assert_awaited_once()
    read_info.assert_awaited_once()
    load_caps.assert_awaited_once()

    shutdown_order: list[str] = []

    async def _mark_client_shutdown() -> None:
        shutdown_order.append("client")

    client.async_shutdown = AsyncMock(side_effect=_mark_client_shutdown)

    with patch(
        "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.async_shutdown",
        new=AsyncMock(side_effect=lambda: shutdown_order.append("super")),
    ):
        await coordinator.async_shutdown()

    assert shutdown_order == ["client", "super"]

    coordinator._shutting_down = True
    client.closing = True
    coordinator.async_set_updated_data({"fast_ok": 10.0})
    coordinator._last_slow_data = {"slow_ok": 20.0}
    data = await coordinator._async_update_data()
    assert data == {"fast_ok": 10.0, "slow_ok": 20.0}

    coordinator._device_info = {"sw_version": "1.2.3"}
    assert coordinator._capability_signature() == "1.2.3.4:1502:71:1.2.3"

    coordinator._capability_store.async_load = AsyncMock(side_effect=RuntimeError("load failed"))
    await coordinator._load_register_capability_state()

    coordinator._capability_store.async_load = AsyncMock(return_value=None)
    await coordinator._load_register_capability_state()

    coordinator._capability_store.async_load = AsyncMock(
        return_value={"signature": "other", "state": {"a": 1}}
    )
    await coordinator._load_register_capability_state()

    coordinator._capability_store.async_load = AsyncMock(
        return_value={"signature": coordinator._capability_signature(), "state": {"10": 2}}
    )
    await coordinator._load_register_capability_state()
    client.import_unavailable_state.assert_called_once_with({"10": 2})
    assert coordinator._last_saved_capability_state == json.dumps({"10": 2}, sort_keys=True)

    client.import_unavailable_state.reset_mock()
    client.import_unavailable_state.side_effect = ValueError("bad state")
    await coordinator._load_register_capability_state()
    client.import_unavailable_state.side_effect = None

    coordinator._capability_store.async_load = AsyncMock(
        return_value={"signature": coordinator._capability_signature(), "state": ["not-a-dict"]}
    )
    await coordinator._load_register_capability_state()

    client.export_unavailable_state.return_value = {"x": 1}
    coordinator._last_saved_capability_state = json.dumps({"x": 1}, sort_keys=True)
    coordinator._capability_store.async_save = AsyncMock()
    await coordinator._save_register_capability_state_if_changed()
    coordinator._capability_store.async_save.assert_not_awaited()

    client.export_unavailable_state.return_value = {"y": 2}
    await coordinator._save_register_capability_state_if_changed()
    coordinator._capability_store.async_save.assert_awaited_once()
    assert coordinator._last_saved_capability_state == json.dumps({"y": 2}, sort_keys=True)

    coordinator._capability_store.async_save = AsyncMock(side_effect=RuntimeError("save failed"))
    client.export_unavailable_state.return_value = {"z": 3}
    await coordinator._save_register_capability_state_if_changed()


@pytest.mark.asyncio
async def test_modbus_coordinator_update_read_info_and_write_paths(hass) -> None:
    """Coordinator should cover reconnect, read loops and write validation."""
    client = _modbus_client()
    coordinator = ModbusDataUpdateCoordinator(hass, client)

    fast_ok = _modbus_reg(10, "fast_ok", group=RegisterGroup.POWER)
    slow_ok = _modbus_reg(20, "slow_ok", group=RegisterGroup.ENERGY)
    fast_perm = _modbus_reg(30, "fast_perm", group=RegisterGroup.BATTERY)
    fast_err = _modbus_reg(40, "fast_err", group=RegisterGroup.PHASE)

    # Batch returns only successful reads; fast_perm absent (1 of 2 fail → no UpdateFailed)
    async def _batch_normal(regs):
        return {r.name: r.address * 1.0 for r in regs if r.name in ("fast_ok", "slow_ok")}

    client.connected = False

    # A reconnect bumps the client's connection generation; a value cached on
    # the previous generation is no longer trusted (volatile setpoints do not
    # survive an inverter reset).
    async def _bump_connect() -> bool:
        client.connection_generation += 1
        return True

    client.connect = AsyncMock(side_effect=_bump_connect)
    client.read_registers_batch = AsyncMock(side_effect=_batch_normal)
    coordinator._slow_tick = 5
    coordinator._device_info_tick = 59
    # Prime a value tagged with the current (pre-reconnect) generation 0.
    coordinator._last_commanded["active_power_setpoint"] = (80.0, 0)
    with patch(
        "custom_components.kostal_kore.modbus_coordinator.MONITORING_REGISTERS",
        (fast_ok, fast_perm, slow_ok),
    ), patch.object(coordinator, "_read_device_info", new=AsyncMock()) as read_info, patch.object(
        coordinator, "_save_register_capability_state_if_changed", new=AsyncMock()
    ) as save_state:
        data = await coordinator._async_update_data()
    client.connect.assert_awaited()
    client.detect_endianness.assert_awaited()
    assert data == {"fast_ok": 10.0, "slow_ok": 20.0}
    read_info.assert_awaited_once()
    save_state.assert_awaited_once()
    assert coordinator.last_commanded("active_power_setpoint") is None  # cleared on reconnect

    # Connection error during reconnect → UpdateFailed
    client.connected = False
    client.connect = AsyncMock(side_effect=ModbusConnectionError("down"))
    with patch(
        "custom_components.kostal_kore.modbus_coordinator.MONITORING_REGISTERS",
        (fast_ok,),
    ):
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    # Connection lost during batch read → UpdateFailed
    client.connect = AsyncMock()
    client.detect_endianness = AsyncMock()
    client.connected = True
    client.read_registers_batch = AsyncMock(side_effect=ModbusConnectionError("lost"))
    with patch(
        "custom_components.kostal_kore.modbus_coordinator.MONITORING_REGISTERS",
        (fast_ok,),
    ):
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    # All fast registers fail (batch returns {}, none suppressed) → UpdateFailed
    client.read_registers_batch = AsyncMock(return_value={})
    client.unavailable_registers = set()
    with patch(
        "custom_components.kostal_kore.modbus_coordinator.MONITORING_REGISTERS",
        (fast_ok, fast_err),
    ):
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    # All fast registers permanently suppressed → returns {} without raising
    coordinator._slow_tick = 0
    coordinator._device_info_tick = 0
    client.read_registers_batch = AsyncMock(return_value={})
    client.unavailable_registers = {fast_perm.address}
    with patch(
        "custom_components.kostal_kore.modbus_coordinator.MONITORING_REGISTERS",
        (fast_perm,),
    ), patch.object(
        coordinator, "_save_register_capability_state_if_changed", new=AsyncMock()
    ):
        # With _last_slow_data cache, stale slow values are served even when
        # all fast registers are suppressed — that is the correct behaviour.
        assert await coordinator._async_update_data() == {"slow_ok": 20.0}

    slow_perm = _modbus_reg(50, "slow_perm", group=RegisterGroup.ENERGY)
    slow_err = _modbus_reg(60, "slow_err", group=RegisterGroup.ENERGY)

    # Slow-poll batch raises ModbusClientError (caught by coordinator) → fast_ok still returned
    async def _batch_slow_err(regs):
        if any(r.group == RegisterGroup.ENERGY for r in regs):
            raise ModbusClientError("slow batch failed")
        return {r.name: 1.0 for r in regs if r.name == "fast_ok"}

    client.read_registers_batch = AsyncMock(side_effect=_batch_slow_err)
    client.unavailable_registers = set()
    coordinator._slow_tick = 5
    coordinator._device_info_tick = 0
    with patch(
        "custom_components.kostal_kore.modbus_coordinator.MONITORING_REGISTERS",
        (fast_ok, slow_perm, slow_err),
    ), patch.object(
        coordinator, "_save_register_capability_state_if_changed", new=AsyncMock()
    ):
        # _last_slow_data carries the last successful slow-poll values; they are
        # merged into every tick so entities stay available between slow polls.
        assert await coordinator._async_update_data() == {"fast_ok": 1.0, "slow_ok": 20.0}

    # _read_device_info calls individual read_register, not batch
    info_values = {"serial_number": "SN", "product_name": "PLENTICORE"}

    async def _info_read(reg: ModbusRegister):
        if reg.name == "sw_version":
            raise ModbusClientError("skip")
        return info_values.get(reg.name, reg.name)

    client.read_register.side_effect = _info_read
    await coordinator._read_device_info()
    assert coordinator.device_info_data["serial_number"] == "SN"
    assert "sw_version" not in coordinator.device_info_data

    ro_reg = _modbus_reg(100, "ro_reg", group=RegisterGroup.POWER, access=Access.RO)
    rw_reg = _modbus_reg(101, "rw_reg", group=RegisterGroup.CONTROL, access=Access.RW)
    with pytest.raises(ValueError):
        await coordinator.async_write_register(ro_reg, 1)
    with pytest.raises(ValueError):
        await coordinator.async_write_register(rw_reg, math.nan)
    with pytest.raises(ValueError):
        await coordinator.async_write_register(rw_reg, math.inf)

    await coordinator.async_write_register(rw_reg, 42)
    client.write_register.assert_awaited_with(rw_reg, 42)
    # Every write path records the commanded value so write-only registers
    # (e.g. the active-power setpoint 533) can report the active setpoint.
    assert coordinator.last_commanded("rw_reg") == 42.0

    client.write_register = AsyncMock(side_effect=ModbusClientError("write failed"))
    with pytest.raises(ModbusClientError):
        await coordinator.async_write_register(rw_reg, 5)
    # A failed write must not overwrite the cached value.
    assert coordinator.last_commanded("rw_reg") == 42.0

    await coordinator.async_write_by_name("foo", 1)
    client.write_by_name.assert_awaited_once_with("foo", 1)
    assert coordinator.last_commanded("foo") == 1.0

    # write-by-address resolves the register name (proxy path) so it caches too
    await coordinator.async_write_by_address(REG_ACTIVE_POWER_SETPOINT.address, 80)
    client.write_by_address.assert_awaited_once_with(
        REG_ACTIVE_POWER_SETPOINT.address, 80
    )
    assert coordinator.last_commanded("active_power_setpoint") == 80.0
    # an unknown address writes fine but caches nothing
    await coordinator.async_write_by_address(64999, 7)
    assert coordinator.last_commanded("addr_64999") is None

    # a fractional command to an integer (UINT16) register is cached as the
    # truncated value the client actually encodes (int(80.5) == 80), never the
    # un-applied 80.5.
    client.write_register = AsyncMock()
    await coordinator.async_write_register(REG_ACTIVE_POWER_SETPOINT, 80.5)
    assert coordinator.last_commanded("active_power_setpoint") == 80.0


@pytest.mark.asyncio
async def test_last_commanded_invalidated_when_connection_generation_changes(hass) -> None:
    """A cached command is trusted only while the connection generation is
    unchanged. A reconnect performed internally inside the client's read/write
    retry loop advances the generation without the coordinator's reconnect
    branch ever running, so a stale command is no longer returned — while a
    command re-issued on the new connection survives."""
    client = _modbus_client()
    client.connection_generation = 3
    coordinator = ModbusDataUpdateCoordinator(hass, client)

    coordinator._record_commanded("active_power_setpoint", 80)
    assert coordinator.last_commanded("active_power_setpoint") == 80.0

    # Internal reconnect bumps the generation → the old command is not trusted.
    client.connection_generation = 4
    assert coordinator.last_commanded("active_power_setpoint") is None

    # A command re-issued on the new connection (generation 4) survives.
    coordinator._record_commanded("active_power_setpoint", 90)
    assert coordinator.last_commanded("active_power_setpoint") == 90.0


@pytest.mark.asyncio
async def test_modbus_reset_button_and_create_buttons() -> None:
    """Reset button should clear suppression state and request a refresh."""
    coordinator = MagicMock()
    coordinator.client = MagicMock()
    coordinator.client.unavailable_registers = {1, 2, 3}
    coordinator.client.reset_unavailable = MagicMock()
    coordinator.async_request_refresh = AsyncMock()

    reset = ModbusResetButton(coordinator, "entry", _device_info())
    await reset.async_press()
    coordinator.client.reset_unavailable.assert_called_once()
    coordinator.async_request_refresh.assert_awaited_once()

    buttons = create_modbus_buttons(coordinator, "entry", _device_info())
    assert [type(button) for button in buttons] == [
        ModbusResetButton,
        ModbusDiagnosticsButton,
        BatteryTestButton,
    ]


def test_modbus_diagnostics_button_format_variants() -> None:
    """Formatting helper should translate enums and render numbers sanely."""
    assert ModbusDiagnosticsButton._format("inverter_state", 6) == "FeedIn"
    assert ModbusDiagnosticsButton._format("battery_type", 0x0004) == "BYD"
    assert ModbusDiagnosticsButton._format("battery_mgmt_mode", 0x02) == "External via MODBUS"
    assert ModbusDiagnosticsButton._format("float_small", 12.3456) == "12.35"
    assert ModbusDiagnosticsButton._format("float_large", 123456.0) == "123,456"
    assert ModbusDiagnosticsButton._format("other", "abc") == "abc"


@pytest.mark.asyncio
async def test_modbus_diagnostics_button_report_paths_and_notification_failure() -> None:
    """Diagnostics button should report ok/suppressed/error paths and tolerate notification errors."""
    ok_reg = _modbus_reg(1, "ok_power", group=RegisterGroup.POWER, unit="W")
    suppressed_reg = _modbus_reg(2, "suppressed", group=RegisterGroup.POWERMETER)
    na_reg = _modbus_reg(3, "not_available", group=RegisterGroup.ENERGY)
    err_reg = _modbus_reg(4, "error_reg", group=RegisterGroup.DEVICE_INFO)
    skipped_rw = _modbus_reg(5, "rw_skip", group=RegisterGroup.CONTROL, access=Access.RW)

    coordinator = MagicMock()
    coordinator.client = MagicMock()
    coordinator.client.host = "1.2.3.4"
    coordinator.client.port = 1502
    coordinator.client.unavailable_registers = {suppressed_reg.address}

    async def _read(reg: ModbusRegister):
        if reg.name == "suppressed":
            raise ModbusPermanentError("suppressed")
        if reg.name == "not_available":
            raise ModbusPermanentError("na")
        if reg.name == "error_reg":
            raise ModbusClientError("broken")
        return 123.4

    coordinator.client.read_register = AsyncMock(side_effect=_read)

    button = ModbusDiagnosticsButton(coordinator, "entry1", _device_info())
    button.hass = SimpleNamespace(
        services=SimpleNamespace(async_call=AsyncMock(side_effect=RuntimeError("notify failed")))
    )
    button.async_write_ha_state = MagicMock()

    with patch("custom_components.kostal_kore.modbus_button.ALL_REGISTERS", (ok_reg, suppressed_reg, na_reg, err_reg, skipped_rw)):
        await button.async_press()

    attrs = button.extra_state_attributes
    assert attrs["registers_ok"] == 1
    assert attrs["registers_skipped"] == 2
    assert attrs["registers_suppressed"] == 1
    assert attrs["registers_errors"] == 1
    report = json.loads(attrs["report_json"])
    assert report["summary"] == {"ok": 1, "skipped": 2, "suppressed": 1, "errors": 1}
    assert report["registers"]["suppressed"]["status"] == "suppressed"
    assert report["registers"]["not_available"]["status"] == "not_available"
    assert report["registers"]["error_reg"]["status"] == "error"
    button.async_write_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_modbus_diagnostics_button_success_and_suppressed_only_summary() -> None:
    """Diagnostics report should distinguish perfect success from suppressed-only success."""
    ok_reg = _modbus_reg(1, "ok_power", group=RegisterGroup.POWER, unit="W")
    suppressed_reg = _modbus_reg(2, "suppressed", group=RegisterGroup.POWERMETER)
    coordinator = MagicMock()
    coordinator.client = MagicMock()
    coordinator.client.host = "1.2.3.4"
    coordinator.client.port = 1502
    coordinator.client.unavailable_registers = {suppressed_reg.address}
    hass = SimpleNamespace(services=SimpleNamespace(async_call=AsyncMock()))

    button = ModbusDiagnosticsButton(coordinator, "entry2", _device_info())
    button.hass = hass
    button.async_write_ha_state = MagicMock()

    coordinator.client.read_register = AsyncMock(side_effect=[111.0])
    with patch("custom_components.kostal_kore.modbus_button.ALL_REGISTERS", (ok_reg,)):
        await button.async_press()
    first_message = hass.services.async_call.await_args.args[2]["message"]
    assert "Alle Tests bestanden" in first_message

    async def _read(reg: ModbusRegister):
        if reg.name == "suppressed":
            raise ModbusPermanentError("suppressed")
        return 111.0

    coordinator.client.read_register = AsyncMock(side_effect=_read)
    with patch("custom_components.kostal_kore.modbus_button.ALL_REGISTERS", (ok_reg, suppressed_reg)):
        await button.async_press()
    second_message = hass.services.async_call.await_args.args[2]["message"]
    assert "Suppression-Cache" in second_message


@pytest.mark.asyncio
async def test_battery_test_button_abort_block_success_and_error_paths(hass) -> None:
    """Battery test button should cover abort, blocker, success and error branches."""
    coordinator = MagicMock()
    button = BatteryTestButton(coordinator, "entry1", _device_info())
    button.hass = hass
    button.async_write_ha_state = MagicMock()

    running_suite = SimpleNamespace(running=True, request_abort=MagicMock())
    button._suite = running_suite
    await button.async_press()
    running_suite.request_abort.assert_called_once()
    assert button.extra_state_attributes["status"] == "aborting"

    button._suite = None
    hass.data.setdefault(DOMAIN, {})["entry1"] = {"soc_controller": SimpleNamespace(active=True)}
    await button.async_press()
    assert button.extra_state_attributes["status"] == "blocked: SoC Controller aktiv"

    hass.data[DOMAIN]["entry1"] = {}
    results = [SimpleNamespace(success=True), SimpleNamespace(success=False)]
    suite_instance = SimpleNamespace(
        running=False,
        log_lines=["a", "b"],
        run=AsyncMock(return_value=results),
    )
    with patch(
        "custom_components.kostal_kore.battery_test.BatteryTestSuite",
        return_value=suite_instance,
    ):
        await button.async_press()
    assert button.extra_state_attributes["status"] == "completed"
    assert button.extra_state_attributes["phases_passed"] == 1
    assert button.extra_state_attributes["phases_total"] == 2

    hass.data[DOMAIN]["entry1"] = object()
    suite_nondict = SimpleNamespace(
        running=False,
        log_lines=["x"],
        run=AsyncMock(return_value=[SimpleNamespace(success=True)]),
    )
    with patch(
        "custom_components.kostal_kore.battery_test.BatteryTestSuite",
        return_value=suite_nondict,
    ):
        await button.async_press()
    assert button.extra_state_attributes["status"] == "completed"

    suite_error = SimpleNamespace(
        running=False,
        log_lines=[],
        run=AsyncMock(side_effect=RuntimeError("boom")),
    )
    with patch(
        "custom_components.kostal_kore.battery_test.BatteryTestSuite",
        return_value=suite_error,
    ):
        await button.async_press()
    assert button.extra_state_attributes["status"] == "error"
    assert button.extra_state_attributes["error"] == "boom"
