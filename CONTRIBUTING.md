# Contributing

**English** · [Deutsch](#mitmachen)

Bug reports, hardware reports and pull requests are welcome. Reports from
models other than the PLENTICORE G3 are especially useful, because only one G3
is tested on real hardware.

## Reporting a problem

Open an [issue](https://github.com/Puma7/KostalKore/issues) with the diagnostics
download, the model and generation, the inverter firmware, the battery, and
whether Modbus, the proxy or the MQTT bridge are enabled. Remove anything you do
not want to publish; the download already redacts passwords and serial numbers.

## Development setup

There is no build step and no dev server. Everything runs against a mocked
Home Assistant:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements_test.txt
pip install "pykoplenti==1.5.0" "pymodbus>=3.6"
ln -s Tests tests        # pytest.ini expects lowercase "tests" on Linux

python -m pytest Tests/ --timeout=60          # needs 100 % branch coverage
python -m mypy custom_components/kostal_kore/ # strict
python -m ruff check custom_components/kostal_kore Tests
```

CI runs the same on Python 3.12 (the manifest's floor) and 3.14 (current Home
Assistant). For a real Home Assistant with an inverter emulator, see the lab in
[Inverter Charge Night](https://github.com/Puma7/InverterChargeNight/tree/main/lab).

## Pull requests

- Target `main`. Keep a pull request to one topic.
- Add or update tests; the suite enforces 100 % branch coverage of measured
  files.
- Note user-visible changes under `[Unreleased]` in `CHANGELOG.md`.
- Bump `version` in `custom_components/kostal_kore/manifest.json` when the
  change is meant for a release. A release tag must match it (`v<version>`);
  the *Release version check* workflow fails otherwise.
- Do not claim hardware behaviour that was not observed. Open questions go to
  [docs/development/HARDWARE_VALIDATION_TODO.md](docs/development/HARDWARE_VALIDATION_TODO.md).

## Documentation and translations

- User documentation exists in English (`docs/en/`) and German (`docs/de/`)
  with the same file names. Change both, or say in the pull request which one
  still needs the translation.
- `README.md` and `README.de.md` are kept in step the same way.
- Entity names are written in English in the code (`_english_name` or the
  entity description's `name`); `naming.py` turns them into translation keys and
  keeps entity IDs English. After adding or renaming an entity, run
  `python scripts/sync_translations.py --write` to update `strings.json` and
  `translations/en.json` (services come from `services.yaml`), then add the
  German text to `translations/de.json`. The tests fail if a language misses a
  key or a placeholder.
- Some entities carry a fixed `translation_key` (from their description or
  built from the API key). The sync script keeps the text of such a key as it
  is, so when you rename one of these entities, also change its entry in
  `strings.json` and in every `translations/*.json`.
- To add a language, copy `translations/en.json` to `translations/<code>.json`
  and translate the values; keep the keys and the `{placeholders}`.
- Developer notes (`docs/development/`) are English only.

---

## Mitmachen

[English](#contributing) · **Deutsch**

Fehlerberichte, Rückmeldungen zur Hardware und Pull Requests sind willkommen.
Besonders hilfreich sind Berichte von anderen Modellen als dem PLENTICORE G3,
weil nur ein G3 auf echter Hardware getestet wird.

**Problem melden:** Eröffne ein
[Issue](https://github.com/Puma7/KostalKore/issues) mit dem Diagnose-Download,
Modell und Generation, Firmware des Wechselrichters, Batterie und der Angabe, ob
Modbus, Proxy oder MQTT-Bridge aktiv sind. Entferne, was du nicht
veröffentlichen willst; Passwörter und Seriennummern sind im Download bereits
geschwärzt.

**Entwicklung:** Die Befehle oben unter *Development setup* gelten unverändert.
Alles läuft gegen ein nachgebildetes Home Assistant; die Tests verlangen 100 %
Zweigabdeckung, mypy läuft im strikten Modus. Für ein echtes Home Assistant mit
Wechselrichter-Emulator gibt es das Labor von
[Inverter Charge Night](https://github.com/Puma7/InverterChargeNight/tree/main/lab).

**Pull Requests:** gegen `main`, ein Thema pro Pull Request, mit Tests und einem
Eintrag unter `[Unreleased]` im `CHANGELOG.md`. Für ein Release die `version` im
Manifest erhöhen; der Release-Tag muss dazu passen (`v<version>`). Kein
Hardware-Verhalten behaupten, das nicht beobachtet wurde; offene Fragen gehören
in [docs/development/HARDWARE_VALIDATION_TODO.md](docs/development/HARDWARE_VALIDATION_TODO.md).

**Doku und Übersetzungen:** Die Nutzer-Doku gibt es englisch (`docs/en/`) und
deutsch (`docs/de/`) mit gleichen Dateinamen, ebenso `README.md` und
`README.de.md`. Ändere beide oder nenne im Pull Request, welche Übersetzung noch
fehlt. Entitätsnamen stehen englisch im Code; nach dem Anlegen oder Umbenennen
einer Entität `python scripts/sync_translations.py --write` ausführen und den
deutschen Text in `translations/de.json` ergänzen. Hat die Entität einen festen
`translation_key` (aus ihrer Beschreibung oder aus dem API-Schlüssel gebildet),
behält das Sync-Skript dessen Text bei; beim Umbenennen dann den Eintrag in
`strings.json` und in allen `translations/*.json` mit ändern. Für eine neue Sprache
`translations/en.json` nach `translations/<code>.json` kopieren und die Werte
übersetzen; Schlüssel und `{Platzhalter}` bleiben gleich. Die Tests schlagen
fehl, wenn einer Sprache ein Schlüssel oder Platzhalter fehlt.
