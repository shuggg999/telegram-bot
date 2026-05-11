"""Per-chat_id token bucket rate limiter."""
from __future__ import annotations

import asyncio
import time
from typing import Dict


class TokenBucket:
    def __init__(self, rate: float, capacity: int) -> None:
        self._rate = rate
        self._capacity = capacity
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, n: float = 1.0) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
                self._last_refill = now
                if self._tokens >= n:
                    self._tokens -= n
                    return
                deficit = n - self._tokens
                sleep_for = deficit / self._rate
                await asyncio.sleep(sleep_for)


class ChatRateLimiter:
    """Lazy-instantiate one TokenBucket per chat_id."""

    def __init__(self, rate: float = 1.0, capacity: int = 5) -> None:
        self._rate = rate
        self._capacity = capacity
        self._buckets: Dict[str, TokenBucket] = {}
        self._guard = asyncio.Lock()

    async def acquire(self, chat_id: str) -> None:
        bucket = self._buckets.get(chat_id)
        if bucket is None:
            async with self._guard:
                bucket = self._buckets.get(chat_id)
                if bucket is None:
                    bucket = TokenBucket(self._rate, self._capacity)
                    self._buckets[chat_id] = bucket
        await bucket.acquire(1.0)

    def health(self) -> dict:
        return {"status": "ok", "tracked_chats": len(self._buckets)}
