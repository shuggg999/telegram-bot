"""POST /alerts endpoint with async background delivery."""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request, status
from loguru import logger
from pydantic import BaseModel, Field

router = APIRouter()


_ALERT_TTL_SECONDS = 3600
_SEEN_CACHE_MAX = 10000


class AlertEvent(BaseModel):
    schema_version: int = Field(...)
    alert_id: str = Field(..., min_length=1)
    source_service: str = Field(..., min_length=1)
    format: str = Field(..., min_length=1)
    payload: Dict[str, Any] = Field(default_factory=dict)
    chat_id: Optional[str] = None


class _SeenCache:
    """LRU cache of alert_id with TTL."""

    def __init__(self, max_size: int = _SEEN_CACHE_MAX, ttl: float = _ALERT_TTL_SECONDS) -> None:
        self._items: "OrderedDict[str, float]" = OrderedDict()
        self._max = max_size
        self._ttl = ttl

    def seen(self, alert_id: str) -> bool:
        now = time.monotonic()
        # purge expired
        while self._items:
            k, t = next(iter(self._items.items()))
            if now - t > self._ttl:
                self._items.popitem(last=False)
            else:
                break
        if alert_id in self._items:
            # touch
            self._items.move_to_end(alert_id)
            return True
        self._items[alert_id] = now
        if len(self._items) > self._max:
            self._items.popitem(last=False)
        return False


_seen = _SeenCache()


async def _deliver(request: Request, alert: AlertEvent) -> None:
    """Background task: rate-limit → format → send → audit."""
    state = request.app.state
    settings = state.settings
    chat_id = alert.chat_id or settings.TELEGRAM_DEFAULT_CHAT_ID
    if not chat_id:
        logger.error("Alert {} has no chat_id and no default configured; dropping", alert.alert_id)
        await state.audit_writer.write_row(
            alert_id=alert.alert_id, source_service=alert.source_service,
            chat_id="", status="failed", attempts=0,
            last_response_body="no chat_id",
        )
        return

    start = time.monotonic()
    # rate-limit per chat
    await state.rate_limiter.acquire(chat_id)

    # format
    try:
        if alert.format == "volume_alert_v1":
            from src.formatter.volume_alert import format_volume_alert
            text = format_volume_alert(alert.payload)
        else:
            text = f"[{alert.format}] {alert.payload}"
    except Exception as exc:  # noqa: BLE001
        logger.error("Formatter failed for {}: {}", alert.format, exc)
        await state.audit_writer.write_row(
            alert_id=alert.alert_id, source_service=alert.source_service,
            chat_id=chat_id, status="failed", attempts=0,
            last_response_body=f"format error: {exc}",
        )
        return

    # send
    success, last_resp, attempts = await state.telegram_client.send_message(chat_id, text)
    elapsed_ms = int((time.monotonic() - start) * 1000)

    if success:
        status_str = "sent"
    elif attempts >= 5 and last_resp.get("error_code") == 429:
        status_str = "dropped"
    else:
        status_str = "failed"

    await state.audit_writer.write_row(
        alert_id=alert.alert_id,
        source_service=alert.source_service,
        chat_id=chat_id,
        status=status_str,
        attempts=attempts,
        last_response_code=last_resp.get("error_code") if isinstance(last_resp, dict) else None,
        last_response_body=str(last_resp)[:2000] if last_resp else None,
        elapsed_ms=elapsed_ms,
    )


@router.post("/alerts", status_code=status.HTTP_202_ACCEPTED)
async def receive_alert(alert: AlertEvent, request: Request) -> Dict[str, Any]:
    if alert.schema_version != 1:
        raise HTTPException(
            status_code=400,
            detail={"error": f"unsupported schema_version: {alert.schema_version}", "supported": [1]},
        )

    if _seen.seen(alert.alert_id):
        logger.info("Duplicate alert_id {}; returning 202 without delivery", alert.alert_id)
        await request.app.state.audit_writer.write_row(
            alert_id=alert.alert_id,
            source_service=alert.source_service,
            chat_id=alert.chat_id or request.app.state.settings.TELEGRAM_DEFAULT_CHAT_ID or "",
            status="duplicate",
            attempts=0,
        )
        return {"alert_id": alert.alert_id, "received_at": datetime.now(timezone.utc).isoformat(), "duplicate": True}

    asyncio.create_task(_deliver(request, alert))
    return {"alert_id": alert.alert_id, "received_at": datetime.now(timezone.utc).isoformat()}
