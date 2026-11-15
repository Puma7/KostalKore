"""Unit-id keyword shim across the supported pymodbus range.

The manifest allows ``pymodbus>=3.6,<4.0``. pymodbus 3.10 renamed the
unit-id keyword of the client request methods from ``slave=`` to
``device_id=`` and dropped the old name; 3.6-3.9 accept only ``slave=``.
Home Assistant core's own Modbus integration pins 3.6.9-3.9.2 on HA
2024.12-2025.8 and 3.11+ from 2025.9, so either spelling can be installed
when KORE loads. Raising the floor to 3.10 would fight that pin.

The keyword is read once from the installed client's signature, and every
Modbus request in the integration goes through the helpers below.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Final, cast

from pymodbus.client import AsyncModbusTcpClient

# pymodbus >= 3.10
_DEVICE_ID: Final = "device_id"
# pymodbus 3.6 - 3.9
_SLAVE: Final = "slave"


def detect_unit_id_keyword(method: Callable[..., Any]) -> str:
    """Return the unit-id keyword that ``method`` accepts."""
    parameters = inspect.signature(method).parameters
    return _DEVICE_ID if _DEVICE_ID in parameters else _SLAVE


UNIT_ID_KEYWORD: Final[str] = detect_unit_id_keyword(
    AsyncModbusTcpClient.read_holding_registers
)


def read_holding_registers(
    client: AsyncModbusTcpClient, address: int, count: int, unit_id: int
) -> Awaitable[Any]:
    """Start a holding-register read (function code 0x03)."""
    return cast(
        Awaitable[Any],
        cast(Any, client).read_holding_registers(
            address=address, count=count, **{UNIT_ID_KEYWORD: unit_id}
        ),
    )


def write_register(
    client: AsyncModbusTcpClient, address: int, value: int, unit_id: int
) -> Awaitable[Any]:
    """Start a single-register write (function code 0x06)."""
    return cast(
        Awaitable[Any],
        cast(Any, client).write_register(
            address=address, value=value, **{UNIT_ID_KEYWORD: unit_id}
        ),
    )


def write_registers(
    client: AsyncModbusTcpClient, address: int, values: Sequence[int], unit_id: int
) -> Awaitable[Any]:
    """Start a multi-register write (function code 0x10)."""
    return cast(
        Awaitable[Any],
        cast(Any, client).write_registers(
            address=address, values=list(values), **{UNIT_ID_KEYWORD: unit_id}
        ),
    )
