# AI Context & Documentation - Kostal Inverter HA

## 1. Project Overview
**Name**: Kostal Plenticore Solar Inverter Integration
**Type**: Custom Home Assistant Integration
**Working Directory**: `kostal_plenticore` (Root of the integration package)
**Core Library**: `pykoplenti` (v1.5.0rc1)

This integration allows Home Assistant to monitor and control Kostal Plenticore inverters via their local network API (HTTP/Modbus-TCP wrapper). It is designed to be robust, safe, and efficient, using Home Assistant's `DataUpdateCoordinator` pattern.

## 2. Architecture & Data Flow

High-level data flow from the Inverter to Home Assistant entities:

```mermaid
graph TD
    Inverter[Kostal Inverter] <-->|HTTP/Modbus TCP| PyKo[pykoplenti Library]
    PyKo <-->|ExtendedApiClient| Plenticore[Plenticore Class (coordinator.py)]
    
    subgraph Coordinators
    Plenticore -->|Process Data (10s)| ProcCoord[ProcessDataUpdateCoordinator]
    Plenticore -->|Settings (30s)| SetCoord[SettingDataUpdateCoordinator]
    Plenticore -->|Settings (30s)| SelCoord[SelectDataUpdateCoordinator]
    end
    
    subgraph Entity Platforms
    ProcCoord -->|Updates| Sensors[sensor.py]
    SetCoord -->|Updates| Numbers[number.py]
    SetCoord -->|Updates| Switches[switch.py]
    SelCoord -->|Updates| Selects[select.py]
    end
    
    Sensors -->|State| HA[Home Assistant State Machine]
    Numbers -->|State| HA
```

### Key Components

1.  **`coordinator.py`**:
    *   **`Plenticore`**: Central class managing the API client (`ExtendedApiClient`), authentication (login/logout), and device metadata.
    *   **`ProcessDataUpdateCoordinator`**: Polls read-only "Process Data" (Power, Voltage, Current) every 10 seconds.
    *   **`SettingDataUpdateCoordinator`**: Polls read/write "Settings" (Battery limits, Min SoC) every 30 seconds.
    *   **Error Handling**: Automatically parses `ApiException` into specific Modbus errors (e.g., `ModbusServerDeviceBusyError`).

2.  **`sensor.py`**:
    *   Defines entities via `PlenticoreSensorEntityDescription`.
    *   Maps `module_id` (e.g., `devices:local:pv1`) and `data_id` (e.g., `P`) to HA sensors.
    *   Uses `helper.py` formatters to normalize data (e.g., `format_round` for Watts).
    *   Calculated efficiency sensors are in `_calc_` and use EnergyFlow statistics.

3.  **`helper.py`**:
    *   **`PlenticoreDataFormatter`**: Static methods to convert API strings to Python types (int, float) and human-readable strings (State Codes -> Text).
    *   **`get_hostname_id`**: Utilities for network discovery.
    *   **`parse_modbus_exception`**: Central MODBUS error parsing.
    *   **`ensure_installer_access`**: Shared installer code validation for control writes.

4.  **`const_ids.py`**:
    *   Centralized identifiers for module IDs and common setting IDs.

4.  **`config_flow.py`**:
    *   Handles the setup UI: Host IP, Password, and optional Service Code.
    *   Validates connection before creating the standard config entry.

## 3. Directory Structure & File map

```text
kostal_plenticore/
├── __init__.py           # Integration entry point. Sets up the Plenticore instance.
├── manifest.json         # Metadata (version, dependencies, codeowners).
├── config_flow.py        # UI logic for adding the integration.
├── const.py              # Constants (DOMAIN="kostal_plenticore").
├── coordinator.py        # CORE LOGIC: API Client & Data Coordinators.
├── helper.py             # UTILS: Data formatting & type conversion.
├── sensor.py             # Read-only entities (Process Data).
├── number.py             # Writable numeric entities (Settings).
├── select.py             # Writable choice entities (Settings).
├── switch.py             # Writable boolean entities (Settings).
├── diagnostics.py        # Logic for downloading debug data.
└── strings.json          # Translation strings for UI.
```

## 4. Development Patterns & Guidelines

### A. Adding a New Sensor
To add a new read-only metric:
1.  **Identify** the `module_id` and `data_id` from the Kostal API (use `diagnostics.py` output to find these).
2.  **Edit `sensor.py`**: Add a new `PlenticoreSensorEntityDescription` to the `SENSOR_PROCESS_DATA` list.
    ```python
    PlenticoreSensorEntityDescription(
        module_id="devices:local",
        key="New_Data_ID",
        name="New Sensor Name",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        formatter="format_round",
    )
    ```

### B. Adding a New Control (Number)
To add a writable setting:
1.  **Identify** the setting in the API.
2.  **Edit `number.py`**: Add to `NUMBER_SETTINGS_DATA`.
    ```python
    PlenticoreNumberEntityDescription(
        module_id="devices:local",
        data_id="Battery:MinSoc",
        key="battery_min_soc",
        name="Battery Min SoC",
        fmt_from="format_round",      # API -> HA
        fmt_to="format_round_back",   # HA -> API
    )
    ```

### C. Safety & Error Handling
*   **Authentication**: The integration handles login/logout automatically. Do not manually instantiate `ExtendedApiClient` outside of the provided structures.
*   **Modbus Errors**: Catch `ApiException` and use `parse_modbus_exception`.
*   **Service Code**: Use `ensure_installer_access(...)` before writes that require installer access.

## 5. Critical Technical Constraints
*   **Polling Interval**: Logic is sensitive to polling frequency. Do not decrease intervals below 10s to avoid overwhelming the inverter's single-threaded web server.
*   **Library**: Relies strictly on `pykoplenti`. Any changes to low-level communication must be done in that library, not here.
*   **Async**: All I/O is asynchronous. Use `await` for all client calls.

## 6. Known Issues / Gotchas
*   **API 500 Errors**: Some models return 500 for supported but inactive features. The code logs warning/info and continues.
*   **Battery Wakeup**: Write operations might fail if the battery is in deep sleep. The integration retries standard Modbus busy errors.
*   **Legacy Unique IDs**: Older select entities used `entry_id + module_id`. A registry migration now remaps to `entry_id + module_id + key` to avoid duplicate/grey entities.
*   **Legacy Battery IDs**: Some firmware exposes `Battery:MinSoc` and `Battery:MinHomeComsumption` (typo). The integration auto-falls back to those IDs.

---
*Last Updated: 2026-01-17*
