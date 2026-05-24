"""Tests for REG 1038 mutual-exclusion owner manager."""

from __future__ import annotations

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.kostal_kore.battery_reg_1038_owner import (
    OWNER_CHARGE_BLOCK,
    OWNER_GRID_FEEDIN,
    OWNER_SOC_CONTROLLER,
    Reg1038OwnerManager,
    acquire_reg_1038_or_raise,
    get_reg_1038_owner_manager,
    release_reg_1038,
    reg_1038_owner_for_entry,
)
from custom_components.kostal_kore.const import DOMAIN


def test_reg_1038_owner_manager_acquire_release_and_labels() -> None:
    mgr = Reg1038OwnerManager()
    assert mgr.label(None) == "none"
    assert mgr.try_acquire(OWNER_GRID_FEEDIN)
    assert mgr.current == OWNER_GRID_FEEDIN
    assert mgr.label(OWNER_GRID_FEEDIN) == "Grid Feed-In Optimizer"
    assert not mgr.try_acquire(OWNER_SOC_CONTROLLER)
    mgr.release(OWNER_GRID_FEEDIN)
    assert mgr.current is None
    assert mgr.try_acquire(OWNER_SOC_CONTROLLER)
    mgr.require_owner(OWNER_SOC_CONTROLLER)
    with pytest.raises(RuntimeError, match="owner mismatch"):
        mgr.require_owner(OWNER_CHARGE_BLOCK)


def test_get_reg_1038_owner_manager_creates_singleton_per_entry(hass) -> None:
    hass.data.setdefault(DOMAIN, {})["entry_a"] = {}
    first = get_reg_1038_owner_manager(hass, "entry_a")
    second = get_reg_1038_owner_manager(hass, "entry_a")
    assert first is second


def test_acquire_reg_1038_or_raise_and_release(hass) -> None:
    entry_id = "entry_b"
    hass.data.setdefault(DOMAIN, {})[entry_id] = {}
    acquire_reg_1038_or_raise(hass, entry_id, OWNER_GRID_FEEDIN)
    with pytest.raises(HomeAssistantError, match="SoC Controller"):
        acquire_reg_1038_or_raise(hass, entry_id, OWNER_SOC_CONTROLLER)
    release_reg_1038(hass, entry_id, OWNER_GRID_FEEDIN)
    acquire_reg_1038_or_raise(hass, entry_id, OWNER_SOC_CONTROLLER)


def test_reg_1038_owner_for_entry(hass) -> None:
    entry_id = "entry_c"
    hass.data.setdefault(DOMAIN, {})[entry_id] = {}
    assert reg_1038_owner_for_entry(hass, entry_id) is None
    acquire_reg_1038_or_raise(hass, entry_id, OWNER_CHARGE_BLOCK)
    assert reg_1038_owner_for_entry(hass, entry_id) == "Block Battery Charging"


def test_release_reg_1038_without_manager_is_noop(hass) -> None:
    release_reg_1038(hass, "missing_entry", OWNER_GRID_FEEDIN)


def test_reg_1038_owner_for_entry_missing_domain(hass) -> None:
    assert reg_1038_owner_for_entry(hass, "unknown") is None
