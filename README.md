# telegram-bot

> Thin delivery service: receives alert events via HTTP webhook, formats them as Telegram messages, calls the Telegram Bot API with retries + per-chat rate limiting + audit logging.

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

## What this is

The **delivery plane** of a small distributed trading-data platform. Reusable: any upstream producer that emits standardized `AlertEvent` JSON can use this service — not coupled to volume monitoring.

1. **Accept** `POST /alerts` with a typed `AlertEvent` body
2. **Deduplicate** by `alert_id` (LRU cache, 10k entries, 1h TTL)
3. **Format** payload to HTML / MarkdownV2 (escaped, injection-safe)
4. **Rate-limit** per `chat_id` (token bucket: capacity 5, refill 1/sec)
5. **Deliver** via Telegram Bot API with 429-aware retry (parses `retry_after`), exponential backoff on 5xx, no retry on 4xx
6. **Audit** every attempt to ClickHouse table `telegram_audit` (status, attempts, elapsed_ms)

## Architecture

```
[volume-monitor]                       [telegram-bot (this repo)]                    [Telegram API]
[strategy-alerter]  ──HTTP POST────→  ┌──────────────────────────┐  ──HTTPS──→     api.telegram.org
[system-monitor]    /alerts            │ /alerts                  │   (optional      sendMessage
[…]                                    │  ├─ dedup                │    via SOCKS5)
                                       │  ├─ format               │
                                       │  ├─ rate-limit per chat  │
                                       │  ├─ send + retry         │
                                       │  └─ audit ClickHouse     │
                                       └──────────────────────────┘
```

This service **owns**:
- `POST /alerts` accepting normalized alert JSON
- HTML / MarkdownV2 message formatting (using `html.escape`)
- Telegram Bot API calls and retry policy
- Per-chat token-bucket rate limiting
- Audit log persistence

It does **NOT** own:
- Detection or business logic (producers handle that)
- Multi-channel delivery (Discord, Lark, email — separate services)
- Bot command UX (`/start`, `/stop`, ack flow) — out of scope

## Why a separate repo

If every monitor / strategy / system-alerter bundled Telegram credentials and rate-limit logic, you'd have:
- N copies of the bot token (rotation = N deploys)
- Independent rate-limit budgets fighting for Telegram's 30 msg/sec global allowance
- Telegram outage cascading into every producer

Centralizing makes the token a single secret, coordinates rate-limit budgets across producers, and isolates Telegram failures.

## Status

**Production.** Deployed and verified end-to-end. 25 tests covering Telegram client (8), rate limiter (4), formatter (4), alerts endpoint (5), health (4). Real Telegram send takes ~1.5s through SOCKS5.

## Quick Start

### Prerequisites

- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- The numeric `chat_id` to send to (DM your bot, then `GET https://api.telegram.org/bot<TOKEN>/getUpdates`)
- Optional: a SOCKS5 proxy if `api.telegram.org` is blocked from your network
- Docker & Docker Compose
- A reachable ClickHouse (for audit log; can share the one from freqtrade-data-service)

### Run

```bash
git clone https://github.com/shuggg999/telegram-bot.git
cd telegram-bot
cp .env.example .env
# REQUIRED: set TELEGRAM_BOT_TOKEN and TELEGRAM_DEFAULT_CHAT_ID
docker compose up -d
```

By default `docker-compose.yml` joins external network `freqtrade-data-service_default` so `clickhouse-db:9000` resolves. Override if your ClickHouse lives elsewhere.

### Verify

```bash
docker compose ps

curl -s http://localhost:8300/api/v1/health | jq

# Smoke-test send (replace alert_id each run; dedup will skip repeats)
curl -s -X POST http://localhost:8300/alerts \
  -H 'Content-Type: application/json' \
  -d '{
    "schema_version": 1,
    "alert_id": "smoke-test-1",
    "source_service": "manual",
    "format": "html",
    "payload": {"text": "<b>Hello from telegram-bot</b>"}
  }' | jq

# Confirm audit row appeared
docker exec clickhouse-db clickhouse-client --query \
  "SELECT alert_id, status, attempts, elapsed_ms FROM crypto_data.telegram_audit ORDER BY received_at DESC LIMIT 1 FORMAT JSONEachRow"
```

A successful send shows `{"status":"sent","attempts":1,"elapsed_ms":~1500}`.

## API Surface

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/health` | Per-module health: telegram_api, clickhouse_audit, chat_rate_limiter |
| `POST /alerts` | Accept AlertEvent, returns 202 + background-task ack |

### AlertEvent schema

```json
{
  "schema_version": 1,
  "alert_id": "string, unique per event",
  "source_service": "name of producer",
  "format": "html" | "markdown_v2",
  "payload": { "text": "rendered message body" },
  "chat_id": "optional override; otherwise TELEGRAM_DEFAULT_CHAT_ID"
}
```

## Configuration

See `.env.example`. Required and key knobs:

| Variable | Required | Notes |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | **yes** | From [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_DEFAULT_CHAT_ID` | **yes** | Numeric chat id; sent to if `AlertEvent.chat_id` is absent |
| `TELEGRAM_PARSE_MODE` | no | `HTML` (default) or `MarkdownV2` |
| `TELEGRAM_PROXY_URL` | no | e.g. `socks5://host.docker.internal:10808` if api.telegram.org is blocked |
| `CLICKHOUSE_HOST` | no | Default `clickhouse-db`; audit only, leave empty to skip |
| `RATE_LIMIT_CAPACITY` | no | Token-bucket per chat, default 5 |
| `RATE_LIMIT_REFILL_PER_SEC` | no | Refill rate, default 1.0 |

**This is the only repo in the platform that holds `TELEGRAM_BOT_TOKEN`.** Rotation is a single-file change here.

## Project Structure

```
src/
├── main.py                     FastAPI + lifespan + /health
├── config.py                   pydantic-settings
├── api/
│   └── alerts.py               POST /alerts + dedup LRU cache
├── telegram/
│   ├── client.py               sendMessage + 429/5xx/4xx retry
│   └── rate_limiter.py         ChatRateLimiter (per-chat token bucket)
├── formatter/
│   └── volume_alert.py         HTML formatter with html.escape
└── audit/
    └── ch_audit.py             INSERT telegram_audit (idempotent CREATE TABLE)

tests/
└── unit/                       25 tests

openspec/
└── changes/archive/            bootstrap-telegram-bot (initial design)
```

## Retry Policy

- **HTTP 200**: done, audit `status=sent`
- **HTTP 429 (rate limit)**: parse `parameters.retry_after`, sleep, retry up to 5 times
- **HTTP 5xx**: exponential backoff (1s, 2s, 4s), retry up to 3 times
- **HTTP 4xx (except 429)**: no retry, audit `status=client_error`
- **Network error**: same as 5xx
- **Exhausted retries**: audit `status=failed_after_retries`

## Development

```bash
pip install -r requirements.txt
pytest tests/ -q     # 25 tests
```

To run locally without docker, point `CLICKHOUSE_HOST=localhost` and ensure your shell can reach `api.telegram.org` (or set `TELEGRAM_PROXY_URL`).

## Spec-Driven Workflow

[OpenSpec](https://github.com/Fission-AI/OpenSpec) tracks capability changes. See `openspec/changes/archive/`.

## License

[AGPL v3](LICENSE). Running a modified version as a network service requires source publication.

## Sibling Services

- [freqtrade-data-service](https://github.com/shuggg999/freqtrade-data-service) — upstream data plane
- [volume-monitor](https://github.com/shuggg999/volume-monitor) — example producer (volume anomaly alerts)

---

**Disclaimer**: This service delivers messages and does not place trades or hold funds. Cryptocurrency trading is high-risk; signals delivered through this bot are NOT recommendations.
