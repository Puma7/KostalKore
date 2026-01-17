"""Code to handle the Plenticore API."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

from pykoplenti import ApiClient, ApiException

_KNOWN_HOSTNAME_IDS: Final[tuple[str, ...]] = ("Network:Hostname", "Hostname")


class PlenticoreDataFormatter:
    """Provides method to format values of process or settings data."""

    INVERTER_STATES: Final[dict[int, str]] = {
        0: "Off",
        1: "Init",
        2: "IsoMEas",
        3: "GridCheck",
        4: "StartUp",
        6: "FeedIn",
        7: "Throttled",
        8: "ExtSwitchOff",
        9: "Update",
        10: "Standby",
        11: "GridSync",
        12: "GridPreCheck",
        13: "GridSwitchOff",
        14: "Overheating",
        15: "Shutdown",
        16: "ImproperDcVoltage",
        17: "ESB",
    }

    EM_STATES: Final[dict[int, str]] = {
        0: "Idle",
        1: "n/a",
        2: "Emergency Battery Charge",
        4: "n/a",
        8: "Winter Mode Step 1",
        16: "Winter Mode Step 2",
    }

    @classmethod
    def get_method(cls, name: str) -> Callable[[Any], Any]:
        """Return a callable formatter of the given name."""
        return getattr(cls, name)

    @staticmethod
    def format_round(state: str) -> int | str:
        """Return the given state value as rounded integer."""
        try:
            return round(float(state))
        except (TypeError, ValueError):
            return state

    @staticmethod
    def format_round_back(value: float) -> str:
        """Return a rounded integer value from a float."""
        try:
            if isinstance(value, float) and value.is_integer():
                int_value = int(value)
            elif isinstance(value, int):
                int_value = value
            else:
                int_value = round(value)

            return str(int_value)
        except (TypeError, ValueError):
            return ""

    @staticmethod
    def format_float(state: str) -> float | str:
        """Return the given state value as float rounded to three decimal places."""
        try:
            return round(float(state), 3)
        except (TypeError, ValueError):
            return state

    @staticmethod
    def format_float_back(value: float) -> str:
        """Return the given float value as string for the inverter API."""
        try:
            return str(round(float(value), 3))
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def format_energy(state: str) -> float | str:
        """Return the given state value as energy value, scaled to kWh."""
        try:
            return round(float(state) / 1000, 1)
        except (TypeError, ValueError):
            return state

    @staticmethod
    def format_inverter_state(state: str) -> str | None:
        """Return a readable string of the inverter state."""
        try:
            value = int(state)
        except (TypeError, ValueError):
            return state

        return PlenticoreDataFormatter.INVERTER_STATES.get(value)

    @staticmethod
    def format_em_manager_state(state: str) -> str | None:
        """Return a readable state of the energy manager."""
        try:
            value = int(state)
        except (TypeError, ValueError):
            return state

        return PlenticoreDataFormatter.EM_STATES.get(value)

    @staticmethod
    def format_battery_management_mode(state: str) -> str:
        """Return readable battery management mode."""
        modes: Final[dict[int, str]] = {
            0x00: "No external battery management",
            0x01: "External management via digital I/O",
            0x02: "External management via MODBUS"
        }
        try:
            return modes.get(int(state), f"Unknown mode: {state}")
        except (TypeError, ValueError):
            return state

    @staticmethod
    def format_sensor_type(state: str) -> str:
        """Return readable sensor type."""
        sensors: Final[dict[int, str]] = {
            0x00: "SDM 630 (B+G E-Tech)",
            0x01: "B-Control EM-300 LR",
            0x02: "Reserved",
            0x03: "KOSTAL Smart Energy Meter",
            0xFF: "No sensor"
        }
        try:
            return sensors.get(int(state), f"Unknown sensor: {state}")
        except (TypeError, ValueError):
            return state

    @staticmethod
    def format_string(state: str) -> str:
        """Return the string value as-is."""
        return state

    @staticmethod
    def format_battery_type(state: str) -> str:
        """Return readable battery type."""
        battery_types: Final[dict[int, str]] = {
            0x0000: "No battery (PV-Functionality)",
            0x0002: "PIKO Battery Li",
            0x0004: "BYD",
            0x0008: "BMZ",
            0x0010: "AXIstorage Li SH",
            0x0040: "LG",
            0x0200: "Pyontech Force H",
            0x0400: "AXIstorage Li SV",
            0x1000: "Dyness Tower / TowerPro",
            0x2000: "VARTA.wall",
            0x4000: "ZYC",
        }
        try:
            value = int(state)
            return battery_types.get(value, f"Unknown battery type: {state}")
        except (TypeError, ValueError):
            return state

    @staticmethod
    def format_pssb_fuse_state(state: str) -> str:
        """Return readable PSSB fuse state."""
        fuse_states: Final[dict[int, str]] = {
            0x00: "Fuse fail",
            0x01: "Fuse ok",
            0xFF: "Unchecked",
        }
        try:
            value = int(state)
            return fuse_states.get(value, f"Unknown fuse state: {state}")
        except (TypeError, ValueError):
            return state


async def get_hostname_id(client: ApiClient) -> str:
    """Check for known existing hostname ids."""
    all_settings = await client.get_settings()
    for entry in all_settings["scb:network"]:
        if entry.id in _KNOWN_HOSTNAME_IDS:
            return entry.id
    raise ApiException("Hostname identifier not found in KNOWN_HOSTNAME_IDS")
