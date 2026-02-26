# Architektur-Konzept: Perfekte REST/Modbus Parallelisierung

## Status Quo (v2.9.0)

```
┌─────────────────────────────────────────────────────────────┐
│                    Home Assistant                            │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ REST Process │  │ REST Setting │  │ Modbus           │  │
│  │ Coordinator  │  │ Coordinator  │  │ Coordinator      │  │
│  │ (60s wenn MB)│  │ (90s wenn MB)│  │ (5s fast/30s)    │  │
│  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘  │
│         │                 │                    │            │
│         └────────┬────────┘                    │            │
│                  │                             │            │
│            REST API :80                  Modbus TCP :1502   │
│         (HTTP + Auth)                    (kein Auth)        │
│                  │                             │            │
│                  └──────────┬──────────────────┘            │
│                             │                               │
│                     Kostal Inverter                          │
│                   (ein Prozessor für beides)                 │
└─────────────────────────────────────────────────────────────┘

Problem: Zwei unabhängige Koordinatoren → doppelte Daten, 
Timing-Konflikte, Inverter überlastet beim Startup
```

## Ziel-Architektur (v3.0)

```
┌─────────────────────────────────────────────────────────────┐
│                    Home Assistant                            │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │            Unified Data Coordinator                  │    │
│  │                                                     │    │
│  │  ┌─────────────┐     ┌──────────────────────────┐   │    │
│  │  │ REST Client │     │ Modbus Client             │   │    │
│  │  │ (Fallback)  │     │ (Primär)                  │   │    │
│  │  └──────┬──────┘     └────────────┬─────────────┘   │    │
│  │         │                         │                 │    │
│  │  ┌──────┴─────────────────────────┴──────────┐      │    │
│  │  │         Request Scheduler                  │      │    │
│  │  │  - Serialisiert ALLE Anfragen              │      │    │
│  │  │  - Modbus hat Priorität für Echtzeit-Daten │      │    │
│  │  │  - REST nur für exklusive Daten            │      │    │
│  │  │  - Max 1 Request gleichzeitig zum Inverter │      │    │
│  │  └────────────────────┬──────────────────────┘      │    │
│  │                       │                             │    │
│  │              Unified Data Store                      │    │
│  │         (ein dict, eine Wahrheit)                    │    │
│  └───────────────────────┬─────────────────────────────┘    │
│                          │                                   │
│         ┌────────────────┼────────────────┐                  │
│         │                │                │                  │
│    HA Entities      MQTT Bridge     Health/Safety            │
│    (Sensoren,       (evcc,          (Fire Safety,            │
│     Numbers,         iobroker)       Diagnostik)             │
│     Switches)                                                │
└─────────────────────────────────────────────────────────────┘
```

## Kernprinzipien

### 1. Ein Koordinator, eine Wahrheit

Statt 4 separate Coordinators (REST Process, REST Settings Number, 
REST Settings Switch/Select, Modbus) gibt es EINEN `UnifiedCoordinator`:

```python
class UnifiedCoordinator(DataUpdateCoordinator):
    """Einziger Coordinator für alle Datenquellen."""
    
    def __init__(self, hass, modbus_client, rest_client):
        self._modbus = modbus_client  # kann None sein
        self._rest = rest_client
        self._data_store = {}  # eine einzige Wahrheitsquelle
        self._scheduler = RequestScheduler()
    
    async def _async_update_data(self):
        if self._modbus and self._modbus.connected:
            # Modbus primär: schnelle Register
            await self._scheduler.execute(
                self._modbus.read_fast_registers  # Power, Battery, Phases
            )
            # REST sekundär: nur exklusive Daten (Events, Settings-Discovery)
            if self._should_poll_rest():
                await self._scheduler.execute(
                    self._rest.get_exclusive_data  # Events, neue Settings
                )
        else:
            # Kein Modbus: REST wie bisher
            await self._scheduler.execute(
                self._rest.get_all_data
            )
        return self._data_store
```

### 2. Request Scheduler (Serialisierung)

```python
class RequestScheduler:
    """Serialisiert alle Anfragen an den Inverter.
    
    Der Kostal hat nur einen Prozessor. Parallele REST + Modbus
    Anfragen überlasten ihn. Der Scheduler stellt sicher:
    - Max 1 Request gleichzeitig
    - Modbus hat Priorität (Echtzeit-Daten)
    - REST wird in Lücken eingeschoben
    - Mindestpause zwischen Requests (50ms)
    """
    
    async def execute(self, request_fn, priority="normal"):
        async with self._lock:
            await asyncio.sleep(0.05)  # 50ms Pause zwischen Requests
            return await request_fn()
```

### 3. Datenquellen-Mapping

```python
# Welche Daten kommen von wo?
DATA_SOURCE_MAP = {
    # Modbus-exklusiv (kein REST-Equivalent):
    "active_power_setpoint":     Source.MODBUS_ONLY,
    "reactive_power_setpoint":   Source.MODBUS_ONLY,
    "bat_charge_dc_abs_power":   Source.MODBUS_ONLY,
    "io_output_1..4":            Source.MODBUS_ONLY,
    
    # REST-exklusiv (kein Modbus-Equivalent):
    "events":                    Source.REST_ONLY,
    "settings_discovery":        Source.REST_ONLY,
    "diagnostics":               Source.REST_ONLY,
    "shadow_management":         Source.REST_ONLY,
    
    # Beides verfügbar → Modbus bevorzugt:
    "total_dc_power":            Source.PREFER_MODBUS,
    "battery_soc":               Source.PREFER_MODBUS,
    "phase1_voltage":            Source.PREFER_MODBUS,
    "grid_frequency":            Source.PREFER_MODBUS,
    "battery_temperature":       Source.PREFER_MODBUS,
    "isolation_resistance":      Source.PREFER_MODBUS,
    "battery_min_soc":           Source.PREFER_MODBUS,  # Modbus Register 1042
    "battery_min_home_consumption": Source.PREFER_REST,  # REST hat bessere Ranges
}
```

### 4. Entity-Abstraktionsschicht

```python
class UnifiedSensorEntity(SensorEntity):
    """Sensor der automatisch die beste Datenquelle nutzt."""
    
    def __init__(self, coordinator, data_key, ...):
        self._data_key = data_key
    
    @property
    def native_value(self):
        # Coordinator liefert bereits den besten Wert
        return self.coordinator.data.get(self._data_key)
    
    @property
    def extra_state_attributes(self):
        return {
            "source": self.coordinator.get_source(self._data_key),
            # "modbus" oder "rest" → Transparenz für den User
        }
```

### 5. Startup-Sequenz

```
1. REST Login (muss zuerst, für Settings-Discovery)
2. REST: Settings + Module-Liste laden (welche Entities erstellen?)
3. Modbus Connect + Endianness Detect
4. Modbus: Device Info lesen (Serial, Max Power, Battery Type)
5. Entities erstellen (basierend auf REST Discovery + Modbus Device Info)
6. Polling starten (Modbus primär, REST sekundär)

Dauer: ~5s statt ~25s (kein paralleles Blockieren mehr)
```

### 6. Failover-Strategie

```
Normal:    Modbus → Daten → Entities aktuell
Modbus-Ausfall: REST übernimmt automatisch (10s Polling)
REST-Ausfall:   Modbus allein (kein Settings-Update, aber Daten fließen)
Beides aus:     Entities → "unavailable", Reconnect-Loop
```

## Migrationsplan

### Phase 1: Request Scheduler (v3.0-alpha)
- `RequestScheduler` zwischen alle Clients und Inverter schalten
- Serialisiert REST + Modbus Anfragen
- Keine Entity-Änderungen nötig

### Phase 2: Unified Data Store (v3.0-beta)
- Alle Coordinators lesen/schreiben in einen gemeinsamen Store
- Entities lesen aus dem Store statt direkt vom Coordinator
- Datenquellen-Mapping implementieren

### Phase 3: Entity Refactoring (v3.0)
- REST-basierte + Modbus-basierte Entities zusammenführen
- Keine Duplikate mehr (z.B. "Battery SoC" und "Battery SoC (Modbus)")
- Source-Attribut zeigt woher der Wert kommt

### Aufwand-Schätzung
- Phase 1: ~1 Session (Request Scheduler ist isoliert)
- Phase 2: ~2 Sessions (Store-Umbau, alle Coordinators anpassen)
- Phase 3: ~3 Sessions (Entity-Refactoring, Tests, Migration)
- Gesamt: ~6 Sessions für den kompletten Umbau
