# pykoplenti Version in Home Assistant prüfen

## 🔍 Methode 1: Home Assistant Log

**Nach dem Neustart der Integration suchen nach:**

```bash
# In Home Assistant Log suchen nach:
grep -i "pykoplenti" /config/home-assistant.log
```

**Erwartete Ausgabe:**
```
2026-01-07 23:00:00 INFO (MainThread) [homeassistant.components.custom_component] 
Loaded custom component kostal_plenticore with pykoplenti 1.5.0rc1
```

## 🔍 Methode 2: Developer Tools

**In Home Assistant:**
1. **Developer Tools** → **Services**
2. **Service**: `python_script.execute` (falls installiert)
3. **Code**:
```yaml
import pkg_resources
version = pkg_resources.get_distribution("pykoplenti").version
hass.states.set("sensor.pykoplenti_version", version)
```

## 🔍 Methode 3: Terminal Check (Beste Methode)

**Im Home Assistant Container:**
```bash
# In HA Container einloggen
docker exec -it homeassistant bash

# Version prüfen
python3 -c "import pkg_resources; print(pkg_resources.get_distribution('pykoplenti').version)"

# Oder alternativ
pip list | grep pykoplenti
```

## 🔍 Methode 4: Integration Log

**In der Integration selbst prüfen:**
Füge diesen Code temporär zu `sensor.py` hinzu:

```python
# Am Anfang von async_setup_entry
import pkg_resources
try:
    version = pkg_resources.get_distribution("pykoplenti").version
    _LOGGER.info("Using pykoplenti version: %s", version)
except Exception as e:
    _LOGGER.error("Could not determine pykoplenti version: %s", e)
```

## 🔍 Methode 5: File System Check

**Im Home Assistant config Verzeichnis:**
```bash
# Dependencies prüfen
ls -la /config/deps/python3.12/lib/python3.12/site-packages/ | grep pykoplenti

# Version auslesen
cat /config/deps/python3.12/lib/python3.12/site-packages/pykoplenti-*.dist-info/METADATA | grep Version
```

## 📋 Erwartete Ergebnisse

| Methode | Erwartete Ausgabe | Was es bedeutet |
|---------|------------------|----------------|
| **Log Check** | `pykoplenti 1.5.0rc1` | ✅ Korrekte Version |
| **pip list** | `pykoplenti==1.5.0rc1` | ✅ Installiert |
| **pkg_resources** | `1.5.0rc1` | ✅ Verfügbar |
| **File System** | `pykoplenti-1.5.0rc1.dist-info` | ✅ Paket vorhanden |

## ⚠️ Wichtige Hinweise

### **Wenn immer noch 1.4.0 angezeigt wird:**
1. **Home Assistant komplett neu starten** (nicht nur Integration)
2. **Cache leeren**: `pip cache purge`
3. **Manuell installieren**: `pip install pykoplenti==1.5.0rc1 --force-reinstall`

### **Debugging Schritte:**
```bash
# 1. Prüfen ob HA die neue Version lädt
python3 -c "import pykoplenti; print(pykoplenti.__file__)"

# 2. Prüfen ob mehrere Versionen installiert sind
pip list | grep pykoplenti

# 3. Alte Version deinstallieren
pip uninstall pykoplenti -y
pip install pykoplenti==1.5.0rc1
```

## 🎯 Schnellste Methode

**Für deine G3 L 20 kW Setup:**
```bash
# Direkt im HA Terminal
docker exec homeassistant python3 -c "import pkg_resources; print('pykoplenti version:', pkg_resources.get_distribution('pykoplenti').version)"
```

**Sollte ausgeben:** `pykoplenti version: 1.5.0rc1`
