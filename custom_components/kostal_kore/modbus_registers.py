"""Kostal Plenticore Modbus register definitions.

Complete register map derived from the official Kostal MODBUS-TCP/SunSpec
interface documentation (BA_KOSTAL_Interface_MODBUS-TCP_SunSpec_with_Control).

Registers are grouped by function: device info, power monitoring, battery
management, grid control, and G3 battery limitation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class DataType(StrEnum):
    """Modbus register data types."""

    UINT16 = "uint16"
    UINT32 = "uint32"
    SINT16 = "sint16"
    SINT32 = "sint32"
    FLOAT32 = "float32"
    STRING = "string"
    BOOL = "bool"
    UINT8 = "uint8"


class Access(StrEnum):
    """Register access modes."""

    RO = "ro"
    RW = "rw"


class RegisterGroup(StrEnum):
    """Logical grouping of registers."""

    DEVICE_INFO = "device_info"
    POWER = "power"
    ENERGY = "energy"
    PHASE = "phase"
    BATTERY = "battery"
    POWERMETER = "powermeter"
    CONTROL = "control"
    BATTERY_MGMT = "battery_mgmt"
    BATTERY_LIMIT_G3 = "battery_limit_g3"
    IO_BOARD = "io_board"
    NETWORK = "network"


@dataclass(frozen=True)
class ModbusRegister:
    """Definition of a single Modbus register or register block."""

    address: int
    name: str
    description: str
    data_type: DataType
    count: int
    access: Access
    group: RegisterGroup
    unit: str | None = None
    scale: float | None = None


# ---------------------------------------------------------------------------
# Default connection parameters
# ---------------------------------------------------------------------------
DEFAULT_MODBUS_PORT: Final[int] = 1502
DEFAULT_UNIT_ID: Final[int] = 71

# ---------------------------------------------------------------------------
# Device info registers (read-only)
# ---------------------------------------------------------------------------
REG_MODBUS_ENABLE = ModbusRegister(2, "modbus_enable", "MODBUS Enable", DataType.BOOL, 1, Access.RW, RegisterGroup.DEVICE_INFO)
REG_UNIT_ID = ModbusRegister(4, "unit_id", "MODBUS Unit-ID", DataType.UINT16, 1, Access.RW, RegisterGroup.DEVICE_INFO)
REG_BYTE_ORDER = ModbusRegister(5, "byte_order", "MODBUS Byte Order", DataType.UINT16, 1, Access.RW, RegisterGroup.DEVICE_INFO)
REG_ARTICLE_NUMBER = ModbusRegister(6, "article_number", "Inverter article number", DataType.STRING, 8, Access.RO, RegisterGroup.DEVICE_INFO)
REG_SERIAL_NUMBER = ModbusRegister(14, "serial_number", "Inverter serial number", DataType.STRING, 8, Access.RO, RegisterGroup.DEVICE_INFO)
REG_NUM_BIDIRECTIONAL = ModbusRegister(30, "num_bidirectional", "Number of bidirectional converter", DataType.UINT16, 1, Access.RO, RegisterGroup.DEVICE_INFO)
REG_NUM_AC_PHASES = ModbusRegister(32, "num_ac_phases", "Number of AC phases", DataType.UINT16, 1, Access.RO, RegisterGroup.DEVICE_INFO)
REG_NUM_PV_STRINGS = ModbusRegister(34, "num_pv_strings", "Number of PV strings", DataType.UINT16, 1, Access.RO, RegisterGroup.DEVICE_INFO)
REG_HW_VERSION = ModbusRegister(36, "hw_version", "Hardware-Version", DataType.UINT16, 2, Access.RO, RegisterGroup.DEVICE_INFO)
REG_SW_VERSION_MC = ModbusRegister(38, "sw_version_mc", "Software-Version Maincontroller", DataType.STRING, 8, Access.RO, RegisterGroup.DEVICE_INFO)
REG_SW_VERSION_IOC = ModbusRegister(46, "sw_version_ioc", "Software-Version IO-Controller", DataType.STRING, 8, Access.RO, RegisterGroup.DEVICE_INFO)
REG_POWER_ID = ModbusRegister(54, "power_id", "Power-ID", DataType.UINT32, 2, Access.RO, RegisterGroup.DEVICE_INFO)
REG_INVERTER_STATE = ModbusRegister(56, "inverter_state", "Inverter state", DataType.UINT32, 2, Access.RO, RegisterGroup.DEVICE_INFO)
REG_SW_VERSION = ModbusRegister(58, "sw_version", "Overall software version", DataType.STRING, 13, Access.RO, RegisterGroup.DEVICE_INFO)
REG_PRODUCT_NAME = ModbusRegister(768, "product_name", "Product name", DataType.STRING, 32, Access.RO, RegisterGroup.DEVICE_INFO)
REG_POWER_CLASS = ModbusRegister(800, "power_class", "Power class", DataType.STRING, 32, Access.RO, RegisterGroup.DEVICE_INFO)

# ---------------------------------------------------------------------------
# Power and measurement registers (read-only)
# ---------------------------------------------------------------------------
REG_CONTROLLER_TEMP = ModbusRegister(98, "controller_temp", "Temperature of controller PCB", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWER, "°C")
REG_TOTAL_DC_POWER = ModbusRegister(100, "total_dc_power", "Total DC power", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWER, "W")
REG_EM_STATE = ModbusRegister(104, "em_state", "State of energy manager", DataType.UINT32, 2, Access.RO, RegisterGroup.POWER)
REG_HOME_FROM_BATTERY = ModbusRegister(106, "home_from_battery", "Home own consumption from battery", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWER, "W")
REG_HOME_FROM_GRID = ModbusRegister(108, "home_from_grid", "Home own consumption from grid", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWER, "W")
REG_TOTAL_HOME_BATTERY = ModbusRegister(110, "total_home_battery", "Total home consumption Battery", DataType.FLOAT32, 2, Access.RO, RegisterGroup.ENERGY, "Wh")
REG_TOTAL_HOME_GRID = ModbusRegister(112, "total_home_grid", "Total home consumption Grid", DataType.FLOAT32, 2, Access.RO, RegisterGroup.ENERGY, "Wh")
REG_TOTAL_HOME_PV = ModbusRegister(114, "total_home_pv", "Total home consumption PV", DataType.FLOAT32, 2, Access.RO, RegisterGroup.ENERGY, "Wh")
REG_HOME_FROM_PV = ModbusRegister(116, "home_from_pv", "Home own consumption from PV", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWER, "W")
REG_TOTAL_HOME_CONSUMPTION = ModbusRegister(118, "total_home_consumption", "Total home consumption", DataType.FLOAT32, 2, Access.RO, RegisterGroup.ENERGY, "Wh")
REG_ISOLATION_RESISTANCE = ModbusRegister(120, "isolation_resistance", "Isolation resistance", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWER, "Ohm")
REG_POWER_LIMIT_EVU = ModbusRegister(122, "power_limit_evu", "Power limit from EVU", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWER, "%")
REG_HOME_CONSUMPTION_RATE = ModbusRegister(124, "home_consumption_rate", "Total home consumption rate", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWER, "%")
REG_WORKTIME = ModbusRegister(144, "worktime", "Worktime", DataType.FLOAT32, 2, Access.RO, RegisterGroup.DEVICE_INFO, "s")
REG_COS_PHI = ModbusRegister(150, "cos_phi", "Actual cos phi", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWER)
REG_GRID_FREQUENCY = ModbusRegister(152, "grid_frequency", "Grid frequency", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWER, "Hz")
REG_TOTAL_AC_POWER = ModbusRegister(172, "total_ac_power", "Total AC active power", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWER, "W")
REG_TOTAL_AC_REACTIVE = ModbusRegister(174, "total_ac_reactive", "Total AC reactive power", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWER, "Var")
REG_TOTAL_AC_APPARENT = ModbusRegister(178, "total_ac_apparent", "Total AC apparent power", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWER, "VA")

# ---------------------------------------------------------------------------
# Phase registers (read-only)
# ---------------------------------------------------------------------------
REG_PHASE1_CURRENT = ModbusRegister(154, "phase1_current", "Current Phase 1", DataType.FLOAT32, 2, Access.RO, RegisterGroup.PHASE, "A")
REG_PHASE1_POWER = ModbusRegister(156, "phase1_power", "Active power Phase 1", DataType.FLOAT32, 2, Access.RO, RegisterGroup.PHASE, "W")
REG_PHASE1_VOLTAGE = ModbusRegister(158, "phase1_voltage", "Voltage Phase 1", DataType.FLOAT32, 2, Access.RO, RegisterGroup.PHASE, "V")
REG_PHASE2_CURRENT = ModbusRegister(160, "phase2_current", "Current Phase 2", DataType.FLOAT32, 2, Access.RO, RegisterGroup.PHASE, "A")
REG_PHASE2_POWER = ModbusRegister(162, "phase2_power", "Active power Phase 2", DataType.FLOAT32, 2, Access.RO, RegisterGroup.PHASE, "W")
REG_PHASE2_VOLTAGE = ModbusRegister(164, "phase2_voltage", "Voltage Phase 2", DataType.FLOAT32, 2, Access.RO, RegisterGroup.PHASE, "V")
REG_PHASE3_CURRENT = ModbusRegister(166, "phase3_current", "Current Phase 3", DataType.FLOAT32, 2, Access.RO, RegisterGroup.PHASE, "A")
REG_PHASE3_POWER = ModbusRegister(168, "phase3_power", "Active power Phase 3", DataType.FLOAT32, 2, Access.RO, RegisterGroup.PHASE, "W")
REG_PHASE3_VOLTAGE = ModbusRegister(170, "phase3_voltage", "Voltage Phase 3", DataType.FLOAT32, 2, Access.RO, RegisterGroup.PHASE, "V")

# ---------------------------------------------------------------------------
# DC string registers (read-only)
# ---------------------------------------------------------------------------
REG_DC1_CURRENT = ModbusRegister(258, "dc1_current", "Current DC1", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWER, "A")
REG_DC1_POWER = ModbusRegister(260, "dc1_power", "Power DC1", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWER, "W")
REG_DC1_VOLTAGE = ModbusRegister(266, "dc1_voltage", "Voltage DC1", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWER, "V")
REG_DC2_CURRENT = ModbusRegister(268, "dc2_current", "Current DC2", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWER, "A")
REG_DC2_POWER = ModbusRegister(270, "dc2_power", "Power DC2", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWER, "W")
REG_DC2_VOLTAGE = ModbusRegister(276, "dc2_voltage", "Voltage DC2", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWER, "V")
REG_DC3_CURRENT = ModbusRegister(278, "dc3_current", "Current DC3", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWER, "A")
REG_DC3_POWER = ModbusRegister(280, "dc3_power", "Power DC3", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWER, "W")
REG_DC3_VOLTAGE = ModbusRegister(286, "dc3_voltage", "Voltage DC3", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWER, "V")

# ---------------------------------------------------------------------------
# Energy yield registers (read-only)
# ---------------------------------------------------------------------------
REG_TOTAL_YIELD = ModbusRegister(320, "total_yield", "Total yield", DataType.FLOAT32, 2, Access.RO, RegisterGroup.ENERGY, "Wh")
REG_DAILY_YIELD = ModbusRegister(322, "daily_yield", "Daily yield", DataType.FLOAT32, 2, Access.RO, RegisterGroup.ENERGY, "Wh")
REG_YEARLY_YIELD = ModbusRegister(324, "yearly_yield", "Yearly yield", DataType.FLOAT32, 2, Access.RO, RegisterGroup.ENERGY, "Wh")
REG_MONTHLY_YIELD = ModbusRegister(326, "monthly_yield", "Monthly yield", DataType.FLOAT32, 2, Access.RO, RegisterGroup.ENERGY, "Wh")

# ---------------------------------------------------------------------------
# Battery registers (read-only)
# ---------------------------------------------------------------------------
REG_BATTERY_GROSS_CAPACITY = ModbusRegister(512, "battery_gross_capacity", "Battery gross capacity", DataType.UINT32, 2, Access.RO, RegisterGroup.BATTERY, "Ah")
REG_BATTERY_SOC = ModbusRegister(514, "battery_soc", "Battery actual SOC", DataType.UINT16, 1, Access.RO, RegisterGroup.BATTERY, "%")
REG_BATTERY_CHARGE_CURRENT = ModbusRegister(190, "battery_charge_current", "Battery charge current", DataType.FLOAT32, 2, Access.RO, RegisterGroup.BATTERY, "A")
REG_BATTERY_CYCLES = ModbusRegister(194, "battery_cycles", "Number of battery cycles", DataType.FLOAT32, 2, Access.RO, RegisterGroup.BATTERY)
REG_BATTERY_ACTUAL_CURRENT = ModbusRegister(200, "battery_actual_current", "Actual battery charge/discharge current", DataType.FLOAT32, 2, Access.RO, RegisterGroup.BATTERY, "A")
REG_BATTERY_STATE_OF_CHARGE = ModbusRegister(210, "battery_state_of_charge", "Act. state of charge", DataType.FLOAT32, 2, Access.RO, RegisterGroup.BATTERY, "%")
REG_BATTERY_TEMPERATURE = ModbusRegister(214, "battery_temperature", "Battery temperature", DataType.FLOAT32, 2, Access.RO, RegisterGroup.BATTERY, "°C")
REG_BATTERY_VOLTAGE = ModbusRegister(216, "battery_voltage", "Battery voltage", DataType.FLOAT32, 2, Access.RO, RegisterGroup.BATTERY, "V")
REG_BATTERY_TYPE = ModbusRegister(588, "battery_type", "Battery Type", DataType.UINT16, 1, Access.RO, RegisterGroup.BATTERY)
REG_BATTERY_WORK_CAPACITY = ModbusRegister(1068, "battery_work_capacity", "Battery work capacity", DataType.FLOAT32, 2, Access.RO, RegisterGroup.BATTERY, "Wh")
REG_BATTERY_SERIAL = ModbusRegister(1070, "battery_serial", "Battery serial number", DataType.UINT32, 2, Access.RO, RegisterGroup.BATTERY)
REG_BATTERY_MAX_CHARGE_FROM_BAT = ModbusRegister(1076, "battery_max_charge_hw", "Max charge power limit (from battery)", DataType.FLOAT32, 2, Access.RO, RegisterGroup.BATTERY, "W")
REG_BATTERY_MAX_DISCHARGE_FROM_BAT = ModbusRegister(1078, "battery_max_discharge_hw", "Max discharge power limit (from battery)", DataType.FLOAT32, 2, Access.RO, RegisterGroup.BATTERY, "W")
REG_BATTERY_MGMT_MODE = ModbusRegister(1080, "battery_mgmt_mode", "Battery management mode", DataType.UINT8, 1, Access.RO, RegisterGroup.BATTERY)
REG_SENSOR_TYPE = ModbusRegister(1082, "sensor_type", "Installed sensor type", DataType.UINT8, 1, Access.RO, RegisterGroup.BATTERY)

# Inverter info registers
REG_INVERTER_MAX_POWER = ModbusRegister(531, "inverter_max_power", "Inverter Max Power", DataType.UINT16, 1, Access.RO, RegisterGroup.DEVICE_INFO, "W")
REG_INVERTER_GEN_POWER = ModbusRegister(575, "inverter_gen_power", "Inverter Generation Power (actual)", DataType.SINT16, 1, Access.RO, RegisterGroup.POWER, "W")
REG_GENERATION_ENERGY = ModbusRegister(577, "generation_energy", "Generation Energy", DataType.UINT32, 2, Access.RO, RegisterGroup.ENERGY, "Wh")
REG_BATTERY_CHARGE_DISCHARGE_POWER = ModbusRegister(582, "battery_cd_power", "Actual battery charge/discharge power", DataType.SINT16, 1, Access.RO, RegisterGroup.BATTERY, "W")

# ---------------------------------------------------------------------------
# Energy totals (read-only)
# ---------------------------------------------------------------------------
REG_TOTAL_DC_CHARGE = ModbusRegister(1046, "total_dc_charge", "Total DC charge energy (to battery)", DataType.FLOAT32, 2, Access.RO, RegisterGroup.ENERGY, "Wh")
REG_TOTAL_DC_DISCHARGE = ModbusRegister(1048, "total_dc_discharge", "Total DC discharge energy (from battery)", DataType.FLOAT32, 2, Access.RO, RegisterGroup.ENERGY, "Wh")
REG_TOTAL_AC_CHARGE = ModbusRegister(1050, "total_ac_charge", "Total AC charge energy (to battery)", DataType.FLOAT32, 2, Access.RO, RegisterGroup.ENERGY, "Wh")
REG_TOTAL_AC_DISCHARGE = ModbusRegister(1052, "total_ac_discharge", "Total AC discharge energy (battery to grid)", DataType.FLOAT32, 2, Access.RO, RegisterGroup.ENERGY, "Wh")
REG_TOTAL_AC_CHARGE_GRID = ModbusRegister(1054, "total_ac_charge_grid", "Total AC charge energy (grid to battery)", DataType.FLOAT32, 2, Access.RO, RegisterGroup.ENERGY, "Wh")
REG_TOTAL_DC_PV_ENERGY = ModbusRegister(1056, "total_dc_pv_energy", "Total DC PV energy (all inputs)", DataType.FLOAT32, 2, Access.RO, RegisterGroup.ENERGY, "Wh")
REG_TOTAL_DC_PV1 = ModbusRegister(1058, "total_dc_pv1", "Total DC energy from PV1", DataType.FLOAT32, 2, Access.RO, RegisterGroup.ENERGY, "Wh")
REG_TOTAL_DC_PV2 = ModbusRegister(1060, "total_dc_pv2", "Total DC energy from PV2", DataType.FLOAT32, 2, Access.RO, RegisterGroup.ENERGY, "Wh")
REG_TOTAL_DC_PV3 = ModbusRegister(1062, "total_dc_pv3", "Total DC energy from PV3", DataType.FLOAT32, 2, Access.RO, RegisterGroup.ENERGY, "Wh")
REG_TOTAL_AC_TO_GRID = ModbusRegister(1064, "total_ac_to_grid", "Total energy AC-side to grid", DataType.FLOAT32, 2, Access.RO, RegisterGroup.ENERGY, "Wh")
REG_TOTAL_DC_POWER_ALL = ModbusRegister(1066, "total_dc_power_all", "Total DC power (all PV inputs)", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWER, "W")

# ---------------------------------------------------------------------------
# Powermeter registers (read-only)
# ---------------------------------------------------------------------------
REG_PM_TOTAL_ACTIVE = ModbusRegister(252, "pm_total_active", "Total active power (powermeter)", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWERMETER, "W")
REG_PM_TOTAL_REACTIVE = ModbusRegister(254, "pm_total_reactive", "Total reactive power (powermeter)", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWERMETER, "Var")
REG_PM_TOTAL_APPARENT = ModbusRegister(256, "pm_total_apparent", "Total apparent power (powermeter)", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWERMETER, "VA")
REG_PM_COS_PHI = ModbusRegister(218, "pm_cos_phi", "Cos phi (powermeter)", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWERMETER)
REG_PM_FREQUENCY = ModbusRegister(220, "pm_frequency", "Frequency (powermeter)", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWERMETER, "Hz")
REG_PM_L1_CURRENT = ModbusRegister(222, "pm_l1_current", "Current phase 1 (powermeter)", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWERMETER, "A")
REG_PM_L1_ACTIVE = ModbusRegister(224, "pm_l1_active", "Active power phase 1 (powermeter)", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWERMETER, "W")
REG_PM_L1_REACTIVE = ModbusRegister(226, "pm_l1_reactive", "Reactive power phase 1 (powermeter)", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWERMETER, "Var")
REG_PM_L1_APPARENT = ModbusRegister(228, "pm_l1_apparent", "Apparent power phase 1 (powermeter)", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWERMETER, "VA")
REG_PM_L1_VOLTAGE = ModbusRegister(230, "pm_l1_voltage", "Voltage phase 1 (powermeter)", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWERMETER, "V")
REG_PM_L2_CURRENT = ModbusRegister(232, "pm_l2_current", "Current phase 2 (powermeter)", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWERMETER, "A")
REG_PM_L2_ACTIVE = ModbusRegister(234, "pm_l2_active", "Active power phase 2 (powermeter)", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWERMETER, "W")
REG_PM_L2_REACTIVE = ModbusRegister(236, "pm_l2_reactive", "Reactive power phase 2 (powermeter)", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWERMETER, "Var")
REG_PM_L2_APPARENT = ModbusRegister(238, "pm_l2_apparent", "Apparent power phase 2 (powermeter)", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWERMETER, "VA")
REG_PM_L2_VOLTAGE = ModbusRegister(240, "pm_l2_voltage", "Voltage phase 2 (powermeter)", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWERMETER, "V")
REG_PM_L3_CURRENT = ModbusRegister(242, "pm_l3_current", "Current phase 3 (powermeter)", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWERMETER, "A")
REG_PM_L3_ACTIVE = ModbusRegister(244, "pm_l3_active", "Active power phase 3 (powermeter)", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWERMETER, "W")
REG_PM_L3_REACTIVE = ModbusRegister(246, "pm_l3_reactive", "Reactive power phase 3 (powermeter)", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWERMETER, "Var")
REG_PM_L3_APPARENT = ModbusRegister(248, "pm_l3_apparent", "Apparent power phase 3 (powermeter)", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWERMETER, "VA")
REG_PM_L3_VOLTAGE = ModbusRegister(250, "pm_l3_voltage", "Voltage phase 3 (powermeter)", DataType.FLOAT32, 2, Access.RO, RegisterGroup.POWERMETER, "V")

# Additional battery/system metadata registers used on older/newer firmware tracks
REG_PSSB_FUSE_STATE = ModbusRegister(202, "pssb_fuse_state", "PSSB fuse state", DataType.FLOAT32, 2, Access.RO, RegisterGroup.BATTERY)
REG_BATTERY_READY_FLAG = ModbusRegister(208, "battery_ready_flag", "Battery ready flag", DataType.FLOAT32, 2, Access.RO, RegisterGroup.BATTERY)
REG_FW_MAINCONTROLLER = ModbusRegister(515, "fw_maincontroller", "Firmware maincontroller", DataType.UINT32, 2, Access.RO, RegisterGroup.DEVICE_INFO)
REG_BATTERY_MANUFACTURER = ModbusRegister(517, "battery_manufacturer", "Battery manufacturer", DataType.STRING, 8, Access.RO, RegisterGroup.BATTERY)
REG_BATTERY_MODEL_ID = ModbusRegister(525, "battery_model_id", "Battery model ID", DataType.UINT32, 2, Access.RO, RegisterGroup.BATTERY)
REG_BATTERY_SERIAL_ALT = ModbusRegister(527, "battery_serial_alt", "Battery serial number (alt)", DataType.UINT32, 2, Access.RO, RegisterGroup.BATTERY)
REG_BATTERY_WORK_CAPACITY_SUNSPEC = ModbusRegister(529, "battery_work_capacity_sunspec", "Battery work capacity (SunSpec)", DataType.UINT32, 2, Access.RO, RegisterGroup.BATTERY, "Wh")
REG_BATTERY_NET_CAPACITY = ModbusRegister(580, "battery_net_capacity", "Battery net capacity", DataType.UINT32, 2, Access.RO, RegisterGroup.BATTERY, "Ah")
REG_BATTERY_FW_VERSION = ModbusRegister(586, "battery_fw_version", "Battery firmware", DataType.UINT32, 2, Access.RO, RegisterGroup.BATTERY)

# ---------------------------------------------------------------------------
# Control registers (read/write) – active/reactive power control
# ---------------------------------------------------------------------------
REG_ACTIVE_POWER_SETPOINT = ModbusRegister(533, "active_power_setpoint", "Active power setpoint", DataType.UINT16, 1, Access.RW, RegisterGroup.CONTROL, "%")
REG_REACTIVE_POWER_SETPOINT = ModbusRegister(583, "reactive_power_setpoint", "Reactive power setpoint", DataType.SINT16, 1, Access.RW, RegisterGroup.CONTROL, "%")
REG_DELTA_COS_PHI = ModbusRegister(585, "delta_cos_phi", "Delta-cos phi setpoint", DataType.SINT16, 1, Access.RW, RegisterGroup.CONTROL)
REG_LOW_PRIO_ACTIVE_POWER = ModbusRegister(832, "low_prio_active_power", "Low-Priority Active power setpoint", DataType.UINT16, 1, Access.RW, RegisterGroup.CONTROL, "W")

# ---------------------------------------------------------------------------
# I/O Board registers (read/write)
# ---------------------------------------------------------------------------
REG_IO_OUTPUT_1 = ModbusRegister(608, "io_output_1", "I/O-Board, Switched Output 1", DataType.UINT16, 1, Access.RW, RegisterGroup.IO_BOARD)
REG_IO_OUTPUT_2 = ModbusRegister(609, "io_output_2", "I/O-Board, Switched Output 2", DataType.UINT16, 1, Access.RW, RegisterGroup.IO_BOARD)
REG_IO_OUTPUT_3 = ModbusRegister(610, "io_output_3", "I/O-Board, Switched Output 3", DataType.UINT16, 1, Access.RW, RegisterGroup.IO_BOARD)
REG_IO_OUTPUT_4 = ModbusRegister(611, "io_output_4", "I/O-Board, Switched Output 4", DataType.UINT16, 1, Access.RW, RegisterGroup.IO_BOARD)

# ---------------------------------------------------------------------------
# External battery management registers (read/write) – Section 3.4
# ---------------------------------------------------------------------------
REG_BAT_CHARGE_AC_SETPOINT = ModbusRegister(1024, "bat_charge_ac_setpoint", "Battery charge power (AC) setpoint", DataType.SINT16, 1, Access.RO, RegisterGroup.BATTERY_MGMT, "W")
REG_BAT_CHARGE_AC_SCALE = ModbusRegister(1025, "bat_charge_ac_scale", "Power Scale Factor", DataType.SINT16, 1, Access.RO, RegisterGroup.BATTERY_MGMT)
REG_BAT_CHARGE_AC_ABS = ModbusRegister(1026, "bat_charge_ac_abs", "Battery charge power (AC) absolute", DataType.FLOAT32, 2, Access.RW, RegisterGroup.BATTERY_MGMT, "W")
REG_BAT_CHARGE_DC_REL = ModbusRegister(1028, "bat_charge_dc_rel", "Battery charge current (DC) relative", DataType.FLOAT32, 2, Access.RW, RegisterGroup.BATTERY_MGMT, "%")
REG_BAT_CHARGE_AC_REL = ModbusRegister(1030, "bat_charge_ac_rel", "Battery charge power (AC) relative", DataType.FLOAT32, 2, Access.RW, RegisterGroup.BATTERY_MGMT, "%")
REG_BAT_CHARGE_DC_ABS_CURRENT = ModbusRegister(1032, "bat_charge_dc_abs_current", "Battery charge current (DC) absolute", DataType.FLOAT32, 2, Access.RW, RegisterGroup.BATTERY_MGMT, "A")
REG_BAT_CHARGE_DC_ABS_POWER = ModbusRegister(1034, "bat_charge_dc_abs_power", "Battery charge power (DC) absolute", DataType.FLOAT32, 2, Access.RW, RegisterGroup.BATTERY_MGMT, "W")
REG_BAT_CHARGE_DC_REL_POWER = ModbusRegister(1036, "bat_charge_dc_rel_power", "Battery charge power (DC) relative", DataType.FLOAT32, 2, Access.RW, RegisterGroup.BATTERY_MGMT, "%")
REG_BAT_MAX_CHARGE_LIMIT = ModbusRegister(1038, "bat_max_charge_limit", "Battery max charge power limit", DataType.FLOAT32, 2, Access.RW, RegisterGroup.BATTERY_MGMT, "W")
REG_BAT_MAX_DISCHARGE_LIMIT = ModbusRegister(1040, "bat_max_discharge_limit", "Battery max discharge power limit", DataType.FLOAT32, 2, Access.RW, RegisterGroup.BATTERY_MGMT, "W")
REG_BAT_MIN_SOC = ModbusRegister(1042, "bat_min_soc", "Minimum SOC", DataType.FLOAT32, 2, Access.RW, RegisterGroup.BATTERY_MGMT, "%")
REG_BAT_MAX_SOC = ModbusRegister(1044, "bat_max_soc", "Maximum SOC", DataType.FLOAT32, 2, Access.RW, RegisterGroup.BATTERY_MGMT, "%")

# ---------------------------------------------------------------------------
# G3 Battery limitation registers (read/write) – Section 3.5
# ---------------------------------------------------------------------------
REG_G3_MAX_CHARGE = ModbusRegister(1280, "g3_max_charge", "Max battery charge power", DataType.FLOAT32, 2, Access.RW, RegisterGroup.BATTERY_LIMIT_G3, "W")
REG_G3_MAX_DISCHARGE = ModbusRegister(1282, "g3_max_discharge", "Max battery discharge power", DataType.FLOAT32, 2, Access.RW, RegisterGroup.BATTERY_LIMIT_G3, "W")
REG_G3_MAX_CHARGE_FALLBACK = ModbusRegister(1284, "g3_max_charge_fallback", "Max battery charge power (fallback)", DataType.FLOAT32, 2, Access.RW, RegisterGroup.BATTERY_LIMIT_G3, "W")
REG_G3_MAX_DISCHARGE_FALLBACK = ModbusRegister(1286, "g3_max_discharge_fallback", "Max battery discharge power (fallback)", DataType.FLOAT32, 2, Access.RW, RegisterGroup.BATTERY_LIMIT_G3, "W")
REG_G3_FALLBACK_TIME = ModbusRegister(1288, "g3_fallback_time", "Time until fallback", DataType.UINT32, 2, Access.RW, RegisterGroup.BATTERY_LIMIT_G3, "s")

# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------
ALL_REGISTERS: Final[tuple[ModbusRegister, ...]] = (
    REG_MODBUS_ENABLE, REG_UNIT_ID, REG_BYTE_ORDER,
    REG_ARTICLE_NUMBER, REG_SERIAL_NUMBER,
    REG_NUM_BIDIRECTIONAL, REG_NUM_AC_PHASES, REG_NUM_PV_STRINGS,
    REG_HW_VERSION, REG_SW_VERSION_MC, REG_SW_VERSION_IOC,
    REG_POWER_ID, REG_INVERTER_STATE, REG_SW_VERSION,
    REG_PRODUCT_NAME, REG_POWER_CLASS,
    REG_CONTROLLER_TEMP, REG_TOTAL_DC_POWER, REG_EM_STATE,
    REG_HOME_FROM_BATTERY, REG_HOME_FROM_GRID,
    REG_TOTAL_HOME_BATTERY, REG_TOTAL_HOME_GRID, REG_TOTAL_HOME_PV,
    REG_HOME_FROM_PV, REG_TOTAL_HOME_CONSUMPTION,
    REG_ISOLATION_RESISTANCE, REG_POWER_LIMIT_EVU, REG_HOME_CONSUMPTION_RATE,
    REG_WORKTIME, REG_COS_PHI, REG_GRID_FREQUENCY,
    REG_TOTAL_AC_POWER, REG_TOTAL_AC_REACTIVE, REG_TOTAL_AC_APPARENT,
    REG_PHASE1_CURRENT, REG_PHASE1_POWER, REG_PHASE1_VOLTAGE,
    REG_PHASE2_CURRENT, REG_PHASE2_POWER, REG_PHASE2_VOLTAGE,
    REG_PHASE3_CURRENT, REG_PHASE3_POWER, REG_PHASE3_VOLTAGE,
    REG_DC1_CURRENT, REG_DC1_POWER, REG_DC1_VOLTAGE,
    REG_DC2_CURRENT, REG_DC2_POWER, REG_DC2_VOLTAGE,
    REG_DC3_CURRENT, REG_DC3_POWER, REG_DC3_VOLTAGE,
    REG_TOTAL_YIELD, REG_DAILY_YIELD, REG_YEARLY_YIELD, REG_MONTHLY_YIELD,
    REG_BATTERY_GROSS_CAPACITY, REG_BATTERY_SOC,
    REG_BATTERY_CHARGE_CURRENT, REG_BATTERY_CYCLES,
    REG_BATTERY_ACTUAL_CURRENT, REG_BATTERY_STATE_OF_CHARGE,
    REG_BATTERY_TEMPERATURE, REG_BATTERY_VOLTAGE,
    REG_BATTERY_TYPE, REG_BATTERY_WORK_CAPACITY,
    REG_BATTERY_SERIAL, REG_BATTERY_MAX_CHARGE_FROM_BAT,
    REG_BATTERY_MAX_DISCHARGE_FROM_BAT, REG_BATTERY_MGMT_MODE,
    REG_SENSOR_TYPE,
    REG_INVERTER_MAX_POWER, REG_INVERTER_GEN_POWER,
    REG_GENERATION_ENERGY, REG_BATTERY_CHARGE_DISCHARGE_POWER,
    REG_TOTAL_DC_CHARGE, REG_TOTAL_DC_DISCHARGE,
    REG_TOTAL_AC_CHARGE, REG_TOTAL_AC_DISCHARGE,
    REG_TOTAL_AC_CHARGE_GRID, REG_TOTAL_DC_PV_ENERGY,
    REG_TOTAL_DC_PV1, REG_TOTAL_DC_PV2, REG_TOTAL_DC_PV3,
    REG_TOTAL_AC_TO_GRID, REG_TOTAL_DC_POWER_ALL,
    REG_PM_TOTAL_ACTIVE, REG_PM_TOTAL_REACTIVE, REG_PM_TOTAL_APPARENT,
    REG_PM_COS_PHI, REG_PM_FREQUENCY,
    REG_PM_L1_CURRENT, REG_PM_L1_ACTIVE, REG_PM_L1_REACTIVE, REG_PM_L1_APPARENT, REG_PM_L1_VOLTAGE,
    REG_PM_L2_CURRENT, REG_PM_L2_ACTIVE, REG_PM_L2_REACTIVE, REG_PM_L2_APPARENT, REG_PM_L2_VOLTAGE,
    REG_PM_L3_CURRENT, REG_PM_L3_ACTIVE, REG_PM_L3_REACTIVE, REG_PM_L3_APPARENT, REG_PM_L3_VOLTAGE,
    REG_PSSB_FUSE_STATE, REG_BATTERY_READY_FLAG,
    REG_FW_MAINCONTROLLER, REG_BATTERY_MANUFACTURER, REG_BATTERY_MODEL_ID,
    REG_BATTERY_SERIAL_ALT, REG_BATTERY_WORK_CAPACITY_SUNSPEC, REG_BATTERY_NET_CAPACITY,
    REG_BATTERY_FW_VERSION,
    REG_ACTIVE_POWER_SETPOINT, REG_REACTIVE_POWER_SETPOINT,
    REG_DELTA_COS_PHI, REG_LOW_PRIO_ACTIVE_POWER,
    REG_IO_OUTPUT_1, REG_IO_OUTPUT_2, REG_IO_OUTPUT_3, REG_IO_OUTPUT_4,
    REG_BAT_CHARGE_AC_SETPOINT, REG_BAT_CHARGE_AC_SCALE,
    REG_BAT_CHARGE_AC_ABS, REG_BAT_CHARGE_DC_REL,
    REG_BAT_CHARGE_AC_REL, REG_BAT_CHARGE_DC_ABS_CURRENT,
    REG_BAT_CHARGE_DC_ABS_POWER, REG_BAT_CHARGE_DC_REL_POWER,
    REG_BAT_MAX_CHARGE_LIMIT, REG_BAT_MAX_DISCHARGE_LIMIT,
    REG_BAT_MIN_SOC, REG_BAT_MAX_SOC,
    REG_G3_MAX_CHARGE, REG_G3_MAX_DISCHARGE,
    REG_G3_MAX_CHARGE_FALLBACK, REG_G3_MAX_DISCHARGE_FALLBACK,
    REG_G3_FALLBACK_TIME,
)

REGISTER_BY_NAME: Final[dict[str, ModbusRegister]] = {r.name: r for r in ALL_REGISTERS}
REGISTER_BY_ADDRESS: Final[dict[int, ModbusRegister]] = {r.address: r for r in ALL_REGISTERS}

MONITORING_REGISTERS: Final[tuple[ModbusRegister, ...]] = tuple(
    r for r in ALL_REGISTERS if r.access == Access.RO and r.group in (
        RegisterGroup.POWER, RegisterGroup.BATTERY, RegisterGroup.PHASE,
        RegisterGroup.ENERGY, RegisterGroup.POWERMETER,
    )
)

WRITABLE_REGISTERS: Final[tuple[ModbusRegister, ...]] = tuple(
    r for r in ALL_REGISTERS if r.access == Access.RW
)

# Inverter state lookup
INVERTER_STATES: Final[dict[int, str]] = {
    0: "Off", 1: "Init", 2: "IsoMeas", 3: "GridCheck",
    4: "StartUp", 6: "FeedIn", 7: "Throttled",
    8: "ExtSwitchOff", 9: "Update", 10: "Standby",
    11: "GridSync", 12: "GridPreCheck", 13: "GridSwitchOff",
    14: "Overheating", 15: "Shutdown", 16: "ImproperDcVoltage",
    17: "ESB", 18: "Unknown", 19: "DcCheck",
}

BATTERY_TYPES: Final[dict[int, str]] = {
    0x0000: "No battery", 0x0002: "PIKO Battery Li",
    0x0004: "BYD", 0x0008: "BMZ", 0x0010: "AXIstorage Li SH",
    0x0040: "LG", 0x0200: "Pyontech Force H",
    0x0400: "AXIstorage Li SV", 0x1000: "Dyness Tower/TowerPro",
    0x2000: "VARTA.wall", 0x4000: "ZYC",
}

BATTERY_MGMT_MODES: Final[dict[int, str]] = {
    0x00: "No external battery management",
    0x01: "External via digital I/O",
    0x02: "External via MODBUS",
}
