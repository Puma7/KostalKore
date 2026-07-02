"""Behaviour tests for the Modbus exception hierarchy and error parsing.

Historical note: this file previously held "platinum feature" tests that only
asserted classes/methods existed (``assert hasattr(...)``) or that modules
imported (``assert True``). Those checks verified no behaviour and targeted
modules that are omitted from the coverage gate, so they were removed. What
remains are the two tests that actually exercise behaviour in ``helper.py``.
"""

from __future__ import annotations


class TestModbusExceptionHandling:
    """Behaviour tests for the Modbus exception classes in ``helper.py``."""

    def test_modbus_exception_hierarchy(self) -> None:
        """Modbus error subclasses inherit from ModbusException and carry codes."""
        from kostal_plenticore.helper import (
            ModbusException,
            ModbusIllegalDataAddressError,
            ModbusIllegalFunctionError,
        )

        exc = ModbusIllegalFunctionError(0x01)
        assert isinstance(exc, ModbusException)
        assert exc.exception_code == 0x01

        exc = ModbusIllegalDataAddressError()
        assert isinstance(exc, ModbusException)
        assert exc.exception_code == 0x02

    def test_error_parsing_functionality(self) -> None:
        """parse_modbus_exception maps an ApiException to a typed Modbus error."""
        from pykoplenti import ApiException

        from kostal_plenticore.helper import parse_modbus_exception

        modbus_exc = parse_modbus_exception(ApiException("illegal function error"))

        assert modbus_exc is not None
        assert hasattr(modbus_exc, "exception_code")
