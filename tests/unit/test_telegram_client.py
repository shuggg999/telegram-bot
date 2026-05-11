"""TelegramClient: 429 / 5xx / 4xx behavior."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from src.telegram.client import TelegramClient


@pytest.mark.asyncio
async def test_send_success(monkeypatch):
    async def fake_post(self, url, json=None, **kwargs):
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    cli = TelegramClient(token="TOKEN")
    ok, resp, attempts = await cli.send_message("123", "hi")
    assert ok is True
    assert attempts == 1
    assert resp["ok"] is True
    assert cli._consecutive_failures == 0
    await cli.aclose()


@pytest.mark.asyncio
async def test_send_missing_token():
    cli = TelegramClient(token="")
    ok, resp, attempts = await cli.send_message("123", "hi")
    assert ok is False
    assert attempts == 0
    await cli.aclose()


@pytest.mark.asyncio
async def test_send_429_then_ok(monkeypatch):
    call_count = {"n": 0}

    async def fake_post(self, url, json=None, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 0}},
                                  request=httpx.Request("POST", url))
        return httpx.Response(200, json={"ok": True}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    cli = TelegramClient(token="TOKEN")
    ok, resp, attempts = await cli.send_message("123", "hi")
    assert ok is True
    assert attempts == 2
    await cli.aclose()


@pytest.mark.asyncio
async def test_send_429_drop_after_5(monkeypatch):
    async def fake_post(self, url, json=None, **kwargs):
        return httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 0}, "error_code": 429},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    cli = TelegramClient(token="TOKEN")
    ok, resp, attempts = await cli.send_message("123", "hi")
    assert ok is False
    assert attempts == 5
    assert cli._consecutive_failures == 1
    await cli.aclose()


@pytest.mark.asyncio
async def test_send_400_no_retry(monkeypatch):
    call_count = {"n": 0}

    async def fake_post(self, url, json=None, **kwargs):
        call_count["n"] += 1
        return httpx.Response(400, json={"ok": False, "description": "Bad Request", "error_code": 400},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    cli = TelegramClient(token="TOKEN")
    ok, resp, attempts = await cli.send_message("123", "hi")
    assert ok is False
    assert attempts == 1
    assert call_count["n"] == 1
    await cli.aclose()


@pytest.mark.asyncio
async def test_send_500_then_ok(monkeypatch):
    call_count = {"n": 0}

    async def fake_post(self, url, json=None, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(500, json={"ok": False}, request=httpx.Request("POST", url))
        return httpx.Response(200, json={"ok": True}, request=httpx.Request("POST", url))

    # Speed up via patching sleep
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    async def fast_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    cli = TelegramClient(token="TOKEN")
    ok, resp, attempts = await cli.send_message("123", "hi")
    assert ok is True
    assert attempts == 2
    await cli.aclose()


def test_health_without_token():
    cli = TelegramClient(token="")
    h = cli.health()
    assert h["status"] == "failed"
    assert h["configured"] is False


def test_health_with_token():
    cli = TelegramClient(token="TOKEN")
    h = cli.health()
    assert h["status"] == "ok"
    assert h["configured"] is True
