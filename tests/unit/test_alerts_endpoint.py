"""POST /alerts endpoint behavior."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import src.main as main_module
from src.api import alerts as alerts_mod


def _alert(alert_id="alert-1", schema_version=1):
    return {
        "schema_version": schema_version,
        "alert_id": alert_id,
        "source_service": "volume-monitor",
        "format": "volume_alert_v1",
        "payload": {"symbol": "BTC/USDT", "level": "warn", "tier": "mega"},
        "chat_id": "12345",
    }


@pytest.fixture
def client_with_mocks(monkeypatch):
    """Create TestClient with fully-mocked state, bypassing lifespan."""
    # Reset module-level seen cache between tests
    alerts_mod._seen = alerts_mod._SeenCache()

    app = main_module.app
    app.state.settings = main_module.settings
    app.state.is_running = True
    app.state.telegram_client = MagicMock()
    app.state.telegram_client.send_message = AsyncMock(return_value=(True, {"ok": True}, 1))
    app.state.telegram_client.health = MagicMock(return_value={"status": "ok", "configured": True})
    app.state.rate_limiter = MagicMock()
    app.state.rate_limiter.acquire = AsyncMock()
    app.state.rate_limiter.health = MagicMock(return_value={"status": "ok", "tracked_chats": 0})
    app.state.audit_writer = MagicMock()
    app.state.audit_writer.write_row = AsyncMock()
    app.state.audit_writer.health = MagicMock(return_value={"status": "ok", "connected": True})

    # Force TestClient to NOT run lifespan (default behavior in starlette is to skip)
    return TestClient(app)


def test_post_alert_returns_202(client_with_mocks):
    r = client_with_mocks.post("/alerts", json=_alert())
    assert r.status_code == 202
    body = r.json()
    assert body["alert_id"] == "alert-1"
    assert "received_at" in body


def test_post_unsupported_schema_400(client_with_mocks):
    r = client_with_mocks.post("/alerts", json=_alert(schema_version=2))
    assert r.status_code == 400
    body = r.json()
    assert "schema_version" in body["detail"]["error"]


def test_duplicate_alert_id_returns_202_with_flag(client_with_mocks):
    r1 = client_with_mocks.post("/alerts", json=_alert(alert_id="dup"))
    assert r1.status_code == 202
    r2 = client_with_mocks.post("/alerts", json=_alert(alert_id="dup"))
    assert r2.status_code == 202
    assert r2.json().get("duplicate") is True


def test_malformed_json_returns_422(client_with_mocks):
    r = client_with_mocks.post("/alerts", content="not json", headers={"content-type": "application/json"})
    assert r.status_code == 422


def test_missing_required_field_returns_422(client_with_mocks):
    bad = _alert()
    del bad["alert_id"]
    r = client_with_mocks.post("/alerts", json=bad)
    assert r.status_code == 422
