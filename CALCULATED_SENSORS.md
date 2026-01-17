# New Energy Dashboard Sensors

The Kostal Plenticore integration now includes calculated sensors specifically designed for the Home Assistant Energy Dashboard. These sensors combine raw data points from the inverter to provide clearer insights into total energy flows and system efficiency.

## Overview of New Sensors

The following sensors are automatically created if they don't already exist. They are identified by having `_calc_` in their unique ID.

### 1. Total Grid Consumption (`TotalGridConsumption`)

This sensor calculates the total energy imported from the grid, accounting for both home usage and battery charging.

- **Purpose**: To show the true total energy pulled from the grid.
- **Periods**: Day, Month, Year, Total.
- **Formula**: `Grid to Home + Grid to Battery`
- **Source Data**:
  - `Statistic:EnergyHomeGrid` (Energy consumed by home from grid)
  - `Statistic:EnergyChargeGrid` (Energy used to charge battery from grid)
- **Unit**: kWh
- **State Class**: `total_increasing`

### 2. Battery Discharge Total (`BatteryDischargeTotal`)

This sensor calculates the total energy discharged from the battery, including energy used by the home and any energy fed back into the grid (if applicable/configured).

- **Purpose**: To track the total energy output of the battery system.
- **Periods**: Day, Month, Year, Total.
- **Formula**: `Battery to Home + Battery to Grid`
- **Calculation Detail**:
  - `Battery to Home` = `Statistic:EnergyHomeBat`
  - `Battery to Grid` = `Statistic:EnergyDischarge` (Total Discharge) - `Statistic:EnergyHomeBat`
  - **Result**: `Statistic:EnergyHomeBat + (Statistic:EnergyDischarge - Statistic:EnergyHomeBat)`
- **Source Data**:
  - `Statistic:EnergyHomeBat`
  - `Statistic:EnergyDischarge`
- **Unit**: kWh
- **State Class**: `total_increasing`

### 3. Battery Efficiency (`BatteryEfficiency`)

This sensor calculates the efficiency of the battery system by comparing total energy input vs. total energy output for the given period.

- **Purpose**: To monitor the health and performance of the battery storage system.
- **Periods**: Day, Month, Year, Total.
- **Formula**: `(Total Energy Out / Total Energy In) * 100`
- **Calculation Detail**:
  - `Energy In` = `Statistic:EnergyChargePv` (Charge from PV) + `Statistic:EnergyChargeGrid` (Charge from Grid)
  - `Energy Out` = `BatteryDischargeTotal` (as calculated above)
  - **Result**: `(Energy Out / Energy In) * 100`
- **Source Data**:
  - `Statistic:EnergyChargePv`: **PV to Battery** (strictly charging energy)
  - `Statistic:EnergyChargeGrid`: **Grid to Battery** (strictly charging energy)
  - `Statistic:EnergyHomeBat`: **Battery to Home**
  - `Statistic:EnergyDischarge`: **Total Battery Discharge** (Home + Grid)
- **Unit**: %
- **State Class**: `measurement`

## Usage in Home Assistant

### Energy Dashboard
These sensors are designed to be "plug-and-play" with the Energy Dashboard:
- **Grid Consumption**: Use `sensor.scb_total_grid_consumption_total` (or Day/Month/Year as preferred).
- **Battery Storage**: Use `sensor.scb_battery_discharge_total_total` for the "Energy coming out of the battery" field.

### Manual Verification
You can manually verify these calculations by creating a Template Sensor in Home Assistant or simply adding the raw source sensors to a Lovelace dashboard card and performing the math.

**Example Check (Day):**
1. Note `Statistic:EnergyHomeGrid:Day` value (e.g., 5.0 kWh).
2. Note `Statistic:EnergyChargeGrid:Day` value (e.g., 2.0 kWh).
3. The `TotalGridConsumption:Day` sensor should read 7.0 kWh.

## Troubleshooting

- **"Unavailable" State**: If source data (e.g., `Statistic:EnergyHomeGrid`) is unavailable from the inverter API, the calculated sensor will also show as Unavailable or Unknown.
- **Efficiency > 100%**: The code caps efficiency at 100%. If raw data implies >100% (e.g., due to timing differences in data updates), the sensor will report 100%.

## Limitations (History)

Please note that these are **new entities**. They cannot "backfill" historical data from before they existed. 
- The sensor will start recording from the moment you install this update.
- The **Total** value (e.g., 700 kWh) represents the lifetime counter. Home Assistant will correctly treat this as a "meter reading" and will only calculate **new** usage (increases) from this point forward. It will **not** retrospectively calculate usage for last month.
- This effectively acts like installing a new meter that already has 700 kWh on the dial. Monitoring starts _now_.
