import asyncio
import time
from collections import deque


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self._limit = limit
        self._window = window_seconds
        self._events: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    async def allow(self, key: str) -> bool:
        now = time.monotonic()
        async with self._lock:
            bucket = self._events.setdefault(key, deque())
            while bucket and now - bucket[0] > self._window:
                bucket.popleft()
            if len(bucket) >= self._limit:
                return False
            bucket.append(now)
            if len(self._events) > 10000:
                self._prune(now)
            return True

    def _prune(self, now: float) -> None:
        stale = [
            key
            for key, bucket in self._events.items()
            if not bucket or now - bucket[-1] > self._window
        ]
        for key in stale:
            self._events.pop(key, None)
