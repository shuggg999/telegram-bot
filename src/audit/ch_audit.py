"""ClickHouse audit writer for telegram_audit table.

Failures log warn and do NOT raise — audit is observability, not gating.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

import aiochclient
import aiohttp
from loguru import logger


_DDL = """
CREATE TABLE IF NOT EXISTS crypto_data.telegram_audit (
    received_at DateTime64(3),
    alert_id String,
    source_service LowCardinality(String),
    chat_id String,
    status LowCardinality(String),
    attempts UInt8,
    last_response_code Nullable(UInt16),
    last_response_body Nullable(String),
    last_attempt_at DateTime64(3),
    elapsed_ms UInt32
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(received_at)
ORDER BY (received_at, alert_id)
TTL toDateTime(received_at) + INTERVAL 90 DAY
"""


class AuditWriter:
    """INSERT-only client for `crypto_data.telegram_audit`."""

    def __init__(self, host: str, port: int, user: str, password: str, database: str) -> None:
        self._url = f"http://{host}:{port}"
        self._user = user
        self._password = password
        self._database = database
        self._session: Optional[aiohttp.ClientSession] = None
        self._client: Optional[aiochclient.ChClient] = None
        self._failure_count = 0
        self._last_success_ts: Optional[datetime] = None

    async def connect(self) -> None:
        self._session = aiohttp.ClientSession()
        self._client = aiochclient.ChClient(
            self._session,
            url=self._url,
            user=self._user,
            password=self._password or "",
            database=self._database,
        )
        # Best-effort: ensure table exists (idempotent)
        try:
            await self._client.execute(_DDL)
        except Exception as exc:  # noqa: BLE001 — non-fatal
            logger.warning("AuditWriter DDL failed (non-fatal, table may already exist): {}", exc)

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
        self._client = None

    async def write_row(
        self,
        *,
        alert_id: str,
        source_service: str,
        chat_id: str,
        status: str,
        attempts: int,
        last_response_code: Optional[int] = None,
        last_response_body: Optional[str] = None,
        elapsed_ms: int = 0,
    ) -> None:
        if self._client is None:
            logger.warning("AuditWriter not connected; skipping row alert_id={}", alert_id)
            return
        now = datetime.now(timezone.utc)
        try:
            await self._client.execute(
                """
                INSERT INTO telegram_audit
                (received_at, alert_id, source_service, chat_id, status, attempts,
                 last_response_code, last_response_body, last_attempt_at, elapsed_ms)
                VALUES
                """,
                (
                    now,
                    alert_id,
                    source_service,
                    chat_id,
                    status,
                    attempts,
                    last_response_code,
                    (last_response_body[:2000] if last_response_body else None),
                    now,
                    elapsed_ms,
                ),
            )
            self._last_success_ts = now
        except Exception as exc:  # noqa: BLE001 — audit failures must not block delivery
            self._failure_count += 1
            logger.warning("AuditWriter INSERT failed (#{}): {}", self._failure_count, exc)

    def health(self) -> Dict[str, Any]:
        last_age = None
        if self._last_success_ts is not None:
            last_age = (datetime.now(timezone.utc) - self._last_success_ts).total_seconds()
        if self._client is None:
            status = "failed"
        elif self._failure_count > 10:
            status = "degraded"
        else:
            status = "ok"
        return {
            "status": status,
            "connected": self._client is not None,
            "audit_failure_count": self._failure_count,
            "last_success_age_seconds": last_age,
        }
