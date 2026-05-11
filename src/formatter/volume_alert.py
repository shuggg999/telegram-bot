"""Format a volume-anomaly alert into a Telegram HTML message."""
from __future__ import annotations

import html
from typing import Any, Dict

_LEVEL_EMOJI = {"warn": "⚠️", "strong": "🔥", "extreme": "🚨"}
_TIER_EMOJI = {"mega": "🟣", "large": "🔵", "mid": "🟢", "small": "⚪️"}


def format_volume_alert(payload: Dict[str, Any]) -> str:
    """HTML-formatted Telegram message body.

    Expected payload keys (all optional defaults for resilience):
      exchange, symbol, tier, level, prev_level, ratio,
      current_avg_volume, baseline_median_volume, threshold_used,
      current_window_minutes, baseline_window_hours, detected_at
    """
    e = lambda v: html.escape(str(v))
    level = payload.get("level", "warn")
    tier = payload.get("tier", "mid")
    level_emoji = _LEVEL_EMOJI.get(level, "❗")
    tier_emoji = _TIER_EMOJI.get(tier, "⚪️")
    symbol = e(payload.get("symbol", "?"))
    exchange = e(payload.get("exchange", "?"))
    ratio = payload.get("ratio")
    ratio_str = f"{float(ratio):.2f}x" if isinstance(ratio, (int, float)) else "—"
    cur = payload.get("current_avg_volume")
    base = payload.get("baseline_median_volume")
    cur_str = f"{float(cur):,.0f}" if isinstance(cur, (int, float)) else "—"
    base_str = f"{float(base):,.0f}" if isinstance(base, (int, float)) else "—"
    thresh = payload.get("threshold_used")
    thresh_str = f"{float(thresh):.1f}x" if isinstance(thresh, (int, float)) else "—"
    cw = payload.get("current_window_minutes", "?")
    bw = payload.get("baseline_window_hours", "?")
    detected_at = e(payload.get("detected_at", ""))
    prev = e(payload.get("prev_level", "normal"))

    lines = [
        f"{level_emoji} <b>Volume {level.upper()}</b> {tier_emoji} <i>{tier}</i>",
        f"📊 <b>{symbol}</b> on {exchange}",
        f"🔁 Ratio: <b>{ratio_str}</b> (≥ {thresh_str} for {tier})",
        f"📈 Current {cw}m avg: {cur_str}",
        f"📉 Baseline {bw}h median: {base_str}",
        f"⬆️ Level up: <i>{prev}</i> → <b>{level}</b>",
        f"🕐 {detected_at}",
    ]
    return "\n".join(lines)
