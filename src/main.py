"""telegram-bot FastAPI app."""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from typing import Any, Callable, Dict, Optional

import uvicorn
from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from loguru import logger

from src.api.alerts import router as alerts_router
from src.audit.ch_audit import AuditWriter
from src.config import settings
from src.telegram.client import TelegramClient
from src.telegram.rate_limiter import ChatRateLimiter

# Logging
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level:<8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
    level=settings.LOG_LEVEL,
)


def _safe_subprobe(name: str, fn: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Health sub-probe {!r} raised: {}", name, exc)
        return {"status": "failed", "reason": str(exc)}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    app.state.settings = settings
    app.state.telegram_client = TelegramClient(
        token=settings.TELEGRAM_BOT_TOKEN,
        parse_mode=settings.TELEGRAM_PARSE_MODE,
        proxy_url=settings.TELEGRAM_PROXY_URL or None,
    )
    app.state.rate_limiter = ChatRateLimiter(
        rate=settings.RATE_LIMIT_REFILL_PER_SEC,
        capacity=settings.RATE_LIMIT_CAPACITY,
    )
    app.state.audit_writer = AuditWriter(
        host=settings.CLICKHOUSE_HOST,
        port=settings.CLICKHOUSE_PORT,
        user=settings.CLICKHOUSE_USER,
        password=settings.CLICKHOUSE_PASSWORD,
        database=settings.CLICKHOUSE_DATABASE,
    )
    try:
        await app.state.audit_writer.connect()
    except Exception as exc:
        logger.warning("AuditWriter connect failed (non-fatal): {}", exc)

    app.state.is_running = True
    logger.info("✅ telegram-bot ready")
    yield
    # Shutdown
    app.state.is_running = False
    try:
        await app.state.telegram_client.aclose()
    except Exception:
        pass
    try:
        await app.state.audit_writer.close()
    except Exception:
        pass


app = FastAPI(title="telegram-bot", version="0.1.0", lifespan=lifespan)
app.include_router(alerts_router)


@app.get("/api/v1/health")
async def health_check():
    state = app.state
    if not getattr(state, "is_running", False):
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "error": "service not running"},
        )

    health_status = "healthy"

    def _absorb(s: Optional[str]) -> None:
        nonlocal health_status
        if s == "failed":
            health_status = "unhealthy"
        elif s != "ok" and health_status == "healthy":
            health_status = "degraded"

    details: Dict[str, Any] = {"running": True}
    tg = _safe_subprobe(
        "telegram_api",
        lambda: state.telegram_client.health(),
    )
    details["telegram_api"] = tg
    _absorb(tg.get("status"))

    audit = _safe_subprobe("clickhouse_audit", lambda: state.audit_writer.health())
    details["clickhouse_audit"] = audit
    _absorb(audit.get("status"))

    rate = _safe_subprobe("chat_rate_limiter", lambda: state.rate_limiter.health())
    details["chat_rate_limiter"] = rate
    _absorb(rate.get("status"))

    return JSONResponse(
        status_code=503 if health_status == "unhealthy" else 200,
        content=jsonable_encoder({"status": health_status, "details": details}),
    )


@app.get("/")
async def root():
    return {"service": "telegram-bot", "version": "0.1.0", "health": "/api/v1/health"}


if __name__ == "__main__":
    uvicorn.run(
        "src.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        log_level=settings.LOG_LEVEL.lower(),
    )
