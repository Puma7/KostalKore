# KOSTAL KORE für Home Assistant

[English](README.md) · **Deutsch**

[![CI](https://github.com/Puma7/KostalKore/actions/workflows/ci.yml/badge.svg)](https://github.com/Puma7/KostalKore/actions/workflows/ci.yml)
[![HACS](https://img.shields.io/badge/HACS-custom-orange.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![Release](https://img.shields.io/github/v/release/Puma7/KostalKore)](https://github.com/Puma7/KostalKore/releases)
[![Lizenz: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Eine benutzerdefinierte Home-Assistant-Integration für Wechselrichter vom Typ
**KOSTAL PLENTICORE**. Sie liest den Wechselrichter über seine lokale REST-API
und optional über Modbus TCP aus. Mit Modbus kann sie außerdem Lade- und
Entladegrenzen der Batterie setzen und die eine Modbus-Verbindung des
Wechselrichters mit anderen Systemen wie evcc teilen.

> **Unabhängiges Projekt.** KOSTAL KORE ist ein Community-Projekt. Es wird von
> der KOSTAL Solar Electric GmbH weder hergestellt noch unterstützt oder
> empfohlen. „KOSTAL“ und „PLENTICORE“ sind Marken ihrer Inhaber und werden hier
> nur verwendet, um die Hardware zu benennen, mit der die Integration spricht.

## Stand: was getestet ist und was nicht

Bitte vor der Installation lesen, besonders bevor du die Batteriesteuerung
einschaltest.

| | |
|---|---|
| **Auf echter Hardware getestet** | Eine Anlage: der PLENTICORE G3 L 20 kW mit Batterie des Maintainers, täglich im Betrieb. |
| **Automatisch getestet** | Rund 1.350 Tests gegen ein nachgebildetes Home Assistant unter Python 3.12 und 3.14, dazu Läufe in einer echten Home-Assistant-Instanz gegen einen Wechselrichter-Emulator (das Labor von [Inverter Charge Night](https://github.com/Puma7/InverterChargeNight)). Beides ersetzt deine Hardware nicht. |
| **Nicht getestet** | PLENTICORE G1/G2, PLENTICORE MP G3, andere Leistungsklassen, andere Batterien und andere Firmware-Versionen. Sie nutzen dieselben Schnittstellen und können funktionieren. Rückmeldungen sind willkommen. |
| **Nicht unterstützt** | Mehrere Wechselrichter in einem Eintrag, Multi Device Control (MDC, ab Firmware 3.06.10) und die Batteriesteuerung von KORE gleichzeitig mit den eigenen Batteriefunktionen des Wechselrichters (siehe [Batteriesteuerung](#batteriesteuerung)). |

Große Teile des Codes sind mit KI-Assistenten (Claude, OpenAI Codex) entstanden
und vom Maintainer geprüft. Noch offene Fragen zum Wechselrichter stehen in
[docs/development/HARDWARE_VALIDATION_TODO.md](docs/development/HARDWARE_VALIDATION_TODO.md)
(englisch).

Die Integration kommt ohne Gewährleistung (MIT-Lizenz). Die Batteriesteuerung
schreibt in deinen Wechselrichter. Prüfe ihre Wirkung an deiner eigenen Anlage.

## Was sie kann

**Überwachung (REST, ohne weitere Einrichtung)**
- PV-, Batterie-, Netz- und Hausleistung; Werte der DC-Strings; Phasenwerte.
- Energiestatistiken pro Tag, Monat, Jahr und gesamt, passend für das
  Energie-Dashboard von Home Assistant ([Anleitung](docs/de/energy-dashboard.md)).
- Zustand und Ereignisse des Wechselrichters sowie die Einstellungen, die er
  über REST anbietet. Ändern lassen sich nur Einstellungen aus einer festen
  Liste; manche brauchen den Installateur-Servicecode.
- Berechnete Sensoren wie *Total Grid Consumption* und Batteriewirkungsgrad.
  Sie sind Summen oder Verhältnisse von Zählern des Wechselrichters und so
  genau wie diese.

**Optional: Modbus TCP**
- Schnellere Abfrage der Leistungswerte und Werte, die REST nicht liefert
  (z. B. den Isolationswiderstand).
- Batteriegrenzen: *Battery Max Charge Limit (Modbus)* (Register 1038),
  *Battery Max Discharge Limit (Modbus)* (1040), die zyklischen G3-Grenzen
  (1280/1282, werden automatisch aufrechterhalten) und der
  Wirkleistungssollwert (533). Schreibvorgänge auf die Grenzen werden
  zurückgelesen; hat der Wechselrichter eine Grenze nicht übernommen, gibt es
  einen Fehler statt einer Erfolgsmeldung. Register 533 ist nur beschreibbar:
  Seine Entität zeigt den letzten Befehl, den der Wechselrichter nicht bestätigt.
- Eingebaute Regler auf Basis dieser Grenzen: ein Regler für einen Ziel-SoC der
  Batterie, *Block Battery Charging* und der *Grid Feed-In Optimizer*.
- Ein **Modbus-Proxy**, über den evcc oder ein anderer Client die eine
  Verbindung von KORE zum Wechselrichter mitnutzen kann, und eine
  **MQTT-Bridge**, die Werte veröffentlicht und ausgewählte Befehle annimmt
  ([Anleitung](docs/de/modbus-proxy-mqtt.md)).
- Ein KOSTAL Smart Energy Meter (KSEM) als zusätzliche, eigene Modbus-Quelle.

**Diagnose**
- Diagnose-Download von Home Assistant, Kommunikationszustand je Kanal und
  Reparaturhinweise, etwa bei fehlendem Installateur-Zugang
  ([Anleitung](docs/de/diagnostics.md)).
- Sensoren für Zustand, Alterung und Batterie-SoH. Das sind Schätzungen aus den
  Daten des Wechselrichters, keine Messungen.
- Sicherheitswarnungen (Isolationswiderstand, Temperaturen, DC-Strings). Das
  sind Software-Heuristiken auf den Daten des Wechselrichters. Sie sind **keine**
  zertifizierte Schutzeinrichtung und ersetzen weder Lichtbogenschutz noch
  Fehlerstromschutzschalter noch Rauchmelder.

**Umzug** von der in Home Assistant eingebauten Integration `kostal_plenticore`,
einschließlich Entity-IDs und Verlauf ([Anleitung](docs/de/migration.md)).

## Voraussetzungen

- Home Assistant 2024.12 oder neuer.
- Ein PLENTICORE-Wechselrichter, den Home Assistant im lokalen Netz erreicht
  (HTTP, Port 80), und das Passwort seiner Weboberfläche.
- Optional: der **Servicecode** des Installateurs für Einstellungen, die der
  Wechselrichter Installateuren vorbehält.
- Für Modbus: *Modbus TCP* im Wechselrichter eingeschaltet (Standard: Port 1502,
  Unit-ID 71). Für die Batteriesteuerung muss das Batteriemanagement des
  Wechselrichters auf *Extern über Protokoll (Modbus TCP)* stehen.

## Installation

**HACS (empfohlen)**
1. In HACS oben rechts das Menü öffnen → **Benutzerdefinierte Repositories**.
2. `https://github.com/Puma7/KostalKore` mit dem Typ **Integration** hinzufügen.
3. **KOSTAL KORE** installieren und Home Assistant neu starten.

**Manuell**
1. `custom_components/kostal_kore` nach `<config>/custom_components/kostal_kore`
   kopieren.
2. Home Assistant neu starten.

## Einrichtung

1. **Einstellungen → Geräte & Dienste → Integration hinzufügen → KOSTAL KORE.**
2. Host eintragen (leer lassen, um im lokalen Netz zu suchen), Passwort und,
   falls vorhanden, den Servicecode. Die Suche ist ein Versuch und scheitert oft
   über VLANs oder Firewalls hinweg; die IP-Adresse einzutragen funktioniert
   immer.
3. Im zweiten Schritt wählst du, ob Modbus TCP, die MQTT-Bridge und der
   Modbus-Proxy aktiv sein sollen. Der Assistent prüft die Modbus-Verbindung und
   zeigt die erkannte Zugriffsrolle (Benutzer oder Installateur) und ob
   Schreiben möglich ist.

Später ändert **Konfigurieren** die Optionen für Modbus, MQTT, Proxy und KSEM.
Host, Passwort und Servicecode änderst du mit **Neu konfigurieren** im Menü des
Eintrags.

## Batteriesteuerung

KORE steuert die Batterie über Modbus und stimmt sich nur mit sich selbst und
mit Clients ab, die seinen Proxy nutzen. Mit der Steuerung **im**
Wechselrichter stimmt es sich **nicht** ab. Nutze **einen** Regler pro Batterie:

- Schalte die eigenen Batteriefunktionen des Wechselrichters ab, solange KORE
  die Batterie steuert: *Smart AC Charge* (seit Firmware 3.05 standardmäßig
  an), zeit- oder tarifgesteuertes Laden und die Steuerung über EEBus.
- Lass evccs `batteryMode` (Vorlage `kostal-plenticore-gen2`) oder eigene
  Automationen nicht gleichzeitig die Register 1034/1038/1040 schreiben.
  Solange ein KORE-Regler ein Register belegt, weist KORE konkurrierende
  Schreibzugriffe über seinen Proxy ab.
- Eine aktive Bezugsbegrenzung nach §14a EnWG drosselt das Laden aus dem Netz
  ebenfalls.

Auf einem G3 liest Register 1038 die **wirksame** Grenze zurück, nicht den
letzten Befehl. Das BMS senkt sie, je voller die Batterie wird (1040, je leerer
sie wird), und *Battery Manual Charge* hebt sie auf das Maximum des
Wechselrichters. KORE wertet einen Rücklesewert unter dem Befehl deshalb als
angewendet (`last_command_outcome = limited`). Einzelheiten:
[Entitäten-Referenz](docs/de/entities.md).

## Dokumentation

| Thema | Deutsch | English |
|---|---|---|
| Entitäten, Register und Attribute | [entities.md](docs/de/entities.md) | [entities.md](docs/en/entities.md) |
| Modbus-Proxy und MQTT-Bridge (evcc, ioBroker) | [modbus-proxy-mqtt.md](docs/de/modbus-proxy-mqtt.md) | [modbus-proxy-mqtt.md](docs/en/modbus-proxy-mqtt.md) |
| Energie-Dashboard | [energy-dashboard.md](docs/de/energy-dashboard.md) | [energy-dashboard.md](docs/en/energy-dashboard.md) |
| Fehlersuche | [troubleshooting.md](docs/de/troubleshooting.md) | [troubleshooting.md](docs/en/troubleshooting.md) |
| Diagnose-Download | [diagnostics.md](docs/de/diagnostics.md) | [diagnostics.md](docs/en/diagnostics.md) |
| Kommunikationsfehler | [communication-failures.md](docs/de/communication-failures.md) | [communication-failures.md](docs/en/communication-failures.md) |
| Firmware-Kompatibilität | [firmware-compatibility.md](docs/de/firmware-compatibility.md) | [firmware-compatibility.md](docs/en/firmware-compatibility.md) |
| Umzug von `kostal_plenticore` | [migration.md](docs/de/migration.md) | [migration.md](docs/en/migration.md) |
| Verwaister Recorder-Verlauf | [orphan-history.md](docs/de/orphan-history.md) | [orphan-history.md](docs/en/orphan-history.md) |

Die Dokumente von KOSTAL selbst (Handbücher, Ereignisliste, die
[Modbus-Protokollbeschreibung](https://www.kostal-solar-electric.com/fileadmin/downloadcenter/kse/BA/Protokollbeschreibung/BA_KOSTAL_Interface_MODBUS-TCP_SunSpec_with_Control.pdf))
gibt es auf [kostal-solar-electric.com](https://www.kostal-solar-electric.com/).
Notizen für Entwickler liegen in [docs/development/](docs/development/)
(englisch), Änderungen in [CHANGELOG.md](CHANGELOG.md) (englisch).

## Hilfe

Eröffne ein [Issue](https://github.com/Puma7/KostalKore/issues) und hänge den
Diagnose-Download an. Nenne bitte Modell und Generation (z. B. G3 L 20 kW), die
Firmware des Wechselrichters, die Batterie und ob Modbus aktiv ist. Für
ausführliche Logs:

```yaml
logger:
  logs:
    custom_components.kostal_kore: debug
    pykoplenti: debug
```

## Sprachen

Die Oberfläche gibt es auf Englisch und Deutsch, einschließlich der
Entitätsnamen. Die Entity-IDs bleiben in jeder Sprache englisch, damit
Automationen und diese Doku unverändert passen. Weitere Sprachen sind
willkommen: siehe [CONTRIBUTING.md](CONTRIBUTING.md).

## Mitmachen

Siehe [CONTRIBUTING.md](CONTRIBUTING.md).

## Dank und Lizenz

KORE baut auf [pykoplenti](https://github.com/stegm/pykoplenti) von `@stegm` und
auf der Integration `kostal_plenticore` in Home Assistant Core auf. Es wurde ein
eigenes Projekt, weil Umfang und Architektur zu weit auseinanderliefen, um es
zurückzuführen.

MIT-Lizenz, siehe [LICENSE](LICENSE).
