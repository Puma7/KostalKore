"""Tests for ModbusTcpProxyServer security behavior."""

from __future__ import annotations

import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kostal_plenticore.modbus_proxy import (
    DEFAULT_PROXY_BIND,
    FC_WRITE_MULTIPLE,
    FC_WRITE_SINGLE,
    ModbusTcpProxyServer,
)


def _proxy(*, installer_access: bool, soc_active: bool = False) -> ModbusTcpProxyServer:
    coordinator = MagicMock()
    coordinator.data = {}
    coordinator.async_write_by_address = AsyncMock()
    coordinator.async_write_register = AsyncMock()
    coordinator.client = None
    soc_controller = SimpleNamespace(active=soc_active, target_soc=50.0)
    return ModbusTcpProxyServer(
        coordinator,
        installer_access=installer_access,
        soc_controller=soc_controller,
    )


def test_default_bind_host_is_loopback() -> None:
    proxy = _proxy(installer_access=False)
    assert proxy.bind_host == DEFAULT_PROXY_BIND


@pytest.mark.asyncio
async def test_write_single_rejected_without_installer_access() -> None:
    proxy = _proxy(installer_access=False)
    pdu = struct.pack(">BHH", FC_WRITE_SINGLE, 1034, 1)
    response = await proxy._handle_write_single(pdu)
    assert response == struct.pack(">BB", FC_WRITE_SINGLE | 0x80, 0x03)


@pytest.mark.asyncio
async def test_write_multiple_rejected_on_overlap_without_installer_access() -> None:
    proxy = _proxy(installer_access=False)
    pdu = struct.pack(">BHHBHHH", FC_WRITE_MULTIPLE, 1033, 3, 6, 0, 0, 0)
    response = await proxy._handle_write_multiple(pdu)
    assert response == struct.pack(">BB", FC_WRITE_MULTIPLE | 0x80, 0x03)


@pytest.mark.asyncio
async def test_write_multiple_rejected_when_soc_controller_active() -> None:
    proxy = _proxy(installer_access=True, soc_active=True)
    pdu = struct.pack(">BHHBHH", FC_WRITE_MULTIPLE, 1034, 2, 4, 0, 0)
    response = await proxy._handle_write_multiple(pdu)
    assert response == struct.pack(">BB", FC_WRITE_MULTIPLE | 0x80, 0x06)
