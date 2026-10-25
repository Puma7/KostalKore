# Energie-Dashboard

[English](../en/energy-dashboard.md) · **Deutsch**

Nutze im Energie-Dashboard von Home Assistant (**Einstellungen → Dashboards →
Energie**) die **Total**-Sensoren (`state_class: total_increasing`). Sie zählen
stetig hoch; Tage, Monate und Jahre berechnet Home Assistant selbst daraus.
Nach der ersten Einrichtung kann es bis zu zwei Stunden dauern, bis Daten
erscheinen.

## Stromnetz

| Dashboard-Feld | Entität | Quelle |
|---|---|---|
| Netzbezug | `Total Grid Consumption Total` | EnergyHomeGrid + EnergyChargeGrid |
| Netzeinspeisung | `Energy to Grid Total` | Yield − EnergyHomeBat − EnergyHomePv |
| Netzleistung (optional) | `Grid Power` | Aktuelle Leistung (W) |

`Total Grid Consumption Total` enthält den Netzbezug für Haus **und** Batterie.
Der Batterieanteil ist auf der DC-Seite der Batterie gemessen und enthält die
Wandlungsverluste beim Laden aus dem Netz deshalb vermutlich nicht (etwa 5–8 %
der Ladeenergie; noch nicht bestätigt, siehe
[HV9](../development/HARDWARE_VALIDATION_TODO.md)). Für die AC-Seite vergleiche
mit `Battery AC Charge Energy from Grid Total (Modbus)` (Register 1054).

`Energy to Grid Total` enthält Einspeisung aus PV **und** aus der Batterie.

## Solarmodule

| Dashboard-Feld | Entität | Hinweis |
|---|---|---|
| Solarproduktion, String 1 | `Energy PV1 Total` | Ein Eintrag pro DC-String |
| Solarproduktion, String 2 | `Energy PV2 Total` | |
| Solarproduktion, String 3 | `Energy PV3 Total` | Nur mit drittem String |

Nimm `Energy Yield Total` nicht als Solarproduktion, wenn eine Batterie
angeschlossen ist: Der Zähler erfasst den AC-Ertrag des Wechselrichters, der
auch Energie aus der Batterie enthält; die Batterieentladung würde so doppelt
gezählt.

## Batteriespeicher

| Dashboard-Feld | Entität | Quelle |
|---|---|---|
| In die Batterie geladene Energie | `Battery Charge Total Total` | EnergyChargePv + EnergyChargeGrid |
| Aus der Batterie entnommene Energie | `Battery Discharge Total Total` | EnergyHomeBat + EnergyDischargeGrid |
| Batterieleistung (optional) | `Battery Power` | Aktuelle Lade-/Entladeleistung (W) |

Nimm die Tages-, Monats- und Jahressensoren nicht ins Energie-Dashboard: Sie
werden jeden Zeitraum zurückgesetzt, und das Dashboard teilt die Zeiträume
ohnehin selbst auf.
