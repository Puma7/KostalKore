#!/usr/bin/env python3
"""Collect the English entity names from the code and sync the translation files.

    python scripts/sync_translations.py          # check only (exit 1 on drift)
    python scripts/sync_translations.py --write  # update strings.json + en.json

Entity names are written in English in the code; `naming.py` derives each
entity's translation key from that name when the entity is added. This script
finds every such name statically, so the ``entity`` section of ``strings.json``
and ``translations/en.json`` lists exactly the keys the code can produce. The
``services`` section is copied from ``services.yaml``. Other
languages (``translations/<lang>.json``) are translated by hand; the test suite
checks that they have the same keys and placeholders as English.

Name sources it understands:
- class-level ``_english_name = "..."`` and ``name="..."`` keyword arguments
  inside an entity class (platform from the class's base classes);
- entity description constructors and factories (platform from the callee);
- the module-level name tables listed in ``TABLES`` below;
- names built at run time with f-strings, listed in ``DYNAMIC`` below.
Any other string literal in a name position is reported, so a new pattern cannot
go unnoticed.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "kostal_kore"
sys.path.insert(0, str(ROOT))

PLATFORM_BASES = {
    "BinarySensorEntity": "binary_sensor",
    "SensorEntity": "sensor",
    "RestoreSensor": "sensor",
    "NumberEntity": "number",
    "RestoreNumber": "number",
    "SwitchEntity": "switch",
    "ButtonEntity": "button",
    "SelectEntity": "select",
    "TextEntity": "text",
}
DESCRIPTION_CALLEES = {
    "PlenticoreSensorEntityDescription": "sensor",
    "SensorEntityDescription": "sensor",
    "PlenticoreNumberEntityDescription": "number",
    "create_battery_number_description": "number",
    "create_power_number_description": "number",
    "create_percentage_number_description": "number",
    "PlenticoreSwitchEntityDescription": "switch",
    "SwitchEntityDescription": "switch",
    "create_switch_description": "switch",
    "PlenticoreSelectEntityDescription": "select",
    "PlenticoreEventSensor": "sensor",
}
# Module-level name tables: (file, variable) -> (platform, how to read a name).
TABLES = {
    ("sensor.py", "MODBUS_DIAGNOSTIC_SENSORS"): ("sensor", 1),
    ("sensor.py", "MODBUS_ENERGY_SENSORS"): ("sensor", 1),
    ("sensor.py", "KSEM_DIAGNOSTIC_SENSORS"): ("sensor", 1),
    ("degradation_entities.py", "_PARAM_CONFIG"): ("sensor", "name"),
    ("diagnostic_entities.py", "_AREA_CONFIG"): ("sensor", "name"),
}
# Calls whose ``name=`` is not an entity name.
NOT_ENTITY_CALLEES = {"DeviceInfo", "ParameterTracker", "TrackedParameter", "__init__",
                      "async_track_time_interval", "async_create_background_task",
                      "EventDataUpdateCoordinator"}
DYNAMIC: dict[str, list[str]] = {
    "sensor": [f"DC{n} {metric}" for n in range(1, 7) for metric in ("Power", "Voltage", "Current")]
    + [f"Energy PV{n} {period}" for n in range(1, 7) for period in ("Day", "Month", "Year", "Total")],
    "binary_sensor": [f"Phase {phase} Voltage Warning" for phase in (1, 2, 3)],
    "switch": [f"Shadow Management DC string {n}" for n in range(1, 7)],
}


def name_key(name: str) -> str:
    """Same rule as naming.name_key."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _callee(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else "?"


def _class_platforms(tree: ast.Module) -> dict[str, str]:
    """Map each class in a module to its platform via its (local) base classes."""
    bases = {node.name: [ast.unparse(b).split("[")[0].split(".")[-1] for b in node.bases]
             for node in tree.body if isinstance(node, ast.ClassDef)}
    resolved: dict[str, str] = {}

    def platform(name: str, seen: frozenset[str] = frozenset()) -> str | None:
        if name in PLATFORM_BASES:
            return PLATFORM_BASES[name]
        if name in resolved:
            return resolved[name]
        for base in bases.get(name, []):
            if base not in seen and (found := platform(base, seen | {name})):
                return found
        return None

    for name in bases:
        if found := platform(name):
            resolved[name] = found
    return resolved


def collect() -> tuple[dict[str, set[str]], list[str]]:
    """Return {platform: English names} and the unclassified name literals."""
    names: dict[str, set[str]] = {domain: set(values) for domain, values in DYNAMIC.items()}
    unclassified: list[str] = []

    def add(domain: str, value: str) -> None:
        names.setdefault(domain, set()).add(value)

    for path in sorted(COMPONENT.glob("*.py")):
        if path.name == "naming.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        classes = _class_platforms(tree)
        claimed: set[int] = set()

        for node in tree.body:
            if isinstance(node, ast.Assign | ast.AnnAssign):
                target = node.targets[0] if isinstance(node, ast.Assign) else node.target
                key = (path.name, getattr(target, "id", None))
                if key in TABLES and node.value is not None:
                    domain, field = TABLES[key]
                    for entry in ast.literal_eval(_strip_calls(node.value)):
                        add(domain, entry[field])
                    claimed.update(id(n) for n in ast.walk(node.value))

        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            domain = classes.get(cls.name)
            if domain is None:
                # Not an entity class (coordinators, trackers): its names are not entity names.
                claimed.update(id(n) for n in ast.walk(cls))
                continue
            for node in ast.walk(cls):
                literal = None
                if isinstance(node, ast.Assign) and any(
                    getattr(t, "attr", getattr(t, "id", None)) == "_english_name" for t in node.targets
                ):
                    literal = node.value
                elif isinstance(node, ast.Call) and _callee(node) not in DESCRIPTION_CALLEES:
                    literal = next((kw.value for kw in node.keywords if kw.arg == "name"), None)
                if isinstance(literal, ast.Constant) and isinstance(literal.value, str):
                    claimed.add(id(literal))
                    if domain is not None:
                        add(domain, literal.value)
                    else:
                        unclassified.append(f"{path.name}:{literal.lineno}: {literal.value!r} (class {cls.name})")

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = _callee(node)
            for kw in node.keywords:
                if kw.arg != "name" or id(kw.value) in claimed:
                    continue
                value = kw.value
                if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
                    continue
                if callee in DESCRIPTION_CALLEES:
                    add(DESCRIPTION_CALLEES[callee], value.value)
                elif callee not in NOT_ENTITY_CALLEES:
                    unclassified.append(f"{path.name}:{value.lineno}: {value.value!r} (call {callee})")
            if callee in DESCRIPTION_CALLEES:
                # factories take the name positionally as second argument
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
                    add(DESCRIPTION_CALLEES[callee], node.args[1].value)
    return names, unclassified


def _strip_calls(node: ast.expr) -> ast.expr:
    """Replace calls/attributes (units, enums) by None so literal_eval works."""

    class Strip(ast.NodeTransformer):
        def visit_Call(self, node: ast.Call) -> ast.AST:  # noqa: N802
            return ast.Constant(None)

        def visit_Attribute(self, node: ast.Attribute) -> ast.AST:  # noqa: N802
            return ast.Constant(None)

        def visit_Name(self, node: ast.Name) -> ast.AST:  # noqa: N802
            return ast.Constant(None)

    return ast.fix_missing_locations(Strip().visit(ast.parse(ast.unparse(node), mode="eval"))).body


def entity_section(existing: dict[str, dict[str, dict[str, str]]]) -> dict[str, dict[str, dict[str, str]]]:
    """Existing explicit keys plus one generated key per English name."""
    names, _ = collect()
    section = {domain: dict(entries) for domain, entries in existing.items()}
    for domain, values in names.items():
        entries = section.setdefault(domain, {})
        explicit = {entry.get("name") for entry in entries.values()}
        for value in sorted(values):
            key = name_key(value)
            if key in entries and entries[key].get("name") != value:
                raise SystemExit(f"{domain}.{key}: '{value}' collides with '{entries[key]['name']}'")
            if key not in entries and value not in explicit:
                entries[key] = {"name": value}
    return {domain: dict(sorted(entries.items())) for domain, entries in sorted(section.items())}


def services_section() -> dict[str, dict[str, object]]:
    """English service texts, taken from services.yaml (the single English source)."""
    services = yaml.safe_load((COMPONENT / "services.yaml").read_text(encoding="utf-8"))
    section: dict[str, dict[str, object]] = {}
    for service, spec in services.items():
        entry: dict[str, object] = {"name": spec["name"], "description": spec["description"].strip()}
        fields = {field: {"name": f["name"], "description": f["description"].strip()}
                  for field, f in (spec.get("fields") or {}).items()}
        if fields:
            entry["fields"] = fields
        section[service] = entry
    return section


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="update strings.json and translations/en.json")
    args = parser.parse_args()
    _, unclassified = collect()
    if unclassified:
        print("Unclassified name literals (add them to the tables in this script):", *unclassified, sep="\n  ")
        return 1
    en_path = COMPONENT / "translations" / "en.json"
    section = entity_section(json.loads(en_path.read_text(encoding="utf-8")).get("entity", {}))
    # strings.json keeps its [%key:...%] references; only the entity section is synced.
    texts = {}
    for target in (COMPONENT / "strings.json", en_path):
        raw = target.read_text(encoding="utf-8")
        data = json.loads(raw)
        data["entity"] = section
        data["services"] = services_section()
        indent = len(raw.split("\n", 2)[1]) - len(raw.split("\n", 2)[1].lstrip())  # keep each file's style
        texts[target] = json.dumps(data, indent=indent or 2, ensure_ascii=False) + "\n"
    if args.write:
        for target, text in texts.items():
            target.write_text(text, encoding="utf-8")
        return 0
    stale = [str(t.relative_to(ROOT)) for t, text in texts.items() if t.read_text(encoding="utf-8") != text]
    if stale:
        print("Out of date, run with --write:", *stale)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
