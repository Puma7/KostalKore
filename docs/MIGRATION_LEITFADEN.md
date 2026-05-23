# Migrations-Leitfaden: von `kostal_plenticore` zu `kostal_kore`

Dieser Leitfaden beschreibt **alle** Migrationswege in KOSTAL KORE — von der einfachen Bedienung über die Geräteseite bis zu fortgeschrittenen Services unter **Entwicklerwerkzeuge**. Er richtet sich an Anfänger, Fortgeschrittene und Profis.

**Kurzlinks**

| Dokument | Sprache | Inhalt |
|----------|---------|--------|
| Dieser Leitfaden | Deutsch | Vollständig |
| [MIGRATION_COMPLETE_EN.md](MIGRATION_COMPLETE_EN.md) | English | Full guide |
| [migration_orphan_history.md](migration_orphan_history.md) | Deutsch | Nur Profil „lange auf KORE, nie migriert“ |
| [../migration.md](../migration.md) | English | Kompakte Schritt-für-Schritt-Anleitung |
| [../MIGRATION_ARCHITECTURE.md](../MIGRATION_ARCHITECTURE.md) | English | Technische Grenzen / Architektur (Entwickler) |

---

## 1. Welchen Weg brauche ich?

```
                    ┌─────────────────────────────────────────┐
                    │  Hast du noch kostal_plenticore aktiv?   │
                    └─────────────────┬───────────────────────┘
                                      │
                         JA ─────────┴───────── NEIN
                          │                        │
                          ▼                        ▼
              ┌───────────────────────┐   ┌────────────────────────┐
              │ Profil A: Standard    │   │ Profil B: Orphan-      │
              │ (Buttons oder         │   │ History (nur Services) │
              │  Services)            │   │ → Kap. 6               │
              └───────────┬───────────┘   └────────────────────────┘
                          │
              ┌───────────┴───────────┐
              │ Wie willst du         │
              │ steuern?              │
              └───────────┬───────────┘
                          │
         UI (Anfänger) ───┴─── Services (Profis)
              │                        │
              ▼                        ▼
      Kap. 3: Buttons          Kap. 4: Developer Tools
```

| Profil | Situation | Empfohlener Weg |
|--------|-----------|-----------------|
| **A – Standard** | `kostal_plenticore` ist noch installiert, du wechselst jetzt zu KORE | **Buttons** (Kap. 3) oder gleichwertig Services (Kap. 4.1–4.2) |
| **A – Nacharbeit** | Import lief, aber einzelne Entitäten/History haken | Services `adopt_legacy_entity_ids` + ggf. `copy_legacy_history` (Kap. 4) |
| **B – Orphan** | KORE läuft seit langem, alter Plenticore-Eintrag ist weg, Grafen haben Lücken | **Orphan-Services** (Kap. 6) |
| **Profis / Automatisierung** | Skripte, Node-RED, wiederholbare Dry-Runs | **Immer Services** mit `dry_run: true` zuerst (Kap. 4) |

---

## 2. Übersicht aller Werkzeuge

### 2.1 Geräteseite (Integration → Gerät → Entitäten)

| Entität (Button/Text) | Kategorie | Interne Funktion | Was passiert |
|----------------------|-----------|------------------|--------------|
| **Import Legacy Plenticore Data** | Button | `migrate_legacy_plenticore_entry` | Config/Optionen mergen, Registry umhängen, Legacy-Entry **entladen** (nicht löschen) |
| **Finalize Legacy Cleanup** | Button | `finalize_legacy_cleanup` | Restliche Legacy-Entities/Geräte entfernen, Legacy-**Config-Entry löschen** |
| **Legacy Cleanup Confirmation Code** | Text | (Eingabe für Cleanup) | Code aus Benachrichtigung eintragen (Schritt 1 von 3 beim Cleanup) |

Die Migrations-Buttons erscheinen auf der **KOSTAL-KORE-Geräteseite** unter diagnostischen Entitäten — unabhängig von Modbus.

### 2.2 Developer Tools → Services

| Service | Standard `dry_run` | Bestätigung bei Apply | Interne Funktion |
|---------|-------------------|------------------------|------------------|
| `kostal_kore.adopt_legacy_entity_ids` | `true` | 3 Schritte (Code + `final_confirm`) | Nur Entity-/Device-Registry |
| `kostal_kore.copy_legacy_history` | `true` | 3 Schritte | Recorder-Metadaten (States/Statistics) |
| `kostal_kore.scan_orphan_history` | — (read-only) | keine | Scan Recorder nach Waisen-IDs |
| `kostal_kore.apply_orphan_history_mapping` | `true` | keine (Dry-Run empfohlen) | Orphan-History an KORE binden |

Service-Beschreibungen in der HA-UI stammen aus `custom_components/kostal_kore/services.yaml`.

### 2.3 Wichtig: Button „Import“ ≠ Service `adopt_legacy_entity_ids`

| | **Button: Import Legacy Plenticore Data** | **Service: adopt_legacy_entity_ids** |
|---|------------------------------------------|--------------------------------------|
| **Zielgruppe** | Anfänger, ein Klick (mit Bestätigung) | Profis, Preview, Teil-Reparatur |
| **Config-Entry `data`/`options`** | **Ja**, wird von Legacy übernommen | **Nein** |
| **Entity-Registry** | Ja, inkl. `unique_id`-Rewrite | Ja, gleiche Logik |
| **Device-Registry** | Ja, inkl. KORE-Identifier-Merge | Ja |
| **Legacy-Entry** | Entladen, bleibt in der Liste | Entladen bei Apply |
| **Dry-Run** | Nein (nur Doppelklick innerhalb 60 s) | Ja (`dry_run: true`) |
| **Typischer Einsatz** | Erste Migration | Erneut ausführen, wenn Import schon lief aber Registry klemmt |

**Faustregel:** Erstmigration → **Button Import**. Service `adopt` nur, wenn du bewusst **keine** Config/Optionen nochmal überschreiben willst.

---

## 3. Weg A: Migration über die Geräteseite (Anfänger)

### 3.1 Voraussetzungen

1. **Vollbackup** von Home Assistant (Einstellungen → System → Backups).
2. Alte Integration **`kostal_plenticore`** ist noch als Config-Entry vorhanden (nicht löschen vor Import).
3. Neue Integration **`kostal_kore`** ist angelegt (Setup-Assistent: Host, Passwort, ggf. Service-Code).
4. Beide Einträge können **vorübergehend parallel** existieren.

### 3.2 Schritt 1 — Import Legacy Plenticore Data

**Navigation:** Einstellungen → Geräte & Dienste → KOSTAL KORE → dein Wechselrichter-Gerät → Entitäten → Button **Import Legacy Plenticore Data**.

**Bestätigung (Sicherheit):**

1. **Erster Klick:** Benachrichtigung erscheint — Import innerhalb von **60 Sekunden** erneut bestätigen.
2. **Zweiter Klick** innerhalb 60 s: Migration läuft.

**Was technisch passiert:**

- Legacy-`data`/`options` werden in den KORE-Eintrag gemerged (Host, Passwort, Modbus/MQTT-Optionen, …).
- Alle Legacy-Entities werden auf den KORE-Eintrag umgebunden; `unique_id` wird von `alter_entry_id_*` auf `neuer_entry_id_*` umgeschrieben.
- Doppelte KORE-Entities mit gleicher `unique_id` werden entfernt.
- Am Legacy-Gerät wird ein `kostal_kore`-Identifier ergänzt (verhindert zweite Gerätekarte).
- Der Legacy-Eintrag wird **entladen** (Integration stoppt), aber **nicht gelöscht** — du kannst ihn in der UI wieder aktivieren, falls nötig.
- KORE lädt neu (`async_reload`).

**Nach dem Import prüfen:**

- Persistente Benachrichtigung mit Zählerstand (Entities, Devices, Duplikate entfernt).
- Button-Attribut `last_status` = `ok` (Entwickleransicht).
- Dashboards und Automationen — Entity-IDs können sich geändert haben (`kostal_plenticore_*` → oft gleiche Suffixe unter `kostal_kore` / deinem Namensschema).
- Recorder-Verlauf bleibt für umgebundene Entities in der Regel erhalten (Registry zeigt auf dieselben `entity_id`-Pfade).

**Testphase:** Tage bis Wochen — alte Legacy-Entry bleibt bewusst als Rückfallebene.

### 3.3 Schritt 2 — Finalize Legacy Cleanup

**Erst ausführen, wenn du sicher bist.** Nicht rückgängig machbar ohne Backup.

**Navigation:** Gleiche Geräteseite → **Finalize Legacy Cleanup**.

**Bestätigung (3 Schritte):**

| Schritt | Aktion |
|---------|--------|
| 1 | Button drücken → Benachrichtigung zeigt **Bestätigungscode** (6 Zeichen) |
| 2 | Code in Text-Entität **Legacy Cleanup Confirmation Code** eintragen → Button erneut drücken |
| 3 | Benachrichtigung „Final confirmation“ → Button **innerhalb 60 s** erneut drücken |

Code gültig: **5 Minuten** (Schritt 1–2), finale Bestätigung: **60 Sekunden** (Schritt 3).

**Was passiert:**

- Verbleibende Legacy-Entities in der Registry werden entfernt.
- Legacy-Geräte-Verknüpfungen werden bereinigt; Identifier werden auf `kostal_kore` normalisiert.
- Config-Entry `kostal_plenticore` wird **gelöscht**.
- KORE lädt erneut.

Danach kannst du das alte HACS-Repo/ die alte Custom-Component entfernen.

---

## 4. Weg B: Migration über Services (Fortgeschritten / Profis)

**Navigation:** Entwicklerwerkzeuge → **Services** → Domain `kostal_kore` wählen.

Alle destruktiven Services nutzen standardmäßig **`dry_run: true`**. Für Apply:

1. Aufruf mit `dry_run: false` → Code in Benachrichtigung.
2. Aufruf mit `confirmation_code: "XXXXXX"`.
3. Aufruf mit `confirmation_code` + `final_confirm: true`.

### 4.1 `kostal_kore.adopt_legacy_entity_ids`

**Wann:** Registry-Rebind ohne erneutes Überschreiben der KORE-Config; Dry-Run/Vorschau; wiederholbar.

**Preview:**

```yaml
service: kostal_kore.adopt_legacy_entity_ids
data:
  dry_run: true
  # target_entry_id: optional, wenn nur ein KORE-Eintrag existiert
  # source_entry_id: optional, Pflicht bei mehreren Legacy-Einträgen
```

**Apply (3 Aufrufe):**

```yaml
# 1) Code anfordern
service: kostal_kore.adopt_legacy_entity_ids
data:
  dry_run: false

# 2) Code bestätigen (aus Benachrichtigung)
service: kostal_kore.adopt_legacy_entity_ids
data:
  dry_run: false
  confirmation_code: "AB12CD"

# 3) Ausführen
service: kostal_kore.adopt_legacy_entity_ids
data:
  dry_run: false
  confirmation_code: "AB12CD"
  final_confirm: true
```

### 4.2 `kostal_kore.copy_legacy_history`

**Wann:** Nach erfolgreichem Adopt/Import, wenn **einzelne** Entitäten noch keine History zeigen (alte `entity_id` → neue `entity_id`).

**Nicht blind kopieren** — der Service merged Recorder-**Metadaten** (`StatesMeta`, `StatisticsMeta`), nicht willkürlich alle Rohzeilen. Einheiten-Konflikte werden übersprungen.

**Preview mit Auto-Mapping:**

```yaml
service: kostal_kore.copy_legacy_history
data:
  dry_run: true
  include_auto_map: true
```

**Manuelles Mapping (Beispiel):**

```yaml
service: kostal_kore.copy_legacy_history
data:
  dry_run: true
  entity_map:
    - old_entity_id: sensor.kostal_plenticore_pv_power
      new_entity_id: sensor.kostal_kore_pv_power
```

Apply ebenfalls mit 3-Schritt-Bestätigung wie in 4.1.

### 4.3 Empfohlene Reihenfolge (Profis)

1. Vollbackup.
2. `adopt_legacy_entity_ids` → `dry_run: true`, Ergebnis in Logs/Benachrichtigung prüfen.
3. Adopt apply (3 Service-Aufrufe).
4. Dashboards testen.
5. Nur bei History-Lücken: `copy_legacy_history` → `dry_run: true`, dann apply.
6. Optional: Cleanup per Button **Finalize** (kein separater Cleanup-Service).

---

## 5. Vergleich: Kompletter Ablauf

| Phase | Button-Weg | Service-Weg |
|-------|------------|-------------|
| Erste Bindung + Config | Import-Button (2× Klick) | *Kein direktes Äquivalent* — Import-Button oder manuell Config + `adopt` |
| Registry only | — | `adopt_legacy_entity_ids` |
| History-Lücken | — | `copy_legacy_history` |
| Aufräumen | Finalize-Button (3-stufig) | Finalize-Button (empfohlen) |

---

## 6. Weg C: Orphan-History (Spezialfall)

**Nicht** für frische Plenticore→KORE-Migration.

**Wann:**

- Du nutzt **kostal_kore** seit langem.
- Der alte `kostal_plenticore`-Eintrag existiert **nicht mehr**.
- In der Recorder-DB liegen noch `sensor.kostal_plenticore_*` (oder `wr_`/`wr2_`-Varianten), aber keine Entity-Registry-Verknüpfung.

**Services:**

1. `kostal_kore.scan_orphan_history` — nur lesen.
2. `kostal_kore.apply_orphan_history_mapping` mit `dry_run: true`.
3. Apply mit `dry_run: false`.

Ausführlich: [migration_orphan_history.md](migration_orphan_history.md).

---

## 7. Sicherheits- und Bestätigungsmodell

| Aktion | Mechanismus | Zeitlimit |
|--------|-------------|-----------|
| Import-Button | 2× Button-Druck | 60 s zwischen Klicks |
| Cleanup-Button | Code in Text-Entität + 2× Button | 5 min / 60 s |
| `adopt` / `copy` Services | `confirmation_code` + `final_confirm` | 5 min / 60 s (siehe Benachrichtigung) |
| Orphan-Scan | Keine Schreibzugriffe | — |
| Orphan-Apply | `dry_run` Standard | — |

---

## 8. Fehlerbehebung

| Meldung / Symptom | Ursache | Lösung |
|-------------------|---------|--------|
| `No legacy 'kostal_plenticore' config entry found` | Alter Eintrag gelöscht | Backup restore oder alten Eintrag neu anlegen; ggf. **Orphan-Weg** (Kap. 6) |
| `Multiple legacy entries found` | Mehrere Plenticore-Einträge | `source_entry_id` bei Services setzen; einen Eintrag nach dem anderen migrieren |
| Import: `0 entities migrated` | Legacy nicht geladen / falsches `unique_id`-Format | Legacy-Integration aktivieren; Logs prüfen; ggf. `adopt` mit `dry_run` |
| Zweite Gerätekarte nach Migration | Bekannte Identifier-Thematik | Nach Import nur ein KORE-Gerät nutzen; siehe [MIGRATION_ARCHITECTURE.md](../MIGRATION_ARCHITECTURE.md) |
| History leer trotz Import | Andere `entity_id` als erwartet | `copy_legacy_history` oder Orphan-Weg |
| Cleanup-Code abgelehnt | Tippfehler / abgelaufen | Neuen Zyklus starten (Button erneut) |

---

## 9. FAQ

### Kann ich die alte Integration vor dem Import deaktivieren?

**Nicht empfohlen.** Der Legacy-Eintrag sollte existieren und Entities in der Registry haben.

### Löscht Import den alten Eintrag?

**Nein.** Er wird nur **entladen**. Löschen passiert bei **Finalize Legacy Cleanup**.

### Kann ich nur Services ohne Buttons nutzen?

**Ja**, für Registry: `adopt_legacy_entity_ids`. Für den **vollen** Config-Merge wie beim Import-Button gibt es **keinen** separaten Service — entweder Import-Button oder manuelles Übertragen der Einstellungen + `adopt`.

### Werden Automationen automatisch angepasst?

**Nein.** Entity-IDs können sich ändern. Automationen/Dashboards nach Migration prüfen.

### Parallel `kostal_plenticore` und `kostal_kore`?

Während der Testphase kurz möglich; nach Import ist Legacy **entladen**. Beide gleichzeitig **laden** erhöht Last auf den Wechselrichter — vermeiden.

### Wo finde ich technische Limitierungen?

[MIGRATION_ARCHITECTURE.md](../MIGRATION_ARCHITECTURE.md) (Englisch, für Maintainer und HA-Core-2026.8-Kontext).

---

## 10. Checklisten

### Anfänger (nur Buttons)

- [ ] Vollbackup
- [ ] KORE eingerichtet
- [ ] Import-Button: 1. Klick → 2. Klick innerhalb 60 s
- [ ] Dashboards/Automationen geprüft (Tage/Wochen)
- [ ] Finalize: Code in Text-Entität → 3-stufige Bestätigung
- [ ] Altes Repo/Component optional entfernt

### Profi (Services)

- [ ] Vollbackup
- [ ] `adopt` dry_run → apply (3 Calls)
- [ ] Bei Bedarf `copy_legacy_history` dry_run → apply
- [ ] Finalize-Button für Entry-Löschung
- [ ] Diagnostics/Logs bei 0 Entities archiviert

### Orphan (lange KORE-Nutzer)

- [ ] Vollbackup
- [ ] `scan_orphan_history`
- [ ] Mapping prüfen
- [ ] `apply_orphan_history_mapping` dry_run → apply

---

*Stand: KOSTAL KORE 2.16.x — bei Versionsupdates `services.yaml` und Button-Namen in der Geräteseite gegen diesen Leitfaden prüfen.*
