"""Async token-bucket rate limiter for API calls (requests-per-minute)."""
from __future__ import annotations

import asyncio
import time


class RateLimiter:
    def __init__(self, max_per_minute: int) -> None:
        self.capacity = max(1, max_per_minute)
        self.tokens = float(self.capacity)
        self.rate = self.capacity / 60.0  # tokens per second
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                wait = (1.0 - self.tokens) / self.rate
                await asyncio.sleep(wait)
