"""Token bucket per-chat rate limiter."""
from __future__ import annotations

import asyncio
import time

import pytest

from src.telegram.rate_limiter import ChatRateLimiter, TokenBucket


@pytest.mark.asyncio
async def test_burst_capacity_immediate():
    bucket = TokenBucket(rate=1.0, capacity=5)
    start = time.monotonic()
    for _ in range(5):
        await bucket.acquire(1.0)
    elapsed = time.monotonic() - start
    assert elapsed < 0.1, f"burst of 5 should be instant, took {elapsed}s"


@pytest.mark.asyncio
async def test_refill_paces_after_burst():
    bucket = TokenBucket(rate=10.0, capacity=2)  # 10/sec for fast test
    for _ in range(2):
        await bucket.acquire(1.0)
    start = time.monotonic()
    await bucket.acquire(1.0)
    elapsed = time.monotonic() - start
    assert 0.05 < elapsed < 0.5, f"refill at 10/sec should take ~0.1s, got {elapsed}"


@pytest.mark.asyncio
async def test_different_chats_no_contention():
    rl = ChatRateLimiter(rate=10.0, capacity=2)
    # exhaust chat A
    await rl.acquire("A")
    await rl.acquire("A")
    # chat B should still have full capacity
    start = time.monotonic()
    await rl.acquire("B")
    await rl.acquire("B")
    elapsed = time.monotonic() - start
    assert elapsed < 0.1


@pytest.mark.asyncio
async def test_health_tracks_chat_count():
    rl = ChatRateLimiter(rate=1.0, capacity=5)
    assert rl.health()["tracked_chats"] == 0
    await rl.acquire("A")
    await rl.acquire("B")
    await rl.acquire("A")
    assert rl.health()["tracked_chats"] == 2
