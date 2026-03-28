from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import time as pytime
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.kostal_kore.request_scheduler import (
    MIN_REQUEST_PAUSE,
    RequestScheduler,
)
from custom_components.kostal_kore.scheduled_session import ScheduledSession


@pytest.mark.asyncio
async def test_request_scheduler_counts_requests_and_lock_stats() -> None:
    scheduler = RequestScheduler()
    release = asyncio.Event()

    async def _hold_lock() -> None:
        async with scheduler.request("first"):
            assert scheduler.get_stats()["lock_held"] is True
            release.set()
            await asyncio.sleep(0.02)

    holder = asyncio.create_task(_hold_lock())
    await release.wait()

    async with scheduler.request("second"):
        pass

    await holder

    stats = scheduler.get_stats()
    assert scheduler.request_count == 2
    assert scheduler.timeout_count == 0
    assert stats["total_requests"] == 2
    assert stats["waits"] == scheduler.wait_count
    assert stats["timeouts"] == 0
    assert stats["lock_held"] is False


@pytest.mark.asyncio
async def test_request_scheduler_records_waits_deterministically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = RequestScheduler()
    scheduler._lock = MagicMock()
    scheduler._lock.acquire = AsyncMock(return_value=True)
    scheduler._lock.release = MagicMock()
    scheduler._lock.locked = MagicMock(return_value=False)
    real_monotonic = pytime.monotonic
    times = iter([1.0, 1.02, 1.02, 1.03])
    monkeypatch.setattr(
        "custom_components.kostal_kore.request_scheduler.time.monotonic",
        lambda: next(times, real_monotonic()),
    )

    async with scheduler.request("waited"):
        pass

    stats = scheduler.get_stats()
    assert scheduler.wait_count == 1
    assert stats["waits"] == 1


@pytest.mark.asyncio
async def test_request_scheduler_applies_pause_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    scheduler = RequestScheduler()

    async with scheduler.request("first"):
        pass

    sleep_calls: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr(
        "custom_components.kostal_kore.request_scheduler.asyncio.sleep",
        _fake_sleep,
    )

    scheduler._last_request_time = 1.0
    monkeypatch.setattr(
        "custom_components.kostal_kore.request_scheduler.time.monotonic",
        lambda: 1.0,
    )

    async with scheduler.request("paused"):
        pass

    assert sleep_calls == [pytest.approx(MIN_REQUEST_PAUSE)]

    async def _raise_timeout(awaitable, *args, **kwargs):
        if hasattr(awaitable, "close"):
            awaitable.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(
        "custom_components.kostal_kore.request_scheduler.asyncio.wait_for",
        _raise_timeout,
    )

    with pytest.raises(asyncio.TimeoutError):
        async with scheduler.request("timeout"):
            pass

    assert scheduler.timeout_count == 1


class _SchedulerRecorder:
    def __init__(self) -> None:
        self.sources: list[str] = []

    @asynccontextmanager
    async def request(self, source: str = ""):
        self.sources.append(source)
        yield


@pytest.mark.asyncio
async def test_scheduled_session_wraps_request_and_method_helpers() -> None:
    response = AsyncMock()
    response.__aexit__ = AsyncMock()

    request_ctx = AsyncMock()
    request_ctx.__aenter__ = AsyncMock(return_value=response)

    session = MagicMock()
    session.request.return_value = request_ctx
    session.closed = True

    scheduler = _SchedulerRecorder()
    wrapped = ScheduledSession(session, scheduler)

    async with wrapped.request("GET", "http://example.invalid", headers={"x": "1"}) as result:
        assert result is response

    assert scheduler.sources == ["rest_GET"]
    session.request.assert_called_once_with("GET", "http://example.invalid", headers={"x": "1"})
    response.__aexit__.assert_awaited_once()
    assert wrapped.closed is True

    async with wrapped.get("http://example.invalid/get") as result:
        assert result is response

    async with wrapped.post("http://example.invalid/post", json={"ok": True}) as result:
        assert result is response

    assert scheduler.sources[-2:] == ["rest_GET", "rest_POST"]
    assert session.request.call_args_list[-2].args[:2] == ("GET", "http://example.invalid/get")
    assert session.request.call_args_list[-1].args[:2] == ("POST", "http://example.invalid/post")


@pytest.mark.asyncio
async def test_scheduled_session_exit_without_enter_is_safe() -> None:
    wrapped = ScheduledSession(MagicMock(), _SchedulerRecorder())
    pending = wrapped.request("DELETE", "http://example.invalid")
    await pending.__aexit__(None, None, None)
