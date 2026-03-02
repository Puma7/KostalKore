"""Tests for newly implemented backlog feature primitives."""

from __future__ import annotations

from datetime import timedelta
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from pykoplenti import EventData

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kostal_kore.const import DOMAIN
from custom_components.kostal_kore.coordinator import (
    EventDataUpdateCoordinator,
    Plenticore,
)
from custom_components.kostal_kore.helper import (
    is_allowed_write_target,
    requires_advanced_write_arm,
    validate_cross_field_write_rules,
)
from custom_components.kostal_kore.modbus_client import (
    KostalModbusClient,
    ModbusReadError,
)
from custom_components.kostal_kore.modbus_registers import REG_INVERTER_GEN_POWER


def _mk_event(code: int, category: str = "error", active: bool = True) -> EventData:
    from homeassistant.util import dt as dt_util

    now = dt_util.utcnow()
    return EventData(
        start_time=now - timedelta(seconds=10),
        end_time=now,
        code=code,
        long_description=f"Event {code}",
        category=category,
        description=f"Event {code}",
        group="test",
        is_active=active,
    )


@pytest.mark.asyncio
async def test_event_coordinator_dedup_and_snapshot(hass) -> None:
    """Same event in cooldown is not duplicated in history."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "192.168.1.2", "password": "x"},
    )
    plenticore = MagicMock()
    plenticore.client = MagicMock()
    plenticore.client.get_events = AsyncMock(return_value=[_mk_event(1001)])

    coordinator = EventDataUpdateCoordinator(
        hass=hass,
        config_entry=config_entry,
        logger=logging.getLogger(__name__),
        name="event-test",
        update_interval=timedelta(seconds=30),
        plenticore=plenticore,
    )

    first = await coordinator._async_update_data()
    second = await coordinator._async_update_data()

    assert first["last_event_code"] == 1001
    assert second["active_error_events_count"] == 1
    assert len(coordinator.history) == 1


def test_write_policy_helpers() -> None:
    """Allowlist/arming and cross-field validation helpers behave as expected."""
    assert is_allowed_write_target("devices:local", "Battery:MinSoc")
    assert not is_allowed_write_target("scb:network", "Hostname")

    assert requires_advanced_write_arm("Battery:BackupMode:Enable")
    assert requires_advanced_write_arm("DigitalOutputs:Customer:ConfigurationFlags")
    assert requires_advanced_write_arm("Battery:ChargePowerDcAbs")
    assert requires_advanced_write_arm("Battery:ChargePowerAcRelative")
    assert not requires_advanced_write_arm("Battery:MinSoc")

    err = validate_cross_field_write_rules(
        "DigitalOutputs:Customer:PowerMode:OnPowerThreshold",
        "100",
        {"DigitalOutputs:Customer:PowerMode:OffPowerThreshold": "120"},
    )
    assert err is not None

    err_off = validate_cross_field_write_rules(
        "DigitalOutputs:Customer:PowerMode:OffPowerThreshold",
        "250",
        {"DigitalOutputs:Customer:PowerMode:OnPowerThreshold": "200"},
    )
    assert err_off is not None

    # Parsing errors are handled gracefully and delegated downstream.
    assert (
        validate_cross_field_write_rules(
            "DigitalOutputs:Customer:PowerMode:OffPowerThreshold",
            "not-a-number",
            {"DigitalOutputs:Customer:PowerMode:OnPowerThreshold": "200"},
        )
        is None
    )
    assert (
        validate_cross_field_write_rules(
            "DigitalOutputs:Customer:PowerMode:OnPowerThreshold",
            "250",
            {"DigitalOutputs:Customer:PowerMode:OffPowerThreshold": "200"},
        )
        is None
    )
    assert (
        validate_cross_field_write_rules(
            "DigitalOutputs:Customer:PowerMode:OnPowerThreshold",
            "250",
            {"SomeOtherThreshold": "200"},
        )
        is None
    )
    assert (
        validate_cross_field_write_rules(
            "DigitalOutputs:Customer:PowerMode:OffPowerThreshold",
            "150",
            {"DigitalOutputs:Customer:PowerMode:OnPowerThreshold": "200"},
        )
        is None
    )
    assert (
        validate_cross_field_write_rules(
            "DigitalOutputs:Customer:PowerMode:OffPowerThreshold",
            "150",
            {"SomeOtherThreshold": "200"},
        )
        is None
    )


def test_modbus_sentinel_value_quality_filter() -> None:
    """Register 575 sentinel handling keeps last good value or raises."""
    client = KostalModbusClient(host="127.0.0.1")

    with pytest.raises(ModbusReadError):
        client._apply_quality_filter(REG_INVERTER_GEN_POWER, 32767)

    client._last_good_values[REG_INVERTER_GEN_POWER.address] = 4242
    filtered = client._apply_quality_filter(REG_INVERTER_GEN_POWER, 32767)
    assert filtered == 4242


def test_plenticore_advanced_write_arm_window(hass) -> None:
    """Arming state is toggled by explicit arm/disarm operations."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "192.168.1.2", "password": "x"},
    )
    plenticore = Plenticore(hass, config_entry)
    assert not plenticore.is_advanced_write_armed
    plenticore.arm_advanced_writes(ttl_seconds=30)
    assert plenticore.is_advanced_write_armed
    plenticore.disarm_advanced_writes()
    assert not plenticore.is_advanced_write_armed
