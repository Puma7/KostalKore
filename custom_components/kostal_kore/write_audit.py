"""Write-audit ring buffer for KostalKore.

Tracks Modbus/MQTT/Proxy write events (successes and rejections) in a bounded
deque so that observability sensors can expose recent write history without
reading through logs.

Result values:
  "ok"                  — write completed successfully
  "error"               — write attempted but failed (Modbus/network error)
  "rejected_rate"       — blocked by rate limiter (MQTT bridge)
  "rejected_soc_active" — blocked because SoC controller is active
  "rejected_installer"  — blocked by installer-protection gate
  "rejected_validation" — blocked by value/type validation
  "forwarded_direct"    — forwarded to inverter via raw Modbus (no coordinator)
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field  # noqa: F401
from datetime import datetime, timezone
from typing import Any


@dataclass
class WriteEvent:
    """A single write or rejection event."""

    ts: float          # time.monotonic() timestamp
    source: str        # "modbus_coord" | "mqtt" | "proxy_fc06" | "proxy_fc16" | "proxy_fwd"
    key: str           # register name or "addr:<address>"
    value: Any         # numeric write value (never secrets)
    result: str        # see module docstring for valid values
    detail: str = ""   # optional: exception message or client IP

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict with a wall-clock ISO timestamp."""
        wall = datetime.fromtimestamp(
            self.ts - time.monotonic() + time.time(), tz=timezone.utc
        ).isoformat()
        return {
            "ts_iso": wall,
            "source": self.source,
            "key": self.key,
            "value": self.value,
            "result": self.result,
            "detail": self.detail,
        }


_ERROR_RESULTS = frozenset({"error", "rejected_rate", "rejected_soc_active",
                             "rejected_installer", "rejected_validation"})


class WriteAuditLog:
    """Bounded ring buffer of :class:`WriteEvent` objects."""

    def __init__(self, maxlen: int = 200) -> None:
        self._buf: deque[WriteEvent] = deque(maxlen=maxlen)

    def log(self, event: WriteEvent) -> None:
        """Append an event (safe to call from async context)."""
        self._buf.append(event)

    @property
    def recent(self) -> list[WriteEvent]:
        """Snapshot of all buffered events (oldest first)."""
        return list(self._buf)

    @property
    def total_count(self) -> int:
        return len(self._buf)

    def writes_in_last_n_seconds(self, seconds: float) -> int:
        cutoff = time.monotonic() - seconds
        return sum(1 for e in self._buf if e.ts >= cutoff)

    @property
    def write_rate_per_min(self) -> float:
        """Writes (all results) in the last 60 seconds."""
        return float(self.writes_in_last_n_seconds(60))

    @property
    def error_count_5min(self) -> int:
        """Errors + rejections in the last 5 minutes."""
        cutoff = time.monotonic() - 300.0
        return sum(
            1 for e in self._buf if e.ts >= cutoff and e.result in _ERROR_RESULTS
        )
