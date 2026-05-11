"""GET /api/v1/health verdict ladder."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import src.main as main_module


def _setup_state(app, **overrides):
    app.state.settings = main_module.settings
    app.state.is_running = True
    app.state.telegram_client = MagicMock()
    app.state.telegram_client.health = MagicMock(return_value=overrides.get(
        "telegram_health", {"status": "ok", "configured": True, "consecutive_failures": 0, "last_success_age_seconds": 30.0}
    ))
    app.state.audit_writer = MagicMock()
    app.state.audit_writer.health = MagicMock(return_value=overrides.get(
        "audit_health", {"status": "ok", "connected": True, "audit_failure_count": 0, "last_success_age_seconds": 30.0}
    ))
    app.state.rate_limiter = MagicMock()
    app.state.rate_limiter.health = MagicMock(return_value=overrides.get(
        "rate_health", {"status": "ok", "tracked_chats": 5}
    ))


def test_all_ok_returns_200():
    _setup_state(main_module.app)
    r = TestClient(main_module.app).get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "healthy"
    for k in ("telegram_api", "clickhouse_audit", "chat_rate_limiter"):
        assert k in body["details"]


def test_telegram_failed_returns_503():
    _setup_state(main_module.app, telegram_health={
        "status": "failed", "reason": "TELEGRAM_BOT_TOKEN not set", "configured": False
    })
    r = TestClient(main_module.app).get("/api/v1/health")
    assert r.status_code == 503
    assert r.json()["status"] == "unhealthy"


def test_audit_degraded_returns_200():
    _setup_state(main_module.app, audit_health={
        "status": "degraded", "connected": False, "audit_failure_count": 15, "last_success_age_seconds": None
    })
    r = TestClient(main_module.app).get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "degraded"


def test_subprobe_exception_does_not_500():
    _setup_state(main_module.app)
    main_module.app.state.telegram_client.health = MagicMock(side_effect=RuntimeError("boom"))
    r = TestClient(main_module.app).get("/api/v1/health")
    assert r.status_code != 500
    body = r.json()
    assert body["details"]["telegram_api"]["status"] == "failed"
    assert "boom" in body["details"]["telegram_api"]["reason"]
