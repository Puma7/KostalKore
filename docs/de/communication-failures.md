# Kommunikationsstörungen und Diagnose

[English](../en/communication-failures.md) · **Deutsch**

Der Proxy liefert aktuelle **Beobachtungen**, getrennt von den Caches für
Entitäten und Befehle. Er verwirft Beobachtungen bei fehlgeschlagenen oder
unvollständigen Abfragen, bei Neuverbindungen und bei Schreibzugriffen. Schnelle
Werte verfallen nach drei Abfrageintervallen (mindestens 15 Sekunden); für
langsame Werte gilt eine sechsmal längere Frist. Bei einem Cache-Fehltreffer ist
ein echtes Lesen beim Upstream erforderlich. Ein Fehler liefert die
Modbus-Exception 04, keine alten Daten und keine erfundene Null. Zu kurze
Antworten werden zurückgewiesen, und jede Anfrage hat eine Bearbeitungsfrist
von 10 Sekunden.

Ein fehlgeschlagener Schreibzugriff hat ein **unbekanntes Ergebnis**: Der
Wechselrichter kann ihn empfangen haben, bevor die Antwort verloren ging. Der
Proxy zählt unsichere Schreibzugriffe und stellt sie nicht zur erneuten
Ausführung in eine Warteschlange. Die bestehende Arbitrierung zwischen
Installateur, SoC-Regler und Registerbesitzer gilt weiterhin. Eine lesbare
Grenze ist kein Nachweis, dass alle externen Batterieregler dieselbe Batterie
gleichzeitig sicher steuern können.
`pending_write_confirmations` zählt die betroffenen Registerwörter: Ein
bestätigter kürzerer Schreibzugriff löscht nur seine eigenen Wörter aus einem
früheren unsicheren FC16-Bereich.

Zyklische G3-Entitätsgrenzen erfordern ein Zurücklesen, bevor ihr Keepalive
startet. Ein neuer Befehl bricht den vorherigen Keepalive ab, bevor er seinen
Ersatz versucht. Fehlgeschlagene Schreibzugriffe, verlorene
Kommunikationsepochen und eine geänderte Zuständigkeit für Modbus-Befehle
beenden die alte Keepalive-Phase, verbunden mit einer sichtbaren
Benachrichtigung. Sie wird nicht automatisch wieder aufgenommen, wenn sich die
Verbindung erholt. Native Ersatzgrenzen der Firmware können trotzdem wirksam
werden; konfiguriere und prüfe sie am tatsächlichen Gerät. Die Auswahl der
bevorzugten Netzquelle schließt außerdem fehlgeschlagene bzw. beibehaltene
Quellen und nicht endliche Werte aus.
Numerische REST-Befehle und die Bestätigung über numerisches Zurücklesen müssen
ebenfalls endlich sein.
Unbekannte Schalterwerte werden als unbekannt angezeigt statt als plausibler
AUS-Zustand.

Bei Fehlern grundlegender REST-Prozess- bzw. Einstellungsabfragen bleiben die
letzten Werte im Anzeige-Cache, aber die betroffenen Prozesssensoren und
Einstellungssteuerungen werden nicht verfügbar. So können Verbraucher wie
Inverter Charge Night beibehaltene Daten von aktueller Telemetrie
unterscheiden. Modbus- und REST-Störungen erzeugen bereichsbezogene dauerhafte
Home-Assistant-Benachrichtigungen und behalten Wiederherstellungsbelege und
einen begrenzten Verlauf. Diese Verläufe bleiben über einen Neustart der
Integration hinweg erhalten. Zähler und Verlauf des Proxys umfassen dessen
aktuelle Laufzeit. Wiederholte Abfragen erzeugen keine zusätzlichen
Benachrichtigungen; an eine andauernde Störung wird täglich erinnert.
Die Wiederherstellung wartet, bis die Plattformerkennung abgeschlossen ist und
jeder aktive REST-Abfragekoordinator einen erfolgreichen Durchlauf
zurückgemeldet hat. Deaktivierte bzw. entfernte Leser werden stillgelegt; späte
Antworten können ihren Zustandskanal nicht wiederbeleben.
Herunterfahren und Neuladen der Integration machen aus einer offenen Störung
keine Wiederherstellungsmeldung, nur weil ihre Leser entfernt werden.

In den Optionen der Integration aktiviert **Expected proxy traffic timeout**
eine Überwachung des gesamten Client-Verkehrs. Der Standardwert 0 deaktiviert
sie: Ein ungenutzter Proxy und ein Client, der TCP-Sitzungen regulär schließt,
sind keine Ausfälle. Wähle eine Zeitüberschreitung, die deutlich länger ist als
das maximale Abfrageintervall der externen Anwendung. Die Überwachung meldet
ausbleibende Anfragen und gilt bei neuen gültigen Anfragen als wiederhergestellt;
sie erkennt keine einzelnen Anwendungen und ändert keine Sollwerte des Geräts.
Nutzen mehrere Clients den Proxy, kann der Verkehr eines Clients das Ausbleiben
eines anderen verdecken.

Verwende `kostal_kore.communication_diagnostics` unter Entwicklerwerkzeuge →
Aktionen mit aktivierter Antwort. Die Aktion liefert die lokalen
REST-/Modbus-Störungsverläufe, die Anzahl der Einträge im Beobachtungs-Cache,
die Proxy-Zähler für Lesezugriffe, Cache, Fehler, Zeitüberschreitungen und
unsichere Schreibzugriffe sowie das Alter der letzten Anfrage. Sie führt keine
Netzwerkanfragen, Dateischreibvorgänge oder Wechselrichterbefehle aus. Derselbe
Zustands-Snapshot ist in heruntergeladenen Integrationsdiagnosen und in
bestehenden Debug-Bundles enthalten. Neue Kommunikationsfelder enthalten
begrenzte Fehlercodes, Zähler und Zeitstempel, ohne Zugangsdaten,
Gegenstellenadressen oder rohen Exception-Text.

Für eine detaillierte Ablaufverfolgung aktiviere vorübergehend die
Debug-Protokollierung für `custom_components.kostal_kore`; reproduziere den
Fehler einmal, deaktiviere sie dann wieder und prüfe den Download, bevor du ihn
weitergibst. Debug-Logs können mehr Gerätedetails enthalten als der begrenzte
Kommunikations-Snapshot.

Diese Softwaremechanismen ersetzen weder den Schutz durch Wechselrichter/BMS
noch Leitungsschutz oder geräteeigene Watchdogs. Sind Home Assistant oder alle
Steuerverbindungen offline, kann der letzte Sollwert von Wechselrichter bzw.
Ladegerät weiter wirken. Home Assistant kann keine Ausfallbenachrichtigung
senden, solange Home Assistant selbst nicht läuft; um diesen Fall zu erkennen,
ist eine unabhängige Überwachung nötig. Das Zeitüberschreitungsverhalten des
Geräts muss an der tatsächlichen Firmware geprüft werden, bevor ein garantierter
physischer Stopp zugesichert wird.

Das zugehörige ICN-Labor prüft das unveränderte offizielle evcc-Binary über
genau diesen Proxy, ein echtes Home Assistant und beide Integrationen gegen
einen Loopback-Wechselrichter. Es ist keine echte elektrische Anlage
angeschlossen.
