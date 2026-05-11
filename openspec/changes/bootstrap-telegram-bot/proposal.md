## Why

Brand-new repo. Reason it exists: the architecture brainstorming on 2026-05-12 (see `freqtrade-data-service/docs/superpowers/specs/2026-05-12-data-service-split-architecture-design.md`) extracted Telegram delivery out of `freqtrade-data-service` into its own service so that:

1. Future alert producers (multiple monitor services, strategy alerts, system health alerts) can share a single Telegram bot token + rate-limiting envelope
2. Telegram API outages do not cascade into producer services
3. The producer side stays pure (no HTTP / no credential), the bot side stays pure (no business logic)

This first OpenSpec change covers full bootstrap of the repo: HTTP `POST /alerts` endpoint, alert formatting, Telegram Bot API client with 429 handling, ClickHouse audit logging, FastAPI app with `/api/v1/health`.

## What Changes

### New capability `telegram-delivery`

- HTTP `POST /alerts` endpoint accepting standardized alert event JSON (schema matches volume-monitor's alert event payload, see volume-monitor `bootstrap-volume-monitor/design.md` Decision 6)
- Format alert event into HTML message body using per-`alert_type` template
- Call Telegram `sendMessage` API; on 429, parse `retry_after` from response and retry up to 5 times with sleep
- Per-`chat_id` token bucket rate limiter (1 msg/sec/chat per Telegram docs)
- Audit every delivery attempt to ClickHouse `telegram_audit` table (idempotent INSERT, includes alert_id, attempt count, response status, timestamps)
- `/api/v1/health` with sub-probes for `telegram_api`, `clickhouse_audit`, `chat_rate_limiter`

### Repo structure

```
telegram-bot/
├── src/
│   ├── main.py                    FastAPI app + lifespan
│   ├── config.py                  pydantic-settings
│   ├── api/
│   │   └── alerts.py              POST /alerts handler
│   ├── formatter/
│   │   ├── __init__.py
│   │   └── volume_alert.py        format VolumeAlert → HTML
│   ├── telegram/
│   │   ├── client.py              sendMessage wrapper with retry + 429 handling
│   │   └── rate_limiter.py        per-chat_id token bucket
│   └── audit/
│       └── ch_audit.py            ClickHouse audit table client
├── tests/
│   ├── unit/
│   └── integration/
├── Dockerfile
├── docker-compose.yml
├── environment.yml
├── requirements.txt
├── .env.example
└── openspec/changes/bootstrap-telegram-bot/
```

### Dependencies

- `fastapi` + `uvicorn`
- `httpx` (Telegram API + sync of /alerts handling)
- `aiochclient` (ClickHouse audit insert)
- `tenacity` (retry)
- `pydantic-settings`
- `loguru`

### Soft dep on data-service

The ClickHouse `telegram_audit` table SHALL be created via a migration script committed to `freqtrade-data-service/scripts/migrations/` (since data-service owns DB schema). Schema:

```sql
CREATE TABLE IF NOT EXISTS crypto_data.telegram_audit (
    received_at DateTime64(3),
    alert_id String,
    source_service LowCardinality(String),
    chat_id String,
    status LowCardinality(String),      -- queued/sent/failed/dropped
    attempts UInt8,
    last_response_code Nullable(UInt16),
    last_response_body Nullable(String),
    last_attempt_at DateTime64(3),
    elapsed_ms UInt32
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(received_at)
ORDER BY (received_at, alert_id)
TTL toDateTime(received_at) + INTERVAL 90 DAY;
```

### Not in scope (deferred)

- Bot command handling (`/start`, `/stop`, `/mute symbol`) — v2
- Inline button callbacks (acknowledge alert) — v2
- Multi-bot deployment (one bot per channel) — v2
- HMAC signature verification on incoming webhook (will add when bot is exposed beyond the docker private network)

## Impact

**New code**: ~600 LOC + ~25 unit tests.

**Existing code touched**: ZERO in this repo (brand new). Data-service repo gets ONE migration script (`scripts/migrations/2026-XX-XX-add-telegram-audit-table.sql`).

**Deployment**: Adds a 5th container to jarvis docker-compose stack (`telegram-bot`), exposed on port 8300.

**Risk**: Low.
- Bot token leakage → use `.env` only, never commit
- Telegram API outages → 429 handled gracefully, alerts dropped after 5 retries with explicit log; producers must NOT retry (they already gave up)
- ClickHouse audit failure → log warn but continue delivery (audit is observability, not gating)
