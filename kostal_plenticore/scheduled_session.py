"""Scheduled aiohttp ClientSession wrapper.

Wraps an aiohttp ClientSession so that every HTTP request goes through
the RequestScheduler. This ensures REST API calls are serialized with
Modbus requests without modifying any Coordinator or Entity code.

Lock is held ONLY during the HTTP request itself, NOT during response
reading. This prevents long REST responses from blocking Modbus polls.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from aiohttp import ClientSession

from .request_scheduler import RequestScheduler

_LOGGER: Final = logging.getLogger(__name__)


class ScheduledSession:
    """Proxy around aiohttp.ClientSession that serializes requests via scheduler."""

    def __init__(self, session: ClientSession, scheduler: RequestScheduler) -> None:
        self._session = session
        self._scheduler = scheduler

    def request(self, method: str, url: Any, **kwargs: Any) -> Any:
        """Wrap session.request with the scheduler."""
        return _ScheduledRequest(self._session, self._scheduler, method, url, kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)


class _ScheduledRequest:
    """Context manager that acquires the scheduler only during the HTTP send."""

    def __init__(
        self,
        session: ClientSession,
        scheduler: RequestScheduler,
        method: str,
        url: Any,
        kwargs: dict[str, Any],
    ) -> None:
        self._session = session
        self._scheduler = scheduler
        self._method = method
        self._url = url
        self._kwargs = kwargs
        self._response: Any = None

    async def __aenter__(self) -> Any:
        async with self._scheduler.request(f"rest_{self._method}"):
            self._response = await self._session.request(
                self._method, self._url, **self._kwargs
            ).__aenter__()
        return self._response

    async def __aexit__(self, *args: Any) -> None:
        if self._response is not None:
            await self._response.__aexit__(*args)
