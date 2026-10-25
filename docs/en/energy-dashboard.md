# Energy dashboard

**English** · [Deutsch](../de/energy-dashboard.md)

Use the **Total** sensors (`state_class: total_increasing`) in Home Assistant's
Energy dashboard (**Settings → Dashboards → Energy**). They count up steadily;
Home Assistant computes days, months and years from them. Data can take up to
two hours to appear after the first setup.

## Electricity grid

| Dashboard field | Entity | Source |
|---|---|---|
| Grid consumption | `Total Grid Consumption Total` | EnergyHomeGrid + EnergyChargeGrid |
| Return to grid | `Energy to Grid Total` | Yield − EnergyHomeBat − EnergyHomePv |
| Grid power (optional) | `Grid Power` | Live power (W) |

`Total Grid Consumption Total` covers grid import for the home **and** the
battery. Its battery part is measured on the battery's DC side, so it probably
misses the conversion loss of grid charging (about 5–8 % of the charge energy;
not yet confirmed, see
[HV9](../development/HARDWARE_VALIDATION_TODO.md)). For the AC side, compare with
`Battery AC Charge Energy from Grid Total (Modbus)` (register 1054).

`Energy to Grid Total` contains feed-in from PV **and** from the battery.

## Solar panels

| Dashboard field | Entity | Note |
|---|---|---|
| Solar production, string 1 | `Energy PV1 Total` | One entry per DC string |
| Solar production, string 2 | `Energy PV2 Total` | |
| Solar production, string 3 | `Energy PV3 Total` | Only with a third string |

Do not use `Energy Yield Total` as solar production when a battery is
installed: it counts the inverter's AC yield, which includes energy that came
from the battery, so battery discharge would be counted twice.

## Home battery storage

| Dashboard field | Entity | Source |
|---|---|---|
| Energy going into the battery | `Battery Charge Total Total` | EnergyChargePv + EnergyChargeGrid |
| Energy coming out of the battery | `Battery Discharge Total Total` | EnergyHomeBat + EnergyDischargeGrid |
| Battery power (optional) | `Battery Power` | Live charge/discharge power (W) |

Do not use the Day/Month/Year sensors in the Energy dashboard: they reset every
period, and the dashboard already does that split itself.
