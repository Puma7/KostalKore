"""Request Scheduler -- serializes all requests to the Kostal inverter.

The Kostal inverter has a single processor handling both REST API (port 80)
and Modbus TCP (port 1502). Parallel requests from multiple coordinators
cause timeouts and 503 errors.

This scheduler ensures:
- Max 1 request to the inverter at any time (global lock)
- Minimum pause between requests (50ms, configurable)
- Priority: Modbus fast-poll > REST process data > REST settings
- Request counting for diagnostics

Usage:
    scheduler = RequestScheduler()

    # In REST coordinator:
    async with scheduler.request("rest_process"):
        data = await client.get_process_data_values(...)

    # In Modbus coordinator:
    async with scheduler.request("modbus_fast"):
        data = await modbus_client.read_register(...)
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Final

_LOGGER: Final = logging.getLogger(__name__)

MIN_REQUEST_PAUSE: Final[float] = 0.02
REQUEST_TIMEOUT: Final[float] = 120.0


class RequestScheduler:
    """Serializes all requests to the inverter across REST and Modbus."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._last_request_time: float = 0.0
        self._request_count: int = 0
        self._wait_count: int = 0
        self._timeout_count: int = 0

    @property
    def request_count(self) -> int:
        return self._request_count

    @property
    def wait_count(self) -> int:
        return self._wait_count

    @property
    def timeout_count(self) -> int:
        return self._timeout_count

    @asynccontextmanager
    async def request(self, source: str = "") -> AsyncGenerator[None, None]:
        """Acquire exclusive access to the inverter.

        Usage:
            async with scheduler.request("modbus_fast"):
                await do_something()
        """
        t0 = time.monotonic()
        try:
            await asyncio.wait_for(
                self._lock.acquire(), timeout=REQUEST_TIMEOUT,
            )
        except asyncio.TimeoutError:
            self._timeout_count += 1
            _LOGGER.warning(
                "Request scheduler timeout waiting for lock (source: %s, "
                "queue depth may be too high)", source,
            )
            raise

        waited = time.monotonic() - t0
        if waited > 0.01:
            self._wait_count += 1

        try:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < MIN_REQUEST_PAUSE:
                await asyncio.sleep(MIN_REQUEST_PAUSE - elapsed)

            self._request_count += 1
            yield
        finally:
            self._last_request_time = time.monotonic()
            self._lock.release()

    def get_stats(self) -> dict[str, Any]:
        return {
            "total_requests": self._request_count,
            "waits": self._wait_count,
            "timeouts": self._timeout_count,
            "lock_held": self._lock.locked(),
        }
