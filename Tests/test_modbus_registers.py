"""Tests for Kostal Plenticore Modbus register definitions."""

from __future__ import annotations

from kostal_plenticore.modbus_registers import (
    ALL_REGISTERS,
    Access,
    BATTERY_MGMT_MODES,
    BATTERY_TYPES,
    DataType,
    INVERTER_STATES,
    MONITORING_REGISTERS,
    ModbusRegister,
    REG_ACTIVE_POWER_SETPOINT,
    REG_BAT_CHARGE_DC_ABS_POWER,
    REG_BAT_MAX_CHARGE_LIMIT,
    REG_BAT_MIN_SOC,
    REG_BATTERY_SOC,
    REG_BYTE_ORDER,
    REG_G3_FALLBACK_TIME,
    REG_G3_MAX_CHARGE,
    REG_INVERTER_STATE,
    REG_IO_OUTPUT_1,
    REG_SERIAL_NUMBER,
    REG_TOTAL_AC_POWER,
    REG_TOTAL_DC_POWER,
    REGISTER_BY_ADDRESS,
    REGISTER_BY_NAME,
    RegisterGroup,
    WRITABLE_REGISTERS,
)


class TestRegisterDefinitions:
    """Test register constants and lookup structures."""

    def test_all_registers_not_empty(self) -> None:
        assert len(ALL_REGISTERS) > 80

    def test_register_by_name_lookup(self) -> None:
        assert REGISTER_BY_NAME["total_dc_power"] is REG_TOTAL_DC_POWER
        assert REGISTER_BY_NAME["battery_soc"] is REG_BATTERY_SOC
        assert REGISTER_BY_NAME["inverter_state"] is REG_INVERTER_STATE

    def test_register_by_address_lookup(self) -> None:
        assert REGISTER_BY_ADDRESS[100] is REG_TOTAL_DC_POWER
        assert REGISTER_BY_ADDRESS[514] is REG_BATTERY_SOC
        assert REGISTER_BY_ADDRESS[56] is REG_INVERTER_STATE

    def test_no_duplicate_names(self) -> None:
        names = [r.name for r in ALL_REGISTERS]
        assert len(names) == len(set(names)), "Duplicate register names found"

    def test_no_duplicate_addresses(self) -> None:
        addresses = [r.address for r in ALL_REGISTERS]
        assert len(addresses) == len(set(addresses)), "Duplicate addresses found"

    def test_monitoring_registers_are_read_only(self) -> None:
        for reg in MONITORING_REGISTERS:
            assert reg.access == Access.RO, f"{reg.name} should be read-only"

    def test_writable_registers_are_rw(self) -> None:
        for reg in WRITABLE_REGISTERS:
            assert reg.access == Access.RW, f"{reg.name} should be R/W"

    def test_writable_registers_include_key_controls(self) -> None:
        writable_names = {r.name for r in WRITABLE_REGISTERS}
        assert "active_power_setpoint" in writable_names
        assert "reactive_power_setpoint" in writable_names
        assert "bat_charge_dc_abs_power" in writable_names
        assert "bat_min_soc" in writable_names
        assert "bat_max_charge_limit" in writable_names
        assert "g3_max_charge" in writable_names
        assert "io_output_1" in writable_names

    def test_register_data_types(self) -> None:
        assert REG_TOTAL_DC_POWER.data_type == DataType.FLOAT32
        assert REG_BATTERY_SOC.data_type == DataType.UINT16
        assert REG_INVERTER_STATE.data_type == DataType.UINT32
        assert REG_SERIAL_NUMBER.data_type == DataType.STRING
        assert REG_ACTIVE_POWER_SETPOINT.data_type == DataType.UINT16
        assert REG_BYTE_ORDER.data_type == DataType.UINT16

    def test_register_groups(self) -> None:
        assert REG_TOTAL_DC_POWER.group == RegisterGroup.POWER
        assert REG_BATTERY_SOC.group == RegisterGroup.BATTERY
        assert REG_ACTIVE_POWER_SETPOINT.group == RegisterGroup.CONTROL
        assert REG_BAT_CHARGE_DC_ABS_POWER.group == RegisterGroup.BATTERY_MGMT
        assert REG_G3_MAX_CHARGE.group == RegisterGroup.BATTERY_LIMIT_G3
        assert REG_IO_OUTPUT_1.group == RegisterGroup.IO_BOARD

    def test_register_frozen(self) -> None:
        import pytest

        with pytest.raises(AttributeError):
            REG_TOTAL_DC_POWER.address = 999  # type: ignore[misc]

    def test_register_units(self) -> None:
        assert REG_TOTAL_DC_POWER.unit == "W"
        assert REG_BATTERY_SOC.unit == "%"
        assert REG_TOTAL_AC_POWER.unit == "W"
        assert REG_BAT_MIN_SOC.unit == "%"
        assert REG_G3_FALLBACK_TIME.unit == "s"

    def test_register_counts(self) -> None:
        assert REG_BATTERY_SOC.count == 1
        assert REG_TOTAL_DC_POWER.count == 2
        assert REG_SERIAL_NUMBER.count == 8
        assert REG_G3_FALLBACK_TIME.count == 2

    def test_default_connection_params(self) -> None:
        from kostal_plenticore.modbus_registers import (
            DEFAULT_MODBUS_PORT,
            DEFAULT_UNIT_ID,
        )

        assert DEFAULT_MODBUS_PORT == 1502
        assert DEFAULT_UNIT_ID == 71


class TestLookupMaps:
    """Test inverter state, battery type, and management mode maps."""

    def test_inverter_states_complete(self) -> None:
        assert INVERTER_STATES[0] == "Off"
        assert INVERTER_STATES[6] == "FeedIn"
        assert INVERTER_STATES[10] == "Standby"
        assert INVERTER_STATES[14] == "Overheating"

    def test_battery_types(self) -> None:
        assert BATTERY_TYPES[0x0000] == "No battery"
        assert BATTERY_TYPES[0x0004] == "BYD"
        assert BATTERY_TYPES[0x0040] == "LG"

    def test_battery_mgmt_modes(self) -> None:
        assert BATTERY_MGMT_MODES[0x00] == "No external battery management"
        assert BATTERY_MGMT_MODES[0x02] == "External via MODBUS"


class TestModbusRegisterDataclass:
    """Test the ModbusRegister dataclass itself."""

    def test_create_register(self) -> None:
        reg = ModbusRegister(
            address=9999,
            name="test_reg",
            description="Test register",
            data_type=DataType.UINT16,
            count=1,
            access=Access.RO,
            group=RegisterGroup.POWER,
            unit="W",
        )
        assert reg.address == 9999
        assert reg.name == "test_reg"
        assert reg.unit == "W"
        assert reg.scale is None

    def test_register_equality(self) -> None:
        r1 = ModbusRegister(1, "a", "d", DataType.UINT16, 1, Access.RO, RegisterGroup.POWER)
        r2 = ModbusRegister(1, "a", "d", DataType.UINT16, 1, Access.RO, RegisterGroup.POWER)
        assert r1 == r2

    def test_register_hash(self) -> None:
        r1 = ModbusRegister(1, "a", "d", DataType.UINT16, 1, Access.RO, RegisterGroup.POWER)
        r2 = ModbusRegister(1, "a", "d", DataType.UINT16, 1, Access.RO, RegisterGroup.POWER)
        assert hash(r1) == hash(r2)
        assert len({r1, r2}) == 1
