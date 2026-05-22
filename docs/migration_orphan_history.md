# Orphan-History-Migration

> Gesamtübersicht aller Migrationswege (Buttons, Services, Profile): [MIGRATION_LEITFADEN.md](MIGRATION_LEITFADEN.md) · [MIGRATION_COMPLETE_EN.md](MIGRATION_COMPLETE_EN.md)

## Wann brauche ich das?

Du brauchst dieses Werkzeug, wenn:

- Du **kostal_kore seit Monaten oder Jahren** verwendest, ohne je die Legacy-Migration zu durchlaufen.
- Deine Dashboards die neuen `sensor.*`-Entitäten von kostal_kore zeigen, aber **History-Grafen Lücken oder leere Vergangenheit** haben.
- In deiner Recorder-Datenbank stehen alte `sensor.kostal_plenticore_*`-Zeilen, die niemand mehr abruft.

Wenn du gerade frisch von `kostal_plenticore` migriert hast, brauchst du diese Services **nicht** — verwende stattdessen den Standard-Migrationspfad (Button auf der Geräteseite + `adopt_legacy_entity_ids`-Service).

## Was passiert dabei (kurz)

Es werden **keine Rohdaten gelöscht**. Die alten `StatesMeta`- und `StatisticsMeta`-Zeilen werden auf die aktuellen kostal_kore-Entitäten umgehängt. Bei Konflikten (z.B. unterschiedliche Einheiten oder doppelte Statistik-Quellen) wird der jeweilige Merge übersprungen statt überschrieben.

## Schritt 1 — Backup

Sichere deine Home-Assistant-Datenbank vor dem Apply. Settings → System → Backups → "Voll-Backup".

## Schritt 2 — Scan (read-only, immer sicher)

Developer Tools → Services:

```yaml
service: kostal_kore.scan_orphan_history
```

Es erscheint eine persistente Benachrichtigung mit einer Tabelle:

| Legacy entity_id | States | Stats | Suggested target | Similarity |
| --- | :---: | :---: | --- | :---: |
| `sensor.kostal_plenticore_pv_power` | ✓ | ✓ | `sensor.kore_pv_power` | 1.00 |
| `sensor.kostal_plenticore_battery_soc` | · | ✓ | `sensor.kore_battery_soc` | 1.00 |

Prüfe die Vorschläge. `Similarity = 1.00` bedeutet einen exakten Suffix-Match; Werte unter `0.85` solltest du manuell verifizieren.

## Schritt 3 — Dry-Run

Stelle dein Mapping zusammen und rufe den Apply-Service mit `dry_run: true` auf:

```yaml
service: kostal_kore.apply_orphan_history_mapping
data:
  dry_run: true
  mapping:
    sensor.kostal_plenticore_pv_power: sensor.kore_pv_power
    sensor.kostal_plenticore_battery_soc: sensor.kore_battery_soc
```

Eine zweite persistente Benachrichtigung zeigt dir, wie viele Zeilen verschoben würden und welche Mappings übersprungen werden (z.B. weil das Ziel keine kostal_kore-Entity ist oder die Einheiten nicht passen).

## Schritt 4 — Apply

Wenn der Dry-Run plausibel aussieht:

```yaml
service: kostal_kore.apply_orphan_history_mapping
data:
  dry_run: false
  mapping:
    sensor.kostal_plenticore_pv_power: sensor.kore_pv_power
```

Die Operation läuft in einer Recorder-Transaktion — bei Fehlern wird zurückgerollt.

## Was wird abgelehnt

- **Ziel ist keine kostal_kore-Entity** — verhindert, dass du Plenticore-History auf einen Fremd-Sensor mergst.
- **Quelle hat kein Legacy-Muster** — Schutz vor Tippfehlern, die zufällig auf eine fremde Entity zeigen.
- **Einheit alt ≠ Einheit neu** — der bestehende Migrations-Guard (QA-2) übernimmt; betroffene Mappings werden mit Warnung im Log übersprungen.
- **Recorder nicht aktiv** oder **nicht-unterstütztes Backend** — der Service bricht früh ab.

## Backend-Kompatibilität

Funktioniert auf SQLite, MariaDB und PostgreSQL. Andere Recorder-Backends werden abgelehnt.

## Bekannte Grenzen

- Die Fuzzy-Match-Heuristik (`difflib`-Suffix-Vergleich) ist gut für die offensichtlichen Fälle (`pv_power`, `battery_soc`, `home_grid_p`), aber nicht für komplett umbenannte Entitäten. Im Zweifel manuell mappen.
- Die Mapping-Validierung verlangt, dass das Ziel im Entity Registry der `kostal_kore`-Plattform existiert. Wenn du das Ziel gerade deaktiviert hast, aktiviere es kurz für den Merge.
- Wiederholtes Ausführen des Apply-Services ist idempotent — beim zweiten Lauf sind keine Quell-Rows mehr da, der Merge wird übersprungen.
