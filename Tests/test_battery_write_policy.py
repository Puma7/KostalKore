"""External battery-write policy: shared register set, full word spans, REG 1038 owner."""

from __future__ import annotations

import asyncio
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.kostal_kore.battery_reg_1038_owner import (
    OWNER_CHARGE_BLOCK,
    get_reg_1038_owner_manager,
)
from custom_components.kostal_kore.const import DOMAIN
from custom_components.kostal_kore.modbus_proxy import (
    BATTERY_CONTROL_REGISTERS,
    FC_WRITE_MULTIPLE,
    FC_WRITE_SINGLE,
    ModbusTcpProxyServer,
)
from custom_components.kostal_kore.modbus_registers import (
    BATTERY_CONTROL_ADDRESSES,
    BATTERY_CONTROL_NAMES,
    REG_ACTIVE_POWER_SETPOINT,
    REG_BAT_CHARGE_AC_REL,
    REG_BAT_MAX_CHARGE_LIMIT,
    REG_G3_FALLBACK_TIME,
    REG_G3_MAX_CHARGE_FALLBACK,
    WRITABLE_REGISTERS,
    RegisterGroup,
)
from custom_components.kostal_kore.mqtt_bridge import KostalMqttBridge

ENTRY_ID = "policy_entry"


def test_battery_control_set_covers_every_writable_battery_register() -> None:
    expected = {
        r.name for r in WRITABLE_REGISTERS
        if r.group in (RegisterGroup.BATTERY_MGMT, RegisterGroup.BATTERY_LIMIT_G3)
    }
    assert BATTERY_CONTROL_NAMES == expected
    assert {"bat_charge_ac_abs", "bat_charge_dc_rel", "bat_charge_dc_rel_power",
            "g3_max_charge_fallback", "g3_max_discharge_fallback",
            "g3_fallback_time"} <= BATTERY_CONTROL_NAMES
    # Every word of every register, including the low word of FLOAT32/UINT32.
    assert BATTERY_CONTROL_ADDRESSES == set(range(1026, 1046)) | set(range(1280, 1290))
    assert BATTERY_CONTROL_REGISTERS == BATTERY_CONTROL_ADDRESSES
    assert REG_ACTIVE_POWER_SETPOINT.address not in BATTERY_CONTROL_ADDRESSES


def _proxy(*, installer_access: bool, soc_active: bool = False,
           hass: HomeAssistant | None = None) -> ModbusTcpProxyServer:
    coordinator = MagicMock()
    coordinator.data = {}
    coordinator.async_write_by_address = AsyncMock()
    coordinator.async_write_register = AsyncMock()
    coordinator.client = None
    return ModbusTcpProxyServer(
        coordinator,
        installer_access=installer_access,
        soc_controller=SimpleNamespace(active=soc_active, target_soc=50.0),
        hass=hass,
        entry_id=ENTRY_ID if hass is not None else "",
    )


def _err(fc: int, code: int) -> bytes:
    return struct.pack(">BB", fc | 0x80, code)


@pytest.mark.asyncio
@pytest.mark.parametrize("address", [
    REG_BAT_CHARGE_AC_REL.address,          # previously unprotected via the proxy
    REG_BAT_MAX_CHARGE_LIMIT.address + 1,   # second word of a FLOAT32
    REG_G3_FALLBACK_TIME.address + 1,
])
async def test_proxy_fc06_installer_gate_covers_all_battery_words(address: int) -> None:
    proxy = _proxy(installer_access=False)
    response = await proxy._handle_write_single(struct.pack(">BHH", FC_WRITE_SINGLE, address, 1))
    assert response == _err(FC_WRITE_SINGLE, 0x03)


@pytest.mark.asyncio
async def test_proxy_fc16_soc_arbitration_covers_previously_unlisted_register() -> None:
    proxy = _proxy(installer_access=True, soc_active=True)
    pdu = struct.pack(">BHHBHH", FC_WRITE_MULTIPLE, REG_BAT_CHARGE_AC_REL.address, 2, 4, 0, 0)
    assert await proxy._handle_write_multiple(pdu) == _err(FC_WRITE_MULTIPLE, 0x06)
    proxy._coordinator.async_write_register.assert_not_called()


@pytest.mark.asyncio
async def test_proxy_reg_1038_owner_covers_second_word(hass: HomeAssistant) -> None:
    hass.data[DOMAIN] = {ENTRY_ID: {}}
    get_reg_1038_owner_manager(hass, ENTRY_ID).try_acquire(OWNER_CHARGE_BLOCK)
    proxy = _proxy(installer_access=True, hass=hass)
    single = struct.pack(">BHH", FC_WRITE_SINGLE, REG_BAT_MAX_CHARGE_LIMIT.address + 1, 0)
    assert await proxy._handle_write_single(single) == _err(FC_WRITE_SINGLE, 0x06)
    multiple = struct.pack(
        ">BHHBH", FC_WRITE_MULTIPLE, REG_BAT_MAX_CHARGE_LIMIT.address + 1, 1, 2, 0,
    )
    assert await proxy._handle_write_multiple(multiple) == _err(FC_WRITE_MULTIPLE, 0x06)


def _bridge(hass: HomeAssistant, *, installer_access: bool, entry_id: str = ENTRY_ID):
    coordinator = MagicMock()
    coordinator.async_write_register = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    coordinator._write_audit = None
    bridge = KostalMqttBridge(
        hass, coordinator, "INV", installer_access=installer_access, entry_id=entry_id,
    )
    return bridge, coordinator


@pytest.mark.asyncio
async def test_mqtt_installer_gate_covers_g3_fallback(hass: HomeAssistant) -> None:
    bridge, coordinator = _bridge(hass, installer_access=False)
    await bridge._execute_write(REG_G3_MAX_CHARGE_FALLBACK, "1000", source="test")
    coordinator.async_write_register.assert_not_called()


@pytest.mark.asyncio
async def test_mqtt_rejects_reg_1038_while_owned(hass: HomeAssistant) -> None:
    hass.data[DOMAIN] = {ENTRY_ID: {}}
    bridge, coordinator = _bridge(hass, installer_access=True)
    manager = get_reg_1038_owner_manager(hass, ENTRY_ID)
    manager.try_acquire(OWNER_CHARGE_BLOCK)
    await bridge._execute_write(REG_BAT_MAX_CHARGE_LIMIT, "2000", source="test")
    coordinator.async_write_register.assert_not_called()

    manager.release(OWNER_CHARGE_BLOCK)
    await bridge._execute_write(REG_BAT_MAX_CHARGE_LIMIT, "2000", source="test")
    coordinator.async_write_register.assert_awaited_once()


@pytest.mark.asyncio
async def test_mqtt_reg_1038_owner_checked_after_waiting_for_write_lock(
    hass: HomeAssistant,
) -> None:
    """A feature acquiring REG 1038 while the command waits for the lock wins."""
    hass.data[DOMAIN] = {ENTRY_ID: {}}
    bridge, coordinator = _bridge(hass, installer_access=True)
    await bridge._write_lock.acquire()
    task = hass.async_create_task(
        bridge._execute_write(REG_BAT_MAX_CHARGE_LIMIT, "2000", source="test")
    )
    for _ in range(5):
        await asyncio.sleep(0)
    get_reg_1038_owner_manager(hass, ENTRY_ID).try_acquire(OWNER_CHARGE_BLOCK)
    bridge._write_lock.release()
    await task
    coordinator.async_write_register.assert_not_called()


@pytest.mark.asyncio
async def test_mqtt_without_entry_id_skips_owner_lookup(hass: HomeAssistant) -> None:
    bridge, coordinator = _bridge(hass, installer_access=True, entry_id="")
    await bridge._execute_write(REG_BAT_MAX_CHARGE_LIMIT, "2000", source="test")
    coordinator.async_write_register.assert_awaited_once()
