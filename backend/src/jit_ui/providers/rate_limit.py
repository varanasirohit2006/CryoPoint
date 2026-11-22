"""Shared request pacing and bounded provider retry helpers."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


class RequestPacer:
    """Serialize requests, spacing every attempt to stay within the RPM budget."""

    def __init__(
        self,
        requests_per_minute: int = 12,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not 1 <= requests_per_minute <= 12:
            raise ValueError("Gemini requests_per_minute must be between 1 and 12")
        self.interval = 60.0 / requests_per_minute
        self._clock = clock
        self._sleep = sleep
        self._next_request = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            delay = max(0.0, self._next_request - self._clock())
            if delay:
                await self._sleep(delay)
            self._next_request = max(self._clock(), self._next_request) + self.interval


def retry_after_seconds(value: str | int | float | None) -> float | None:
    """Parse Retry-After seconds or HTTP dates without guessing a shorter delay."""
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            parsed = parsedate_to_datetime(str(value))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None
