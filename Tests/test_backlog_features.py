"""Tests for newly implemented backlog feature primitives."""

from __future__ import annotations

from datetime import timedelta
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pykoplenti import EventData

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kostal_kore.const import DOMAIN
from custom_components.kostal_kore.coordinator import (
    EventDataUpdateCoordinator,
    Plenticore,
    SelectDataUpdateCoordinator,
)
from custom_components.kostal_kore.helper import (
    is_allowed_write_target,
    is_rest_write_supported_target,
    requires_advanced_write_arm,
    validate_cross_field_write_rules,
)
from custom_components.kostal_kore.modbus_client import (
    KostalModbusClient,
    ModbusReadError,
)
from custom_components.kostal_kore.ksem_coordinator import KsemDataUpdateCoordinator
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


@pytest.mark.asyncio
async def test_event_coordinator_first_event_not_deduped_when_monotonic_zero(hass) -> None:
    """First event should be recorded even if monotonic() returns 0."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "192.168.1.2", "password": "x"},
    )
    plenticore = MagicMock()
    plenticore.client = MagicMock()
    plenticore.client.get_events = AsyncMock(return_value=[_mk_event(2001)])

    coordinator = EventDataUpdateCoordinator(
        hass=hass,
        config_entry=config_entry,
        logger=logging.getLogger(__name__),
        name="event-test-monotonic-zero",
        update_interval=timedelta(seconds=30),
        plenticore=plenticore,
    )

    with patch(
        "custom_components.kostal_kore.coordinator.time.monotonic",
        return_value=0.0,
    ):
        result = await coordinator._async_update_data()

    assert result["last_event_code"] == 2001
    assert len(coordinator.history) == 1


def test_write_policy_helpers() -> None:
    """Allowlist/arming and cross-field validation helpers behave as expected."""
    assert is_allowed_write_target("devices:local", "Battery:MinSoc")
    assert not is_allowed_write_target("devices:local", "Battery:ExternControl:AcPowerAbs")
    assert not is_allowed_write_target("scb:network", "Hostname")
    assert not is_rest_write_supported_target("Battery:ExternControl:AcPowerAbs")
    assert not is_rest_write_supported_target("Battery:ChargePowerDcAbs")
    assert is_rest_write_supported_target("Battery:MinSoc")

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


@pytest.mark.asyncio
async def test_ksem_phase_active_power_uses_signed_reads(hass) -> None:
    """Per-phase active power values must preserve export (negative) sign."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "192.168.1.2", "password": "x"},
    )
    coordinator = KsemDataUpdateCoordinator(
        hass=hass,
        config_entry=config_entry,
        host="192.168.1.20",
        port=1502,
        unit_id=71,
    )
    coordinator._ensure_connected = AsyncMock()
    coordinator._read_u32 = AsyncMock(
        side_effect=[3200.0, 400.0, 49.99, 230.1, 229.9, 230.0]
    )
    coordinator._read_i32 = AsyncMock(side_effect=[0.995, -150.0, -50.0, -75.0])

    data = await coordinator._async_update_data()

    assert coordinator._read_i32.await_count == 4
    assert data["l1_active_power_w"] == -150.0
    assert data["l2_active_power_w"] == -50.0
    assert data["l3_active_power_w"] == -75.0


@pytest.mark.asyncio
async def test_ksem_shutdown_calls_base_cleanup(hass) -> None:
    """Shutdown must cancel coordinator polling and close TCP client."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "192.168.1.2", "password": "x"},
    )
    coordinator = KsemDataUpdateCoordinator(
        hass=hass,
        config_entry=config_entry,
        host="192.168.1.20",
        port=1502,
        unit_id=71,
    )
    client = MagicMock()
    coordinator._client = client

    with patch(
        "custom_components.kostal_kore.ksem_coordinator.DataUpdateCoordinator.async_shutdown",
        new_callable=AsyncMock,
    ) as base_shutdown:
        await coordinator.async_shutdown()

    base_shutdown.assert_awaited_once()
    client.close.assert_called_once()
    assert coordinator._client is None


@pytest.mark.asyncio
async def test_select_coordinator_fetch_snapshot_prevents_mutation_runtime_error(
    hass,
) -> None:
    """Mutating tracked select map during update must not crash iteration."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "192.168.1.2", "password": "x"},
    )
    plenticore = MagicMock()
    plenticore.client = MagicMock()

    coordinator = SelectDataUpdateCoordinator(
        hass=hass,
        config_entry=config_entry,
        logger=logging.getLogger(__name__),
        name="select-test",
        update_interval=timedelta(seconds=30),
        plenticore=plenticore,
    )
    coordinator._fetch = {
        "devices:local": {
            "ModeA": ["Opt1", "None"],
            "ModeB": ["Opt2", "None"],
        }
    }

    async def _mutating_read(_module_id: str, _data_id: str):
        coordinator.stop_fetch_data("devices:local", "ModeB", ["Opt2", "None"])
        return {}

    with patch.object(coordinator, "async_read_data", AsyncMock(side_effect=_mutating_read)):
        result = await coordinator._async_update_data()

    assert result["devices:local"]["ModeA"] == "None"
    assert result["devices:local"]["ModeB"] == "None"
