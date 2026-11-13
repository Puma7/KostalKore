"""HA 2026.11 renames an entity_id in place only for classes that opt in.

A custom integration class that defines async_added_to_hass,
async_will_remove_from_hass or async_prepare_to_add_to_hass is removed and
re-added on an entity_id change unless it, or a subclass of it, defines
async_entity_id_changed (until HA 2027.11). Re-adding would run KORE's remove
logic, e.g. "Block Battery Charging" would lift the block on a mere rename.
"""

from __future__ import annotations

import importlib
import pkgutil

from homeassistant.helpers.entity import Entity

import custom_components.kostal_kore as kore

# Mirrors homeassistant.helpers.entity._ADD_REMOVE_METHODS (HA 2026.11).
ADD_REMOVE_METHODS = ("async_prepare_to_add_to_hass", "async_added_to_hass", "async_will_remove_from_hass")


def _requires_readd(entity_class: type) -> bool:
    """Copy of HA 2026.11's _entity_class_requires_readd (the test env may run an older HA)."""
    mro = [cls for cls in entity_class.__mro__ if not cls.__module__.startswith("homeassistant.")]
    hook_owners = [cls for cls in mro if "async_entity_id_changed" in cls.__dict__]
    return any(
        any(method in cls.__dict__ for method in ADD_REMOVE_METHODS)
        and not any(issubclass(owner, cls) for owner in hook_owners)
        for cls in mro
    )


def _kore_entity_classes() -> list[type]:
    for module in pkgutil.iter_modules(kore.__path__):
        if not module.name.startswith("pykoplenti"):
            importlib.import_module(f"{kore.__name__}.{module.name}")
    found: list[type] = []
    pending = [Entity]
    while pending:
        cls = pending.pop()
        for sub in cls.__subclasses__():
            pending.append(sub)
            if sub.__module__.startswith(kore.__name__) and sub not in found:
                found.append(sub)
    return found


def test_every_kore_entity_class_is_renamed_in_place() -> None:
    classes = _kore_entity_classes()
    assert len(classes) > 20
    assert sorted(c.__qualname__ for c in classes if _requires_readd(c)) == []


def test_the_hook_calls_the_base_class() -> None:
    called: list[str] = []

    class Base:
        def async_entity_id_changed(self, old_entity_id: str) -> None:
            called.append(old_entity_id)

    hooked = [cls for cls in _kore_entity_classes() if "async_entity_id_changed" in cls.__dict__]
    assert len(hooked) >= 13
    for cls in hooked:
        # Without the hook in Entity (HA before 2026.11) it is a no-op.
        cls.async_entity_id_changed(object.__new__(cls), "sensor.old")
        if hasattr(Entity, "async_entity_id_changed"):
            continue  # Entity's own hook ends the chain before Base.
        called.clear()
        probe = type("Probe", (cls, Base), {})
        probe.async_entity_id_changed(object.__new__(probe), "sensor.old")
        assert called == ["sensor.old"], cls.__qualname__
