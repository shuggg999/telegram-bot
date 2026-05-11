# telegram-bot

Thin delivery service: receives alert events via HTTP webhook, formats them as Telegram messages, calls the Telegram Bot API. Reusable across multiple upstream alert sources (volume-monitor, future strategy-alert services, system-monitor, etc.).

## Role

```
[volume-monitor] ──HTTP POST /alerts──→ [telegram-bot (this repo)] ──HTTPS──→ Telegram API
[other-monitor] ─────┘                  ├─ format message
                                        ├─ rate-limit per chat
                                        ├─ retry on 429
                                        └─ audit ClickHouse table
```

**This service owns:**
- HTTP `POST /alerts` endpoint accepting standardized alert event JSON
- Message formatting (HTML / MarkdownV2 template per alert type)
- Telegram Bot API calls (`sendMessage`, retry on 429 with `parameters.retry_after`)
- Per-`chat_id` rate limiting (Telegram allows 30 msg/sec global, 1/sec per chat)
- Audit log of every delivery attempt

**This service does NOT own:**
- Detection / business logic (volume-monitor or other producers)
- Multi-channel delivery (Discord, Lark — a separate bot service)
- Bot command handling (`/start`, `/stop`, ack flow) — deferred to v2

## Why a separate repo

The 2026-05-12 architecture brainstorming (see `freqtrade-data-service/docs/superpowers/specs/2026-05-12-data-service-split-architecture-design.md`) decided to extract Telegram delivery so:

1. Other future monitors / strategies can send alerts without each one bundling Telegram credentials
2. Telegram bot token rotation is centralized
3. Rate limits are coordinated (a single producer doing 30 msg/sec would otherwise starve other producers)
4. Outage of Telegram doesn't bring down the producing services

## Status

🚧 **Skeleton repo, not yet implemented.**

First OpenSpec change: `openspec/changes/bootstrap-telegram-bot/`. Implementation is gated on `volume-monitor` producing alerts (which itself is gated on data-service NATS event bus).

## Repo Layout (planned)

```
telegram-bot/
├── README.md
├── Dockerfile
├── docker-compose.yml             join data-service network
├── pyproject.toml
├── .env.example
├── src/
│   ├── main.py                    FastAPI app + lifespan + /health
│   ├── api.py                     POST /alerts
│   ├── formatter/
│   │   └── volume_alert.py        format volume-anomaly events to HTML
│   ├── telegram/
│   │   └── client.py              wraps sendMessage + 429 retry
│   └── audit/
│       └── ch_audit.py            INSERT into telegram_audit ClickHouse table
└── openspec/
    └── changes/bootstrap-telegram-bot/
```

## Deployment

Deploys to jarvis as a sibling container to volume-monitor in the same `freqtrade-data-service_default` docker network. Exposes port 8300 (per +300 offset convention).
