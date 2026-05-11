"""Test volume_alert formatter output."""
from __future__ import annotations

from src.formatter.volume_alert import format_volume_alert


def test_basic_alert():
    msg = format_volume_alert({
        "exchange": "binance",
        "symbol": "BTC/USDT",
        "tier": "mega",
        "level": "warn",
        "prev_level": "normal",
        "ratio": 4.2,
        "current_avg_volume": 1234.5,
        "baseline_median_volume": 294.0,
        "threshold_used": 3.0,
        "current_window_minutes": 5,
        "baseline_window_hours": 24,
        "detected_at": "2026-05-12T00:05:02+00:00",
    })
    assert "<b>BTC/USDT</b>" in msg
    assert "binance" in msg
    assert "4.20x" in msg
    assert "warn" in msg.lower() or "WARN" in msg
    assert "mega" in msg
    assert "2026-05-12T00:05:02+00:00" in msg


def test_html_special_chars_escaped():
    msg = format_volume_alert({
        "symbol": "<script>",
        "exchange": "ex&change",
    })
    assert "<script>" not in msg
    assert "&lt;script&gt;" in msg
    assert "ex&amp;change" in msg


def test_missing_optionals_does_not_crash():
    msg = format_volume_alert({"symbol": "BTC/USDT"})
    assert "BTC/USDT" in msg
    assert "—" in msg  # missing fields show em-dash


def test_extreme_level_emoji():
    msg = format_volume_alert({"symbol": "X", "level": "extreme", "tier": "small"})
    assert "🚨" in msg
