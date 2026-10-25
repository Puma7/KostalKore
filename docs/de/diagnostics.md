# Diagnoseberichte

[English](../en/diagnostics.md) · **Deutsch**

Nutze bevorzugt den Diagnose-Download der Integration in Home Assistant:
**Einstellungen → Geräte & Dienste → Kostal KORE → Menü des Eintrags →
Diagnosedaten herunterladen**. Ab 3.0.8 enthält er auch `data.runtime`:
zwischengespeicherte Modbus-Werte mit ihrer Wortreihenfolge und
Veraltet-Markierungen sowie für jeden REST-Abfragekanal die angeforderten,
fehlenden und aktuellen Feld-IDs. Werte von Einstellungen und Zugangsdaten sind
in der Kanalübersicht nicht enthalten.

Für diesen Laufzeitabschnitt wird nur der lokale Zustand verwendet. Ein
zwischengespeicherter Wert beweist kein aktuelles erfolgreiches Lesen; prüfe
dazu `slow_poll_stale`, `retained_quality_addresses` und den
Kommunikationszustand. REST-`missing_fields` vergleicht mit dem angezeigten
Cache, der Ersatzwerte enthalten kann; `fresh_fields` und `communication_stale`
unterscheiden diesen Fall. Ist die Veraltet-Markierung null, hat der Kanal seine
erste Aktualisierung noch nicht abgeschlossen.

Für die zusätzliche Zustandsübersicht und das Schreibprotokoll führe aus:

```yaml
action: kostal_kore.export_debug_bundle
data: {}
```

Die Aktion ist auf Administratoren beschränkt. Sie unterstützt optionale
Antwortdaten und gibt `bundles` (Download-URLs) und `errors` zurück. Die
dauerhafte Benachrichtigung nennt nur die Dateinamen: Benachrichtigungen sind für
alle HA-Benutzer sichtbar, und die signierten Links funktionieren ohne Anmeldung;
deshalb stehen sie nur in der Antwort der Aktion (Entwicklerwerkzeuge →
Aktionen, *Response*). Die Dateien werden in `kostal_kore_debug` im
**tatsächlichen HA-Konfigurationsverzeichnis** geschrieben (das nicht `/config`
sein muss) – nicht nach `www`, weil `/local` Dateien ohne Authentifizierung
ausliefert. Downloads laufen über `/api/kostal_kore/debug_bundle/…` und
erfordern einen Administrator; die zurückgegebenen Links sind für den
aufrufenden Benutzer signiert und laufen nach einer Stunde ab. Exportierte
Dateien werden nach 24 Stunden gelöscht, auch nachdem der letzte KORE-Eintrag
entfernt wurde. Die Links werden mit dem Refresh-Token der WebSocket- oder
HTTP-Sitzung des Aufrufers signiert. Eine Aktion, die außerhalb der UI/API
gestartet wird (z. B. durch eine Automatisierung), hat kein solches Token und
gibt keinen Link zurück, sondern nur den Dateinamen (`url: null`): Kopiere die
Datei aus dem oben genannten Verzeichnis oder führe die Aktion unter
Entwicklerwerkzeuge → Aktionen aus.
Bundles, die frühere Versionen nach `www` exportiert haben, bleiben öffentlich
erreichbar, bis du `www/kore_debug_*.json` löschst.

## Batteriegrenzen einordnen

Laut [KOSTAL-Protokollbeschreibung Revision 2.9, Abschnitt 3.4](https://www.kostal-solar-electric.com/fileadmin/downloadcenter/kse/BA/Protokollbeschreibung/BA_KOSTAL_Interface_MODBUS-TCP_SunSpec_with_Control.pdf)
sind die Register **1038 / 1040 absolute Lade- bzw. Entladeleistungsgrenzen in
Watt**. Sie sind weder die aktuelle Batterieleistung noch Prozentwerte noch das
Hardware-Maximum der Batterie (1076 / 1078). KORE zeigt den dekodierten Wert
unverändert an; die Eingabeschrittweite der Number-Entität von 100 W rundet
eingehende Werte nicht. Beide konfigurierbaren FLOAT32-Wortreihenfolgen werden
unterstützt. Dieselbe Wortreihenfolge gilt auch für die Float-Register 1042 /
1044 für Min-/Max-SoC.

Eine kleine gebrochene Grenze kann daher korrekt dargestellt werden; das allein
sagt aber nicht, ob der Wechselrichter sie durchsetzt, ob ein anderer Regler sie
gesetzt hat oder ob das Gerät sie korrekt zurückgegeben hat. Vergleiche einen
aktuellen Diagnose-Snapshot, die WebUI des Wechselrichters, den
Management-Modus und die letzten Steuerschreibzugriffe. Ersetze einen
ungewohnten Wert nicht durch einen geschätzten Standardwert. Eine erfolgreiche
Modbus-Startprüfung oder ein wiederhergestellter Modbus-Kanal belegt nicht, dass
REST einwandfrei funktioniert.

## AC-Werte und separate Steuergrenzen

`AC Power` und `AC L1/L2/L3 Current/Power` verwenden die Daten
`devices:local:ac` des Wechselrichters. Sie beschreiben den AC-Anschluss des
Wechselrichters einschließlich seiner PV-/Batterie-Wandlung, nicht den
Verbrauch des ganzen Hauses. Ein ruhender Wechselrichter kann daher null
Strom/Leistung melden, während das Haus Strom aus dem Netz bezieht.
`Grid Power`, `Home Power` und die Werte von `devices:local:powermeter`
beschreiben andere Messpunkte; vergleiche Snapshots vom selben Zeitpunkt.

`Battery Max Charge/Discharge Power (SoC Ctrl)` sind lokale Einstellungen des
Reglers, anfangs 5.000 W (begrenzt auf die Nennleistung des Wechselrichters).
Sie werden verwendet, wenn dieser Regler läuft, und sind keine Rücklesewerte der
Register 1038/1040 oder G3 1280/1282. Die numerischen Modbus-Entitäten zeigen
die tatsächlich dekodierten Registerbeobachtungen. Ein inaktiver Steuermodus
beweist nicht, dass eine zurückgelesene Grenze durchgesetzt wird.

Eine als null gemeldete Batteriespannung, während die Schnittstelle zwischen
Wechselrichter und Batterie ruht, bedeutet nicht, dass die Zellspannung null
ist. Prüfe Batteriezustand und Verfügbarkeit mit dem Wechselrichter bzw. BMS;
KORE erfindet keine Ersatzspannung für das Batteriepaket.

## PV-Eingänge und REST-Kommunikation

Die Anzahl der Hardware-DC-Eingänge kann einen Batterieanschluss enthalten. Ab
3.0.9 bestimmt ein erfolgreich abgerufener REST-Fähigkeitskatalog, welche
PV-Module und Felder abonniert werden, auch für den PV-Summensensor. Nicht
unterstützte PV-Eingänge werden nicht angefragt. Schlägt die Katalogermittlung
fehl, bleibt der Rückgriff auf die physische Anzahl bestehen; verschwindet ein
angekündigter Eingang während der Abfrage, wird der Kommunikationsfehler
weiterhin gemeldet. Fehlende PV-Daten werden dadurch nicht durch null ersetzt.

## Beibehaltene Isolationsbewertungen

Die Zustandsüberwachung kann einen zuvor aufgezeichneten Isolationsmesswert
beibehalten, auch einen aus dem Speicher wiederhergestellten. Im Standby kann
der aktuelle Modbus-Wert nicht verfügbar sein, während dieser beibehaltene
Messwert weiterhin die Zustandsstufe und die Bewertungszahl beeinflusst.

Ab 3.0.10 stellen `health_summary.isolation_measurement` und die Attribute der
Isolationswarnung `assessment_basis` (`current`, `historical`, `unavailable`),
`sample_origin` (`modbus`, `restored`, `none`), `current_measurement_ohm` und
`last_recorded_ohm` bereit. Der historische Tracker-Wert und die Warnung bleiben
erhalten; `kostal_display_mohm` ist null, wenn kein aktueller Messwert vorliegt.
Ein nicht verfügbarer aktueller Messwert belegt nicht, dass ein früherer
niedriger Wert falsch war. Vergleiche den nächsten gültigen Messwert im Betrieb
mit der eigenen Diagnose des Wechselrichters, bevor du die beibehaltene
Bewertung einordnest.
Fehlende, ungültige und mit beibehaltener Qualität gelieferte
Isolationsantworten markieren den aktuellen Messwert ebenfalls als nicht
verfügbar und behalten die vorherige Bewertung bei.

## Bestätigung von Steuerbefehlen

Ab 3.0.11 erfordert eine lesbare Modbus-Number-Entität eine übereinstimmende,
endliche Rückmeldung des Geräts innerhalb von drei gültigen Leseversuchen
(0,2 Sekunden zwischen voneinander abweichenden numerischen Ergebnissen).
Lesefehler des Clients werden nicht wiederholt und auch nicht sofort erneut
aktualisiert: Der Client übernimmt bereits Wiederholungen auf Transportebene und
die Unterdrückung von Registern. Gleitkomma-Genauigkeit (`rel_tol=1e-6`,
`abs_tol=1e-6`) wird akzeptiert, während null eine exakte Rückmeldung erfordert.
Die Eingabeschrittweite der UI wird nicht als Bestätigungstoleranz verwendet.
Werte mit beibehaltener Qualität oder eine geänderte Verbindungsgeneration
können den Befehl nicht bestätigen. Ein unbestätigter gewöhnlicher Steuerbefehl
aktualisiert die Beobachtungen und löst einen Fehler aus; zyklische
G3-Steuerungen behalten ihren bestehenden Schutz, bei einem Fehler keinen
Keepalive zu senden.
Register 533 ist als nur beschreibbar dokumentiert und hat keine lesbare
Bestätigung.
Eine Transportbestätigung von Modbus bzw. ein `ok` im Protokoll allein ist kein
Nachweis, dass das Laden der Batterie der angeforderten Leistung folgt.
Vergleiche zusätzlich die aktuelle Telemetrie.
Nur lesbare und ungültige Number-Befehle lösen Validierungsfehler aus, ohne zu
schreiben.

Auch die abschließende Aktualisierung des Koordinators muss die ursprüngliche
Verbindungsgeneration und einen funktionierenden, verbundenen Koordinator
beibehalten. Eine Neuverbindung, eine Trennung oder eine fehlgeschlagene
Aktualisierung macht die frühere Bestätigung ungültig und kann keine
G3-Keepalive-Phase starten. Das aktualisierte Register muss weiterhin mit dem
Befehl übereinstimmen und eine aktuelle Qualität haben: Auch ein neuerer
abweichender, fehlender, nicht endlicher oder beibehaltener Wert macht die
Bestätigung ungültig. Der nächste Befehl kann eine neue Bestätigung herstellen.
Bei überwachten Steuerungen wird direkt auf die Abfrage gewartet, ohne die
Entprellung von Anfragen; verwendet wird die aktuelle Beobachtung des einzelnen
Registers, nicht ein zusammengeführter Cache der langsamen Abfrage oder Fehler
nicht zugehöriger Register. Nicht überwachte lesbare Steuerungen verwenden
weiterhin ihr direktes Zurücklesen, und das nur beschreibbare Register 533
behält seine dokumentierte Ausnahme.

## Protokollierung von Sicherheitsereignissen

Ab 3.0.12 erzeugt das Lesen des Zustands, Symbols oder der Attribute von
„PV System Safety“ keine zusätzlichen Warnmeldungen. Die Sicherheitsüberwachung
protokolliert weiterhin neue und verschärfte Warnungen. Risikostufen, unsicherer
Zustand, Schwellenwerte und beibehaltene Warnungen bleiben unverändert. Eine
wiederholt dargestellte Warnung ist keine Folge neuer Ereignisse; nutze bei der
Untersuchung ihre Kategorie, ihren Erstellungszeitpunkt und die aktuellen
Messwerte.

## Kontext der nächtlichen Steuerung (3.0.13)

Sowohl die Standarddiagnose als auch das Debug-Bundle enthalten
`runtime.modbus.device_info_cached`: eine Positivliste zwischengespeicherter
Angaben zu Wechselrichter-Firmware, Produkt/Zustand, Nennleistung und
Batteriemanagement-Modus. Das sind Metadaten aus der Geräteerkennung, keine
aktuelle Bestätigung des Steuermodus. Sie können vor der Erkennung fehlen oder
während eines Verbindungsausfalls beibehalten werden; vergleiche die
Verbindungs-/Aktualitätsmarkierungen und prüfe bei der Untersuchung von
Konflikten den aktuellen Modus in der WebUI des Wechselrichters.

Jeder REST-Kanal stellt `control_settings` für ausgewählte numerische
Batterieeinstellungen bereit, die bereits in seinem Cache vorliegen:
minimaler SoC, manuelles Laden, Strategie, Smart-/Zeitsteuerung und externe
Lade-/Entladegrenzen. Jeder Eintrag hat `value` und `fresh`; ungültige bzw.
nicht endliche Werte sind null, und ein beibehaltener Wert ist keine aktuelle
Bestätigung. Fehlende Einstellungen bedeuten „nicht erfasst“, nicht
„deaktiviert“ oder „null“. Der Export abonniert keine inaktiven Einstellungen
und sendet keine Steuerbefehle. Andere Einstellungen und beliebige
Zeichenkettenwerte werden diesem Kontext nicht hinzugefügt.

Lade nach der Nacht die Integrationsdiagnose, vollständige Debug-Logs,
das zusätzliche Bundle und den HA-Verlauf herunter, bevor du neu startest oder
neu lädst. Das Bundle enthält nur die letzten 100 im Speicher gehaltenen
Modbus-Schreibereignisse, nicht die ganze Nacht. Ein `ok` im Protokoll erfasst
den Schreibvorgang auf Transportebene; das Ergebnis des Zurücklesens im
Number-Service und die tatsächliche AC-/DC-Leistung bleiben gesonderte
Nachweise.
