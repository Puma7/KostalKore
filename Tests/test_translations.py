"""Entity names are translated, entity IDs stay English, all languages are complete."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.sensor import SensorEntity, SensorEntityDescription

from custom_components.kostal_kore import naming

ROOT = Path(__file__).resolve().parents[1]
TRANSLATIONS = ROOT / "custom_components" / "kostal_kore" / "translations"
PLACEHOLDER = re.compile(r"\{[a-z0-9_]+\}")


def _flatten(data: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(_flatten(value, path))
        else:
            out[path] = value
    return out


ENGLISH = _flatten(json.loads((TRANSLATIONS / "en.json").read_text(encoding="utf-8")))
LANGUAGES = sorted(p.stem for p in TRANSLATIONS.glob("*.json") if p.stem != "en")


def test_german_is_available() -> None:
    assert "de" in LANGUAGES


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_language_has_every_string_with_the_same_placeholders(language: str) -> None:
    other = _flatten(json.loads((TRANSLATIONS / f"{language}.json").read_text(encoding="utf-8")))
    assert sorted(set(ENGLISH) - set(other)) == []
    assert sorted(set(other) - set(ENGLISH)) == []
    wrong = [key for key, text in ENGLISH.items()
             if sorted(PLACEHOLDER.findall(text)) != sorted(PLACEHOLDER.findall(other[key]))]
    assert wrong == []
    assert [key for key, text in other.items() if not text.strip()] == []


def test_english_translations_match_the_names_in_the_code() -> None:
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "sync_translations.py")],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_name_key() -> None:
    assert naming.name_key("Battery Max Charge Limit (Modbus)") == "battery_max_charge_limit_modbus"
    assert naming.name_key("Power Factor (cos φ)") == "power_factor_cos"


ENGLISH_NAMES = {"sensor": {"battery_power": "Battery Power", "inverter_state": "Inverter State"}}


class _Sensor(SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, name: str | None = None, *, device: str | None = "Kostal",
                 description: SensorEntityDescription | None = None) -> None:
        if name is not None:
            self._english_name = name
        if description is not None:
            self.entity_description = description
        self._attr_device_info = {"identifiers": {("kostal_kore", "x")}, "name": device} if device else None


def test_known_name_is_translated_and_keeps_its_english_id() -> None:
    entity = _Sensor("Battery Power")
    naming.apply_names(entity, "sensor", ENGLISH_NAMES)
    assert entity.translation_key == "battery_power"
    assert not hasattr(entity, "_attr_name")
    assert entity.entity_id == "sensor.kostal_battery_power"


def test_name_matching_another_key_reuses_it() -> None:
    entity = _Sensor(description=SensorEntityDescription(key="x", name="Inverter State"))
    naming.apply_names(entity, "sensor", {"sensor": {"inverter_status": "Inverter State"}})
    assert entity.translation_key == "inverter_status"


def test_explicit_key_is_kept_and_names_the_id_in_english() -> None:
    entity = _Sensor(description=SensorEntityDescription(key="x", name="ignored", translation_key="inverter_state"),
                     device=None)
    naming.apply_names(entity, "sensor", ENGLISH_NAMES)
    assert entity.translation_key == "inverter_state"
    assert entity.entity_id == "sensor.inverter_state"


def test_explicit_key_without_translation_falls_back_to_the_name() -> None:
    entity = _Sensor(description=SensorEntityDescription(key="x", name="Battery Power", translation_key="bat_p"))
    naming.apply_names(entity, "sensor", ENGLISH_NAMES)
    assert entity.translation_key == "battery_power"
    assert entity.entity_id == "sensor.kostal_battery_power"
    unknown = _Sensor(description=SensorEntityDescription(key="y", name="Something New", translation_key="new"))
    naming.apply_names(unknown, "sensor", ENGLISH_NAMES)
    assert unknown.name == "Something New"
    assert unknown.entity_id == "sensor.kostal_something_new"


def test_unknown_name_stays_english_without_translation() -> None:
    entity = _Sensor("Something New")
    naming.apply_names(entity, "sensor", ENGLISH_NAMES)
    assert entity.translation_key is None
    assert entity.name == "Something New"
    assert entity.entity_id == "sensor.kostal_something_new"


def test_registered_or_preset_entity_id_is_not_replaced() -> None:
    entity = _Sensor("Battery Power")
    entity.entity_id = "sensor.my_custom_id"
    naming.apply_names(entity, "sensor", ENGLISH_NAMES)
    assert entity.entity_id == "sensor.my_custom_id"


def test_entity_without_has_entity_name_keeps_its_full_name() -> None:
    entity = _Sensor("Battery Power")
    entity._attr_has_entity_name = False
    naming.apply_names(entity, "sensor", ENGLISH_NAMES)
    assert entity.name == "Battery Power"
    assert entity.entity_id is None
    unnamed = _Sensor()
    unnamed._attr_has_entity_name = False
    naming.apply_names(unnamed, "sensor", ENGLISH_NAMES)
    assert not hasattr(unnamed, "_attr_name")


def test_entity_without_any_name_is_left_alone() -> None:
    entity = _Sensor(device=None)
    naming.apply_names(entity, "sensor", ENGLISH_NAMES)
    assert entity.entity_id is None
    naming.apply_names(SimpleNamespace(), "sensor", ENGLISH_NAMES)  # not an entity: ignored


def test_english_name_ignores_non_string_description_names() -> None:
    entity = _Sensor(description=SensorEntityDescription(key="x"))
    assert naming.english_name(entity) is None


def test_load_english_names_skips_entries_without_a_name(tmp_path: Path) -> None:
    path = tmp_path / "en.json"
    path.write_text(json.dumps({"entity": {"sensor": {"a": {"name": "A"}, "b": {"state": {}}, "c": "x"}}}))
    assert naming.load_english_names(path) == {"sensor": {"a": "A"}}
    assert "Battery Power" in naming.load_english_names()["sensor"].values()


@pytest.mark.asyncio
async def test_translated_adder_names_entities_before_adding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(naming, "_english_names", None)
    hass = SimpleNamespace(async_add_executor_job=AsyncMock(return_value=ENGLISH_NAMES))
    add = MagicMock(return_value="added")
    adder = await naming.async_translated_adder(hass, "sensor", add)
    await naming.async_get_english_names(hass)  # cached: read once
    entity = _Sensor("Battery Power")
    assert adder(iter([entity]), True) == "added"
    add.assert_called_once_with([entity], True)
    assert entity.translation_key == "battery_power"
    hass.async_add_executor_job.assert_awaited_once()


@pytest.mark.usefixtures("mock_plenticore_client")
@pytest.mark.parametrize(("language", "friendly_name"), [
    ("en", "scb Battery Charging / Usage mode"),
    ("de", "scb Lade-/Nutzungsmodus der Batterie"),
])
async def test_names_follow_the_language_and_ids_stay_english(
    hass, mock_config_entry, mock_get_settings, language: str, friendly_name: str,
) -> None:
    from homeassistant.helpers import entity_registry as er
    from pykoplenti import SettingsData

    hass.config.language = language
    mock_get_settings["devices:local"].extend(
        SettingsData(min=None, max=None, default=None, access="readwrite", unit=None,
                     id=setting, type="string")
        for setting in ("Battery:SmartBatteryControl:Enable", "Battery:TimeControl:Enable")
    )
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    entity_id = "select.scb_battery_charging_usage_mode"
    assert er.async_get(hass).async_is_registered(entity_id)
    assert hass.states.get(entity_id).attributes["friendly_name"] == friendly_name

    catalog = naming.load_english_names()
    uncatalogued = [
        (entry.entity_id, entry.translation_key)
        for entry in er.async_entries_for_config_entry(er.async_get(hass), mock_config_entry.entry_id)
        if entry.translation_key not in catalog.get(entry.domain, {})
    ]
    assert not uncatalogued
