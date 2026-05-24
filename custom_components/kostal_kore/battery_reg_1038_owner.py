"""Mutual exclusion for Modbus REG 1038 (bat_max_charge_limit).

Only one integration feature may actively drive the charge-power limit at a
time: Grid Feed-In Optimizer, SoC Controller, or Block Battery Charging.
"""

from __future__ import annotations

from typing import Final

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .helper import integration_entry_store

OWNER_GRID_FEEDIN: Final[str] = "grid_feedin_optimizer"
OWNER_SOC_CONTROLLER: Final[str] = "soc_controller"
OWNER_CHARGE_BLOCK: Final[str] = "charge_block"
OWNER_BATTERY_TEST: Final[str] = "battery_test"

KEY_REG_1038_OWNER: Final[str] = "reg_1038_owner"

_OWNER_LABELS: Final[dict[str, str]] = {
    OWNER_GRID_FEEDIN: "Grid Feed-In Optimizer",
    OWNER_SOC_CONTROLLER: "SoC Controller",
    OWNER_CHARGE_BLOCK: "Block Battery Charging",
    OWNER_BATTERY_TEST: "Battery Charge/Discharge Test",
}


class Reg1038OwnerManager:
    """Track which feature currently owns writes to REG 1038."""

    def __init__(self) -> None:
        self._owner: str | None = None

    @property
    def current(self) -> str | None:
        return self._owner

    def label(self, owner: str | None) -> str:
        if owner is None:
            return "none"
        return _OWNER_LABELS.get(owner, owner)

    def try_acquire(self, owner: str) -> bool:
        if self._owner is None or self._owner == owner:
            self._owner = owner
            return True
        return False

    def release(self, owner: str) -> None:
        if self._owner == owner:
            self._owner = None

    def require_owner(self, owner: str) -> None:
        if self._owner != owner:
            raise RuntimeError(
                f"REG 1038 owner mismatch: expected {owner!r}, got {self._owner!r}"
            )


def get_reg_1038_owner_manager(
    hass: HomeAssistant, entry_id: str
) -> Reg1038OwnerManager:
    """Return the per-entry owner manager (creates on first use)."""
    store = integration_entry_store(hass, entry_id)
    mgr = store.get(KEY_REG_1038_OWNER)
    if mgr is None:
        mgr = Reg1038OwnerManager()
        store[KEY_REG_1038_OWNER] = mgr
    return mgr


def acquire_reg_1038_or_raise(
    hass: HomeAssistant, entry_id: str, owner: str
) -> Reg1038OwnerManager:
    """Acquire REG 1038 for ``owner`` or raise a user-visible error."""
    mgr = get_reg_1038_owner_manager(hass, entry_id)
    if mgr.try_acquire(owner):
        return mgr
    blocking = mgr.current
    raise HomeAssistantError(
        f"Battery charge limit register (1038) is already controlled by "
        f"{mgr.label(blocking)}. Turn that feature off before enabling "
        f"{mgr.label(owner)}."
    )


def release_reg_1038(hass: HomeAssistant, entry_id: str, owner: str) -> None:
    """Release REG 1038 if held by ``owner``."""
    store = integration_entry_store(hass, entry_id)
    mgr = store.get(KEY_REG_1038_OWNER)
    if isinstance(mgr, Reg1038OwnerManager):
        mgr.release(owner)


def reg_1038_owner_for_entry(hass: HomeAssistant, entry_id: str) -> str | None:
    """Human-readable label of the current REG 1038 owner, if any."""
    store = hass.data.get(DOMAIN, {}).get(entry_id)
    if not isinstance(store, dict):
        return None
    mgr = store.get(KEY_REG_1038_OWNER)
    if not isinstance(mgr, Reg1038OwnerManager):
        return None
    current = mgr.current
    return mgr.label(current) if current else None
