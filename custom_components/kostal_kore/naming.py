"""Translated entity names with English entity IDs.

Entity names are written in English in the code (``_english_name`` or the
entity description's ``name``). When a platform adds its entities, each one gets
the translation key derived from that name, or keeps an explicit one, so Home
Assistant shows the name in the user's language from ``translations/<lang>.json``.

Home Assistant would also derive the ID of a *new* entity from the translated
name (German IDs on a German system). KORE suggests the English ID instead, so
entity IDs, the documentation and automations stay the same in every language.
Entities already in the registry keep their ID either way.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Final, TypeVar, cast

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.util import slugify

EnglishNames = dict[str, dict[str, str]]
_AdderT = TypeVar("_AdderT", bound=Callable[..., Any])

_TRANSLATIONS_EN: Final = Path(__file__).parent / "translations" / "en.json"
_NON_KEY: Final = re.compile(r"[^a-z0-9]+")
_english_names: EnglishNames | None = None


def name_key(name: str) -> str:
    """Return the translation key for an English entity name."""
    return _NON_KEY.sub("_", name.lower()).strip("_")


def load_english_names(path: Path = _TRANSLATIONS_EN) -> EnglishNames:
    """Read the English entity names per platform from a translation file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        domain: {key: value["name"] for key, value in entries.items()
                 if isinstance(value, dict) and isinstance(value.get("name"), str)}
        for domain, entries in data.get("entity", {}).items()
    }


async def async_get_english_names(hass: HomeAssistant) -> EnglishNames:
    """Return the English entity names, read once per process."""
    global _english_names  # noqa: PLW0603 - immutable file content, cached once
    if _english_names is None:
        _english_names = await hass.async_add_executor_job(load_english_names)
    return _english_names


def english_name(entity: Entity) -> str | None:
    """Return the English name the code gives an entity."""
    name = getattr(entity, "_english_name", None)
    if name is None:
        description = getattr(entity, "entity_description", None)
        name = getattr(description, "name", None)
    return name if isinstance(name, str) else None


def _key_for(known: dict[str, str], name: str) -> str | None:
    """Return the translation key whose English text is ``name``."""
    if known.get(name_key(name)) == name:
        return name_key(name)
    return next((key for key, text in sorted(known.items()) if text == name), None)


def apply_names(entity: Entity, domain: str, english: EnglishNames) -> None:
    """Give an entity its translation key and suggest its English entity ID."""
    if not isinstance(entity, Entity):
        return
    source = english_name(entity)
    if not entity.has_entity_name:
        if source is not None:
            entity._attr_name = source
        return
    known = english.get(domain, {})
    key = getattr(entity, "_attr_translation_key", None)
    if key is None:
        key = getattr(getattr(entity, "entity_description", None), "translation_key", None)
    if key is None and source is not None:
        if (found := _key_for(known, source)) is not None:
            key = found
            entity._attr_translation_key = key
        else:
            # No translation for this name: show the English name as it is.
            entity._attr_name = source
    name = known.get(key) if key is not None else None
    if name is None:
        name = source
    if entity.entity_id is None and name:
        device_info = entity.device_info or {}
        device = device_info.get("name")
        entity.entity_id = f"{domain}.{slugify(f'{device} {name}' if isinstance(device, str) else name)}"


async def async_translated_adder(
    hass: HomeAssistant, domain: str, add_entities: _AdderT,
) -> _AdderT:
    """Wrap a platform's ``async_add_entities`` so every entity is named via `apply_names`."""
    english = await async_get_english_names(hass)

    def add(entities: Iterable[Entity], *args: Any, **kwargs: Any) -> Any:
        entities = list(entities)
        for entity in entities:
            apply_names(entity, domain, english)
        return add_entities(entities, *args, **kwargs)

    return cast(_AdderT, add)
