# Design: bootstrap-telegram-bot

## Context

Telegram delivery was previously embedded inside `candleforge` as `src/alerts/`. The 2026-05-12 architecture brainstorming extracted it to live in its own repo + process so future producers (other monitors, strategy alerts) can share it without bundling credentials.

This change brings the repo from empty skeleton to working delivery service. Bot lifetime is decoupled from any single producer.

## Goals

1. Single HTTP entrypoint: `POST /alerts` accepts standardized alert payload, returns 202 Accepted immediately, performs Telegram call asynchronously
2. Survives Telegram 429 rate limit gracefully — never lose alerts within a 5-retry budget
3. Multi-producer ready — `chat_id` is in the request body so different producers can target different chats
4. Full audit trail in ClickHouse for "did the user actually get this alert?" forensics

## Non-Goals

- Not handling bot commands (out of v0 scope; users still talk to the bot via Telegram, this service is one-way delivery only)
- Not persisting a retry queue across restarts — in-memory bounded queue is enough
- Not implementing per-chat preferences (mute, format, language) — chat-side concerns live in v2

## Decision 1: 202 Accepted + background task vs synchronous send

| Option | Pros | Cons |
|---|---|---|
| **202 + asyncio background task (採用)** | Producer doesn't block on Telegram latency (1-3s typical); freed for next alert | More moving parts; failure-after-202 only observable in audit log |
| 200 + synchronous send | Producer knows immediately whether delivered | Producer waits 1-3s per alert; chains become slow |

Pick 202. Producer's job is to detect, not to babysit Telegram. The audit table is the persistent record.

## Decision 2: Rate limiter = token bucket per chat_id, 1 token / second

Telegram docs: `30 messages/second globally`, `1 message/second per chat`, bursts up to `~20`. We model the per-chat rule conservatively:

```python
# token bucket per chat_id, refill rate 1/sec, capacity 5 (burst)
bucket = TokenBucket(rate=1.0, capacity=5)
await bucket.acquire(chat_id)
```

Global rate-limit handled implicitly by 429-retry path. We don't preemptively spread sends across 30/sec since traffic volume is well below that (target 10 alerts/day).

## Decision 3: 429 handling

When Telegram returns `429 Too Many Requests`:

1. Parse `parameters.retry_after` from response body (in seconds)
2. `asyncio.sleep(retry_after + 0.5)` (extra margin)
3. Retry, max 5 attempts
4. If still 429 after attempt 5 → mark alert `dropped`, audit row contains `status=dropped, last_response_code=429`

Other Telegram errors:
- `400`: do NOT retry (parse error / bad parse_mode / chat blocked); audit `status=failed`
- `401/403`: do NOT retry (token issue / chat block); audit `status=failed`; PagerDuty-equivalent log ERROR
- `5xx`: retry with exponential backoff (tenacity, 3 attempts: 1s/2s/4s); after exhaustion → `failed`

## Decision 4: Alert payload schema (request body for POST /alerts)

```json
{
  "schema_version": 1,
  "alert_id": "<uuid>",                       // idempotency key
  "source_service": "volume-monitor",         // for audit grouping
  "chat_id": "<from env default or override>", // optional in body
  "format": "volume_alert_v1",                 // template selector
  "payload": {
    // free-form, interpreted by the formatter matching `format`
    "exchange": "binance",
    "symbol": "BTC/USDT",
    "tier": "mega",
    "level": "strong",
    "ratio": 12.4,
    "current_avg_volume": 1234567.0,
    "baseline_median_volume": 99500.0,
    "threshold_used": 10.0,
    "detected_at": "2026-05-12T00:05:02+00:00"
  }
}
```

`format` field decouples sender from formatter. To add a new alert type a producer registers `format=my_new_alert_v1` and we add a formatter module here. No producer changes needed for formatting tweaks.

## Decision 5: Idempotency via alert_id

The bot deduplicates by `alert_id`. If the same `alert_id` arrives twice within 1 hour, the second arrival:

- Returns 202 immediately (no rebroadcast)
- Writes audit row with `status=duplicate, attempts=0`

In-memory LRU cache `seen_alert_ids: dict[str, datetime]` with 1-hour expiry. Bound `maxlen=10000` (alerts that small).

Persistence: NOT persisted across restarts (acceptable: producers should not re-fire on a 60-second window).

## Decision 6: ClickHouse audit table — INSERT-only, async, non-blocking

After every Telegram API attempt (regardless of success), background task INSERTs one row into `crypto_data.telegram_audit`. INSERT failures (ClickHouse down) log warn but don't fail the delivery — audit is observability, not gating.

The full transition (POST received → Telegram acked) produces 1+attempts rows; the final row carries `status=sent|failed|dropped|duplicate`, intermediate rows carry `status=queued`.

## Decision 7: Health endpoint shape

`GET /api/v1/health` returns:

```json
{
  "status": "healthy" | "degraded" | "unhealthy",
  "details": {
    "running": true,
    "telegram_api": {
      "status": "ok" | "degraded" | "failed",
      "configured": true,
      "last_success_ts": "...",
      "consecutive_failures": 0
    },
    "clickhouse_audit": {
      "status": "ok" | "degraded" | "failed",
      "connected": true,
      "audit_failure_count": 0
    },
    "chat_rate_limiter": {
      "status": "ok",
      "tracked_chats": 3
    }
  }
}
```

Verdict ladder (same as candleforge & volume-monitor):
- All ok → 200 `healthy`
- Any degraded → 200 `degraded`
- Any failed → 503 `unhealthy`

`jsonable_encoder()` wrapping mandatory (datetime fields present).

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Bot token leak | `.env` only; gitignore; rotation procedure documented in README; jarvis docker secret in env |
| Telegram 429 storm during incident (many alerts at once) | Rate limiter per chat + bounded retry; after 5 attempts → drop & audit; producers must not retry |
| Audit table fills disk | TTL 90 days; volume tiny (10 alerts/day × 5 attempts × 200 bytes = 10 KB/day) |
| Synchronous-looking producers race ahead of Telegram | 202 + background asyncio task; producer gets quick ack |
| Bot down during alert burst | 60-second alert-id LRU prevents duplicate sends on restart-and-retry from producer side |
| Wrong chat_id silently swallows | Telegram 400 response with `chat not found` → audit `status=failed` + ERROR log; ops watches via /health + audit query |

## Open Questions

1. **Should we add HMAC signing on POST /alerts?** Not for v0 (private docker network). Add when bot exposes to internet.
2. **Format templates: jinja2 vs python f-string?** Pick f-string for v0 simplicity; revisit if templates become non-trivial.
3. **Multi-bot mode?** Defer. If/when we need to send to channels via different bots, refactor to `BotClient(token)` pool keyed by `bot_name` in request body.
