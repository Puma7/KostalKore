"""The unit-id keyword shim works across pymodbus 3.6-3.9 and 3.10+."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pymodbus
import pytest
from pymodbus.client import AsyncModbusTcpClient

from custom_components.kostal_kore import pymodbus_compat
from custom_components.kostal_kore.pymodbus_compat import (
    detect_unit_id_keyword,
    read_holding_registers,
    write_register,
    write_registers,
)


# Signatures copied from the pymodbus client mixin of each release line.
def _read_3_6(self: Any, address: int, count: int = 1, slave: int = 0, **kwargs: Any) -> Any:
    """pymodbus 3.6.x: positional ``slave`` plus ``**kwargs``."""


def _read_3_9(
    self: Any, address: int, *, count: int = 1, slave: int = 1,
    no_response_expected: bool = False,
) -> Any:
    """pymodbus 3.7-3.9: keyword-only ``slave``."""


def _read_3_10(
    self: Any, address: int, *, count: int = 1, device_id: int = 1,
    no_response_expected: bool = False,
) -> Any:
    """pymodbus 3.10+: keyword-only ``device_id``."""


@pytest.mark.parametrize(
    ("method", "expected"),
    [(_read_3_6, "slave"), (_read_3_9, "slave"), (_read_3_10, "device_id")],
    ids=["3.6", "3.9", "3.10"],
)
def test_detects_keyword_per_release_line(method: Any, expected: str) -> None:
    # 3.6's **kwargs would silently swallow device_id= — it must pick slave.
    assert detect_unit_id_keyword(method) == expected


def test_installed_pymodbus_keyword_matches_its_version() -> None:
    major, minor = (int(part) for part in pymodbus.__version__.split(".")[:2])
    expected = "device_id" if (major, minor) >= (3, 10) else "slave"
    assert pymodbus_compat.UNIT_ID_KEYWORD == expected


async def test_helpers_bind_to_installed_client_signature() -> None:
    """autospec rejects any keyword the installed pymodbus does not accept."""
    client = AsyncModbusTcpClient(host="127.0.0.1", port=502)
    kw = pymodbus_compat.UNIT_ID_KEYWORD
    with (
        patch.object(AsyncModbusTcpClient, "read_holding_registers", autospec=True) as read,
        patch.object(AsyncModbusTcpClient, "write_register", autospec=True) as write_one,
        patch.object(AsyncModbusTcpClient, "write_registers", autospec=True) as write_many,
    ):
        read_holding_registers(client, 1034, 2, 71)
        write_register(client, 1038, 7, 71)
        write_registers(client, 1034, (1, 2), 71)

    read.assert_called_once_with(client, address=1034, count=2, **{kw: 71})
    write_one.assert_called_once_with(client, address=1038, value=7, **{kw: 71})
    write_many.assert_called_once_with(client, address=1034, values=[1, 2], **{kw: 71})


def test_helpers_pass_slave_on_old_pymodbus(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pymodbus_compat, "UNIT_ID_KEYWORD", "slave")
    client = MagicMock()

    assert read_holding_registers(client, 5, 1, 3) is client.read_holding_registers.return_value
    assert write_register(client, 1038, 0, 3) is client.write_register.return_value
    assert write_registers(client, 1034, [0, 1], 3) is client.write_registers.return_value

    client.read_holding_registers.assert_called_once_with(address=5, count=1, slave=3)
    client.write_register.assert_called_once_with(address=1038, value=0, slave=3)
    client.write_registers.assert_called_once_with(address=1034, values=[0, 1], slave=3)
