"""Telegram Bot API client with 429 backoff + 5xx retry."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import httpx
from loguru import logger


_MAX_429_RETRIES = 5
_5XX_RETRIES = 3


class TelegramClient:
    """Minimal Telegram Bot API wrapper.

    `send_message(chat_id, text)` returns a tuple `(success, last_response_dict, attempts)`.
    Non-recoverable errors (400/401/403) return immediately without retry.
    """

    def __init__(
        self,
        token: str,
        parse_mode: str = "HTML",
        proxy_url: Optional[str] = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._token = token
        self._parse_mode = parse_mode
        self._proxy = proxy_url or None
        client_kwargs: Dict[str, Any] = {"timeout": httpx.Timeout(timeout_seconds)}
        if self._proxy:
            client_kwargs["proxies"] = self._proxy
        self._client = httpx.AsyncClient(**client_kwargs)
        self._consecutive_failures = 0
        self._last_success_ts: Optional[datetime] = None

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def configured(self) -> bool:
        return bool(self._token)

    def health(self) -> Dict[str, Any]:
        if not self._token:
            return {"status": "failed", "reason": "TELEGRAM_BOT_TOKEN not set", "configured": False}
        last_age = None
        if self._last_success_ts is not None:
            last_age = (datetime.now(timezone.utc) - self._last_success_ts).total_seconds()
        if self._consecutive_failures > 5:
            status = "degraded"
        else:
            status = "ok"
        return {
            "status": status,
            "configured": True,
            "consecutive_failures": self._consecutive_failures,
            "last_success_age_seconds": last_age,
        }

    async def send_message(self, chat_id: str, text: str) -> Tuple[bool, Dict[str, Any], int]:
        """Send one message. Returns (success, last_response_json, attempts)."""
        if not self._token:
            return False, {"error": "TELEGRAM_BOT_TOKEN not set"}, 0

        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        body = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": self._parse_mode,
            "disable_web_page_preview": True,
        }
        attempts_429 = 0
        attempts_5xx = 0
        total_attempts = 0
        last_resp: Dict[str, Any] = {}

        while True:
            total_attempts += 1
            try:
                resp = await self._client.post(url, json=body)
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                # Treat as 5xx-style transient
                attempts_5xx += 1
                if attempts_5xx >= _5XX_RETRIES:
                    self._consecutive_failures += 1
                    return False, {"error": str(exc)}, total_attempts
                await asyncio.sleep(2 ** (attempts_5xx - 1))
                continue

            try:
                last_resp = resp.json()
            except Exception:
                last_resp = {"_raw": resp.text[:500]}

            if resp.status_code == 200 and last_resp.get("ok"):
                self._consecutive_failures = 0
                self._last_success_ts = datetime.now(timezone.utc)
                return True, last_resp, total_attempts

            if resp.status_code == 429:
                attempts_429 += 1
                retry_after = (
                    last_resp.get("parameters", {}).get("retry_after", 1)
                    if isinstance(last_resp, dict)
                    else 1
                )
                logger.warning("Telegram 429, retry_after={}s (attempt {}/{})",
                               retry_after, attempts_429, _MAX_429_RETRIES)
                if attempts_429 >= _MAX_429_RETRIES:
                    self._consecutive_failures += 1
                    return False, last_resp, total_attempts
                await asyncio.sleep(float(retry_after) + 0.5)
                continue

            if 500 <= resp.status_code < 600:
                attempts_5xx += 1
                if attempts_5xx >= _5XX_RETRIES:
                    self._consecutive_failures += 1
                    return False, last_resp, total_attempts
                await asyncio.sleep(2 ** (attempts_5xx - 1))
                continue

            # 4xx (other than 429): non-recoverable
            self._consecutive_failures += 1
            logger.error("Telegram non-recoverable {}: {}", resp.status_code, last_resp)
            return False, last_resp, total_attempts
