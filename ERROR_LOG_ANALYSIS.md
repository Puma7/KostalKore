# Fehlerprotokoll-Analyse - Kostal Plenticore Integration

## 🔍 Fehlermeldungen Analyse

### **Fehler 1: Internal Communication Error (503)**

**Log-Eintrag:**
```
Logger: custom_components.kostal_plenticore.coordinator
Quelle: custom_components/kostal_plenticore/coordinator.py:836
Integration: Kostal Plenticore Solar Inverter
Erstmals aufgetreten: 7. Januar 2026 um 23:13:13 (1 Vorkommnis)
Zuletzt protokolliert: 7. Januar 2026 um 23:13:13

Inverter internal communication error (503) fetching settings - retrying later
```

**Ursache im Code:**
```python
# In coordinator.py Zeile 860-868
if "internal communication error" in error_msg.lower() or "[503]" in error_msg:
    # Try to fallback to stale data to avoid entity unavailability
    cached_result = self._request_cache.get_last_known(fetch_key)
    if cached_result is not None:
        _LOGGER.warning("Inverter internal communication error (503) - using stale data for settings")
        return cached_result

    _LOGGER.warning("Inverter internal communication error (503) fetching settings - retrying later")
    raise UpdateFailed(f"Inverter busy/internal error: {error_msg}") from err
```

**Analyse:**
- ✅ **Gute Fehlerbehandlung** implementiert
- ✅ **Fallback zu Cache-Daten** vorhanden
- ✅ **Retry-Logik** für spätere Versuche
- ⚠️ **503 Errors** deuten auf interne Inverter-Kommunikationsprobleme

### **Fehler 2: WR Power Average Ramp Error (503)**

**Log-Eintrag:**
```
Logger: custom_components.kostal_plenticore.switch
Quelle: helpers/update_coordinator.py:461
Integration: Kostal Plenticore Solar Inverter
Erstmals aufgetreten: 7. Januar 2026 um 23:13:13 (1 Vorkommnis)
Zuletzt protokolliert: 7. Januar 2026 um 23:13:13

Error fetching WR Power Average Ramp After Power Reduction Disabled data: Inverter busy/internal error: API Error: Internal communication error ([503] - internal communication error, try again later)
```

**Analyse:**
- ⚠️ **Externe Quelle**: `helpers/update_coordinator.py:461` (nicht in unserer Codebasis)
- ❌ **Keine Fallback-Logik** vorhanden
- ❌ **Keine Cache-Nutzung** für diese Fehler

### **Fehler 3: Settings Not Available (404)**

**Log-Eintrag:**
```
Logger: custom_components.kostal_plenticore.coordinator
Quelle: custom_components/kostal_plenticore/coordinator.py:841
Integration: Kostal Plenticore Solar Inverter
Erstmals aufgetreten: 7. Januar 2026 um 23:13:14 (1 Vorkommnis)
Zuletzt protokolliert: 7. Januar 2026 um 23:13:14

Some settings are not available on this device (404) - this is normal if features are unsupported: Settings Data
```

**Analyse:**
- ✅ **Normales Verhalten** - 404 für nicht unterstützte Features
- ✅ **Informativer Logging** mit Erklärung
- ✅ **Kein Problem** für die Integration

### **Fehler 4: Module or Setting Not Found (404)**

**Log-Eintrag:**
```
Logger: custom_components.kostal_plenticore.number
Quelle: helpers/update_coordinator.py:461
Integration: Kostal Plenticore Solar Inverter
Erstmals aufgetreten: 7. Januar 2026 um 23:13:14 (1 Vorkommnis)
Zuletzt protokolliert: 7. Januar 2026 um 23:13:14

Error fetching Settings Data data: Settings unavailable: API Error: Module or setting not found ([404] - module or setting not found)
```

**Analyse:**
- ⚠️ **Externe Quelle**: `helpers/update_coordinator.py:461`
- ❌ **Keine Fallback-Logik** vorhanden
- ⚠️ **404 Errors** könnten auf fehlende Features hinweisen

## 🔧 Empfehlungen zur Fehlerbehandlung

### **1. Verbesserte Fehlerbehandlung für 503 Errors**

**Current State (Gut):**
```python
# coordinator.py - bereits gut implementiert
if "internal communication error" in error_msg.lower() or "[503]" in error_msg:
    cached_result = self._request_cache.get_last_known(fetch_key)
    if cached_result is not None:
        _LOGGER.warning("Inverter internal communication error (503) - using stale data for settings")
        return cached_result
```

**Empfehlung:**
- ✅ **Bereits implementiert** - keine Änderung nötig

### **2. Externe Fehlerbehandlung verbessern**

**Problem:** Die Fehler von `helpers/update_coordinator.py` haben keine Fallback-Logik.

**Empfehlung:**
```python
# In den jeweiligen Platform-Dateien (switch.py, number.py, sensor.py)
def _handle_external_coordinator_error(err: Exception, operation: str, entity_name: str) -> Any:
    """Handle external coordinator errors with fallback logic."""
    error_msg = str(err)
    
    # Handle 503 errors (internal communication error)
    if "internal communication error" in error_msg.lower() or "[503]" in error_msg:
        _LOGGER.warning("%s: Inverter busy - using stale data if available", entity_name)
        # Try to get cached data from coordinator
        try:
            coordinator = getattr(self, "coordinator", None)
            if coordinator and hasattr(coordinator, "_request_cache"):
                cache_key = f"settings:{operation}"
                cached_result = coordinator._request_cache.get_last_known(cache_key)
                if cached_result is not None:
                    _LOGGER.warning("%s: Using cached data for %s", entity_name, operation)
                    return cached_result.get(operation, {})
        except Exception:
            pass
    
    # Handle 404 errors (module or setting not found)
    elif "[404]" in error_msg or "module or setting not found" in error_msg.lower():
        _LOGGER.info("%s: Feature not available on this device: %s", entity_name, operation)
        return {}
    
    # Handle other errors
    else:
        _LOGGER.error("%s: Unexpected error fetching %s: %s", entity_name, operation, err)
        return {}
```

### **3. Bessere Fehlerkategorisierung**

**Current State:**
- ✅ **503 Errors**: Gute Behandlung mit Cache-Fallback
- ⚠️ **404 Errors**: Normale Behandlung mit Info-Logging
- ❌ **Externe 503/404 Errors**: Keine Fallback-Logik

**Empfehlung:**
- **Zentralisierte Fehlerbehandlung** für alle Platform-Dateien
- **Consistentes Logging** mit Entity-Namen
- **Fallback zu Cache-Daten** wo möglich

## 📋 Zusammenfassung

### **✅ Gute Fehlerbehandlung:**
- **503 Internal Communication Errors**: ✅ Implementiert mit Cache-Fallback
- **404 Feature Not Available**: ✅ Implementiert mit Info-Logging
- **Retry Logic**: ✅ Implementiert für temporäre Fehler

### **⚠️ Verbesserungspotenzial:**
- **Externe Fehler** von `helpers/update_coordinator.py` haben keine Fallback-Logik
- **Inkonsistente Fehlerbehandlung** zwischen verschiedenen Platform-Dateien
- **Keine zentrale Fehlerbehandlung** für externe Coordinator-Fehler

### **🎯 Fazit:**

**Die Fehler sind größtenteils normal und gut behandelt. Die 503-Errors haben bereits eine gute Fallback-Logik mit Cache-Nutzung. Die 404-Errors sind normale Feature-Prüfungen.**

**Die externen Fehler von `helpers/update_coordinator.py` könnten mit einer zentralisierten Fehlerbehandlung verbessert werden, aber das ist optional und nicht kritisch für die Funktionalität.** 🎉
