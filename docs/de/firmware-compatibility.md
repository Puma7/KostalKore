# Firmware-Kompatibilität und Register 1024

[English](../en/firmware-compatibility.md) · **Deutsch**

## Geprüfte Herstellerquellen

- [G3-Release-Notes, 3.07.03.27419](https://www.kostal-solar-electric.com/fileadmin/downloadcenter/kse/Software/PLENTICORE_G3/Release_notes_G3_3.07.03.27419.txt) vom 14.10.2026: behebt, dass externe Batteriebefehle auf Register 1024 ignoriert wurden. Außerdem wird das G3-Batterietyp-Register 588 auf 32 Bit erweitert, wobei das Lesen mit 16 Bit kompatibel bleibt.
- [Modbus-Protokollbeschreibung, Revision 2.9](https://www.kostal-solar-electric.com/fileadmin/downloadcenter/kse/BA/Protokollbeschreibung/BA_KOSTAL_Interface_MODBUS-TCP_SunSpec_with_Control.pdf) vom 17.07.2026, Abschnitte 3.4–3.5: 1024 ist ein vorzeichenbehafteter 16-Bit-AC-Batteriesollwert, der nur geschrieben werden kann (FC06) und mit dem nur lesbaren Register 1025 skaliert wird. Negative Leistung fordert Laden an, positive Leistung fordert Entladen an.

Die Release-Notes nennen eine korrigierte Version, keinen vollständigen Bereich
betroffener Versionen. Schließe daraus nicht, dass jede ältere Generation bzw.
jeder ältere Build denselben Fehler hat.

## Lesen von REST-Einstellungen unter G3 3.07.03.27419

Fehlerberichte zur offiziellen Integration zeigen, dass diese Firmware das Lesen
**einzelner Einstellungen** über REST (`GET /settings/{module}/{id}`, in pykoplenti
`get_setting_values("module", "id")`) mit `404 Module or setting not found`
beantwortet, während die Sammelabfrage (`POST /settings`,
`get_setting_values({"module": [ids]})`) weiter funktioniert. Auch die
Hostname-Einstellung wird als JSON zurückgegeben (`{"Hostname":"scb-5b162a"}` in
#187116) statt als reiner Name. Quellen: home-assistant/core
[#186638](https://github.com/home-assistant/core/issues/186638),
[#187116](https://github.com/home-assistant/core/issues/187116) und
[#187002](https://github.com/home-assistant/core/issues/187002). Kostal hat diese
Änderung nicht dokumentiert; sie wurde deshalb nicht auf KORE-Hardware geprüft.

KORE verwendet daher für jedes Lesen von Einstellungen ausschließlich die Form
`{module: [ids]}` (Verbindungstest bei der Einrichtung, Ermittlung der
String-Anzahl, Verschattungsmanagement, Diagnose, Lesen bei Bedarf) und
dekodiert einen JSON-kodierten Hostnamen. Ein Test weist jeden wieder
eingeführten Aufruf für einzelne Einstellungen zurück. Prozessdaten wurden
bereits in der Sammelform abgefragt.

## Aktuelles Verhalten von KORE und ICN

Weder der Batterieregler von KORE noch ICN verwenden derzeit 1024 zum Laden.
Der Legacy-Deskriptor von KORE blockiert Schreibzugriffe auf diese Adresse
weiterhin über seine Einstufung `Access.RO`; diese Einstufung ist eine
vorsichtige Einschränkung der Implementierung, keine Aussage über das aktuelle
Protokoll des Herstellers. Das Register ist von der normalen Überwachung und von
der Erkennung beschreibbarer Entitäten ausgenommen. Der bestehende Deskriptor
muss überarbeitet werden, bevor ein Schreibpfad für 1024 implementiert wird.

KORE nutzt bestehende Pfade für DC-Befehle bzw. Ladegrenzen wie 1034 und 1038
sowie – sofern konfiguriert – separate zyklische G3-Grenzen. ICN nutzt die in
seiner Konfiguration ausgewählten Entitäten. Register 1024 wird nicht benötigt,
damit diese Pfade weiter funktionieren. Eine Grenze wie 1038 ist nicht dasselbe
wie ein richtungsbezogener Leistungsbefehl.

| Firmware-Situation | Aktuelle Richtlinie der Integration |
|---|---|
| Ältere G3-Firmware | Bestehende unterstützte Steuerpfade beibehalten; keine Abhängigkeit von 1024 einführen. |
| G3 3.07.03.27419 | Die dokumentierte Korrektur für 1024 ist bekannt; der Steuerpfad wird nicht automatisch geändert. REST-Einstellungen werden nur in der Sammelform gelesen (siehe oben). |
| Unbekannte Firmware oder andere Generation | Bestehende Pfade und Prüfungen der beobachteten Rückmeldung beibehalten; nicht annehmen, dass die G3-Korrektur gilt. |

Ein Firmware-Update des Wechselrichters ist daher keine Voraussetzung für die
Korrektur der Wh-Zählerqualität in KORE 3.0.7. Dieser Fehler liegt in der
Größenordnungsprüfung von KORE, nicht in Register 1024 oder in den Energiezählern
des Wechselrichters. Wh-Summen über 10 MWh sind gültig; der begrenzte Spielraum
gilt nur für ENERGY/Wh-Register, nicht für Leistungs- oder
Batteriekapazitätswerte. Nicht endliche, negative und übermäßige Summen bleiben
ungültig und können eine bestätigte Steuerungsrückmeldung nicht erneuern.

## Firmware-Erkennung und künftige Freischaltung

Verwende die Gesamt-`sw_version` des Wechselrichters, gelesen aus
Modbus-Register 58 oder über REST `get_version().sw_version`. Register 586
kennzeichnet die Batterie-Firmware, die Register 38/46 kennzeichnen die
MC/IOC-Komponenten; das sind getrennte Versionsnamensräume, die nicht mit dem
Schwellenwert für die G3-Gesamtversion verglichen werden dürfen.

KORE wertet Major/Minor/Patch bereits über `parse_firmware_version` und
`firmware_at_least` aus. Diese Hilfsfunktionen lassen eine vierte Build-Nummer
bewusst weg. Allein können sie keinen genauen korrigierten Build belegen. Jede
künftige Implementierung für 1024 braucht eine ausdrückliche Entscheidung nach
Generation bzw. Fähigkeit sowie eine Auswertung des vollständigen Builds, wo die
Zusage des Herstellers buildspezifisch ist. Fehlende oder fehlerhafte Versionen
müssen diesen optionalen Pfad deaktiviert lassen und die bestehenden
unterstützten Pfade beibehalten.

Ein künftiger Schreibpfad braucht außerdem die vorzeichenbehaftete Skalierung
aus 1025, die FC06-Kodierung, Bereichs- und Berechtigungsprüfungen, eine
geeignete unabhängige Rückmeldung des Geräts, eine Koexistenz-Arbitrierung sowie
Tests auf betroffener und korrigierter Firmware. Eine Firmware-Nummer beweist
nicht, dass ein Befehl umgesetzt wurde. Die Implementierung oder Auswahl dieses
neuen Pfads ist zurückgestellt, bis diese Anforderungen auf Hardware validiert
wurden.

Der gespeicherte Cache nicht unterstützter Register enthält bereits die
vollständige Gesamt-Firmware-Zeichenkette in seiner Signatur. Bei der
Einrichtung der Integration wird ein Cache mit abweichender Firmware-Signatur
nicht wiederhergestellt. Das ist eine erneute Ermittlung der Fähigkeiten, keine
Erlaubnis, automatisch ein anderes Batteriesteuerungsprotokoll auszuwählen.

Für Register 588 bleibt der bisherige Lesezugriff mit UINT16/count=1 mit dem
dokumentierten alten und neuen Format kompatibel. Ein künftiger erweiterter
Lesezugriff muss auf die geprüfte Generation/Firmware beschränkt und hinsichtlich
Wortanordnung und Modellzuordnung validiert werden; er darf das Lesen bei
älteren Geräten nicht stillschweigend ändern.
