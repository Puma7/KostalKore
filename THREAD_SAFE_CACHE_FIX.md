# Thread-Safe Cache Fix für Coordinator

## 🚨 Aktuelle Probleme

### **1. Kein Thread-Safety:**
```python
# PROBLEMATISCH
def get(self, key: str) -> Any | None:
    if key not in self._cache:  # ❌ Race condition möglich
        self._misses += 1      # ❌ Nicht atomar
        return None
```

### **2. Race Conditions:**
- **Concurrent reads** können gleichzeitig auf `_cache` zugreifen
- **Hit/Mess Zähler** werden nicht atomar aktualisiert
- **TTL checks** können während writes passieren

## ✅ Thread-Safe Lösung

### **Mit threading.Lock:**
```python
import threading
from typing import Any, Optional

class ThreadSafeRequestCache:
    def __init__(self, ttl_seconds: float = 5.0) -> None:
        self._cache: dict[str, Any] = {}
        self._timestamps: dict[str, datetime] = {}
        self._ttl = timedelta(seconds=ttl_seconds)
        self._hits = 0
        self._misses = 0
        self._lock = threading.RLock()  # ✅ Reentrant Lock
    
    def get(self, key: str) -> Optional[Any]:
        with self._lock:  # ✅ Thread-safe
            if key not in self._cache:
                self._misses += 1
                return None
            
            if datetime.now() - self._timestamps[key] > self._ttl:
                self.invalidate(key)
                self._misses += 1
                return None
                
            self._hits += 1
            return self._cache[key]
    
    def set(self, key: str, value: Any) -> None:
        with self._lock:  # ✅ Thread-safe
            self._cache[key] = value
            self._timestamps[key] = datetime.now()
    
    def invalidate(self, key: str) -> None:
        with self._lock:  # ✅ Thread-safe
            self._cache.pop(key, None)
            self._timestamps.pop(key, None)
```

### **Mit asyncio.Lock (besser für HA):**
```python
import asyncio
from typing import Any, Optional

class AsyncRequestCache:
    def __init__(self, ttl_seconds: float = 5.0) -> None:
        self._cache: dict[str, Any] = {}
        self._timestamps: dict[str, datetime] = {}
        self._ttl = timedelta(seconds=ttl_seconds)
        self._hits = 0
        self._misses = 0
        self._lock = asyncio.Lock()  # ✅ Async Lock
    
    async def get(self, key: str) -> Optional[Any]:
        async with self._lock:  # ✅ Async thread-safe
            if key not in self._cache:
                self._misses += 1
                return None
            
            if datetime.now() - self._timestamps[key] > self._ttl:
                await self.invalidate(key)
                self._misses += 1
                return None
                
            self._hits += 1
            return self._cache[key]
    
    async def set(self, key: str, value: Any) -> None:
        async with self._lock:  # ✅ Async thread-safe
            self._cache[key] = value
            self._timestamps[key] = datetime.now()
```

## 🎯 Empfehlung für Home Assistant

### **Async Version verwenden:**
```python
# In coordinator.py
class AsyncRequestCache:
    def __init__(self, ttl_seconds: float = 5.0) -> None:
        self._cache: dict[str, Any] = {}
        self._timestamps: dict[str, datetime] = {}
        self._ttl = timedelta(seconds=ttl_seconds)
        self._hits = 0
        self._misses = 0
        self._lock = asyncio.Lock()
    
    async def get(self, key: str) -> Any | None:
        async with self._lock:
            # ... thread-safe implementation
```

### **Oder einfacher mit functools.lru_cache:**
```python
from functools import lru_cache
import asyncio

@lru_cache(maxsize=128)
def cached_api_call(key: str, timestamp: float) -> Any:
    # Python's lru_cache ist thread-safe!
    return api_result

# In async context
async def get_cached_data(key: str):
    timestamp = time.time() // 5  # 5-second windows
    return cached_api_call(key, timestamp)
```

## 📋 Fazit

**Das aktuelle Cache-System funktioniert NICHT sicher in HA!**

### **Probleme:**
- ❌ **Kein Thread-Safety**
- ❌ **Race Conditions**
- ❌ **Inkonsistente Daten**

### **Lösung:**
- ✅ **Asyncio.Lock** für async operations
- ✅ **Threading.Lock** für sync operations  
- ✅ **functools.lru_cache** (einfach & thread-safe)

**Sofort korrigieren für produktiven Einsatz!**
