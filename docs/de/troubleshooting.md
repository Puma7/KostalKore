# Fehlersuche

[English](../en/troubleshooting.md) · **Deutsch**

Wenn du ein Problem meldest, häng den Diagnose-Download an
([diagnostics.md](diagnostics.md)) und, wenn möglich, ein Debug-Log:

```yaml
logger:
  logs:
    custom_components.kostal_kore: debug
```

## Einrichtung und Verbindung

- **Keine Verbindung:** Prüfe die IP-Adresse des Wechselrichters und ob Home
  Assistant ihn auf Port 80 erreicht. Die Suche bei der Einrichtung ist ein
  Versuch; über VLANs oder Firewalls hinweg findet sie oft nichts. Trag die
  IP-Adresse von Hand ein.
- **Ungültige Anmeldung:** Gemeint ist das Passwort der Weboberfläche des
  Wechselrichters. Der Servicecode ist optional und nur für
  Installateur-Einstellungen nötig.
- **Zeitüberschreitungen:** Der Wechselrichter beantwortet nur wenige Anfragen
  gleichzeitig und ist bei Updates und Wartung beschäftigt. KORE stellt seine
  eigenen Anfragen hintereinander; andere Programme, die den Wechselrichter
  gleichzeitig abfragen, machen Zeitüberschreitungen wahrscheinlicher.

## Einrichtung scheitert mit `cannot import name 'MessageTooLongError' from 'Crypto'`

Die Bibliothek `pycryptodome` in Home Assistant ist beschädigt: Ihre Dateien
stammen aus zwei Versionen (3.x und 4.x). Bis Version 3.0.16 hat sich KORE
darüber angemeldet. Seit 3.0.17 bringt KORE eine eigene Kopie von pykoplenti
mit, die stattdessen `cryptography` nutzt; ein Update von KORE behebt die
Anmeldung also. Andere Integrationen, die `pycryptodome` nutzen (z. B. die
eingebaute Kostal-Integration), bleiben betroffen, bis die Bibliothek sauber neu
installiert ist: unter Home Assistant OS im Terminal-Add-on `ha core rebuild`
ausführen oder im Container
`python3 -m pip install --force-reinstall --no-deps "pycryptodome>=3.19,<4"` und
Home Assistant neu starten (pykoplenti, das die eingebaute Kostal-Integration
nutzt, verlangt eine 3.x-Version).

## Installateur-Zugang erforderlich

Zeigt Home Assistant den Reparaturhinweis *Installer access required*, trag den
Servicecode über **Neu konfigurieren** im Menü des Eintrags ein
(**Konfigurieren** enthält nur die Optionen für Modbus, MQTT, Proxy und KSEM).
Einstellungen, die der Wechselrichter Installateuren vorbehält, bleiben bis
dahin schreibgeschützt.

## Modbus-Regler sind ausgegraut oder schreibgeschützt

- Schreiben braucht Modbus TCP im Wechselrichter und für die Batteriesteuerung
  das Batteriemanagement *Extern über Protokoll (Modbus TCP)*. In anderen Modi
  sind die Batterieregler schreibgeschützt.
- Nach einem unvollständigen Modbus-Abruf markiert KORE die Regler dieser Gruppe
  als nicht verfügbar, bis ein vollständiger Abruf gelingt. So erscheinen alte
  Rücklesewerte nie als aktuelle Werte. Siehe
  [Entitäten-Referenz](entities.md).
- Ein fehlgeschlagener Befehl (`ReadbackUnconfirmedError`) nennt in `outcome`
  den Grund: Die Grenze liest über dem Befehl (z. B. ist *Battery Manual Charge*
  an), das Register ließ sich nicht lesen, oder eine angehobene Grenze liest noch
  den vorigen Befehl. Ein Wert *unter* dem Befehl ist die wirksame Grenze des
  BMS und gilt als angewendet (`limited`).
- Für Kommunikationsprobleme über Proxy oder MQTT siehe
  [communication-failures.md](communication-failures.md).

## Isolationswiderstand zeigt `unknown`

Der Isolationswiderstand kommt aus Modbus-Register 120, nicht über REST.

- `unknown` heißt, dass noch kein gültiger Modbus-Wert gelesen wurde: beim
  Start, nach einer Verbindungsunterbrechung oder weil Modell oder Firmware das
  Register nicht anbieten.
- Während der PV-Produktion meldet der Wechselrichter **65.535.000 Ω** für einen
  Wert oberhalb des Messbereichs (seine Weboberfläche zeigt etwa 65,5 MΩ). KORE
  zeichnet ihn auf und setzt die Attribute `modbus_off_scale_high` und
  `kostal_display_mohm`. Nachts bedeutet derselbe Rohwert „keine Messung“ und
  wird nicht aufgezeichnet.
- Such im Log nach `Connection lost reading isolation_resistance` oder
  `Illegal data address` und prüfe Modbus-Host, Port und Unit-ID.

## Hausverbrauch, Autarkie oder Eigenverbrauch bleiben bei 0

Melden alle Sensoren, die vom Energiezähler abhängen (Hausverbrauch, Autarkie,
Eigenverbrauchsquote, eingespeiste Energie, Zustand des Energiemanagers), 0
oder sind nicht verfügbar, spricht der Wechselrichter sehr wahrscheinlich nicht
mit seinem Energiezähler (KSEM) und arbeitet ohne Verbrauchsdaten. Das ist ein
Verkabelungs- oder Inbetriebnahmeproblem am Wechselrichter; lass die
Zählerverbindung von deinem Installateur prüfen. (Gleiche Ursache wie
home-assistant/core#166779 bei der eingebauten Integration.)

## Recorder-Warnungen zu Energiesensoren

Gelegentlich meldet der Recorder *„Entity … has state class `total_increasing`,
but its state is not strictly increasing“* für Energiesensoren wie *Home
Consumption from Grid Day/Month/Year/Total*. Das ist harmlos: Der
Wechselrichter korrigiert einen Zähler manchmal leicht nach unten (z. B.
5,1 → 5,0 kWh), und Home Assistant verwirft diesen einen Wert. Die
Langzeitstatistik bleibt stimmig. KORE behält `total_increasing` absichtlich,
weil diese Zähler je Zeitraum zurückgesetzt werden und das Energie-Dashboard
genau das richtig behandelt. Ein negativer Energiewert wird für einen Abruf als
nicht verfügbar angezeigt statt als negativer Wert. (Die eingebaute Integration
verhält sich genauso, siehe home-assistant/core#174543.)

## Die Batterie tut etwas, das KORE nicht befohlen hat

Meist schreibt ein anderer Regler dieselben Register: das eigene *Smart AC
Charge* des Wechselrichters, zeit- oder tarifgesteuertes Laden, EEBus, evccs
`batteryMode` oder eine Automation. Nutze einen Regler pro Batterie; siehe
[Batteriesteuerung](../../README.de.md#batteriesteuerung) in der README. KORE
schreibt eine Warnung ins Log, wenn sich die Batterie entgegen seinem Sollwert
bewegt.
