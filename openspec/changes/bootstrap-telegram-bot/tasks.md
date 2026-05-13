# Tasks: bootstrap-telegram-bot

## 1. Pre-flight Checks

- [ ] 1.1 Confirm `volume-monitor` bootstrap progress — it doesn't have to be fully implemented but its alert payload schema must be locked (per its `bootstrap-volume-monitor/design.md` Decision 6)
- [ ] 1.2 Confirm `candleforge` is the owner of ClickHouse schema migrations; a migration script for the `telegram_audit` table will live there
- [ ] 1.3 jarvis port 8300 is free
- [ ] 1.4 You have a Telegram bot token + chat_id available for test (use the same bot the existing `src/alerts/` module uses in candleforge today)

## 2. Scaffold Project Files

- [ ] 2.1 `pyproject.toml` with project metadata + black/ruff config
- [ ] 2.2 `environment.yml` listing conda deps: python 3.11, fastapi, uvicorn, httpx, aiochclient, tenacity, pydantic-settings, loguru, pytest, pytest-asyncio
- [ ] 2.3 `requirements.txt` mirror
- [ ] 2.4 `.env.example` with: TELEGRAM_BOT_TOKEN, TELEGRAM_DEFAULT_CHAT_ID, TELEGRAM_PARSE_MODE, TELEGRAM_PROXY_URL, CLICKHOUSE_HOST/PORT/USER/PASSWORD/DATABASE, RATE_LIMIT_CAPACITY, RATE_LIMIT_REFILL_PER_SEC, LOG_LEVEL
- [ ] 2.5 `tests/conftest.py` with shared fixtures (httpx_mock for Telegram, aiochclient mock for ClickHouse)

## 3. ClickHouse Audit Migration (cross-repo coordination)

- [ ] 3.1 In `candleforge` repo, write `scripts/migrations/2026-XX-XX-add-telegram-audit-table.sql` containing the `CREATE TABLE crypto_data.telegram_audit ...` DDL (per proposal.md schema)
- [ ] 3.2 Push to candleforge gitea, run on jarvis: `docker exec -i clickhouse-db clickhouse-client < scripts/migrations/2026-XX-XX-add-telegram-audit-table.sql`
- [ ] 3.3 Verify on jarvis: `docker exec clickhouse-db clickhouse-client -q "SHOW CREATE TABLE crypto_data.telegram_audit"` shows expected schema with TTL

## 4. Telegram Client (TDD)

- [ ] 4.1 Write `src/telegram/client.py`: `TelegramClient(token, parse_mode, proxy_url)` with `async send_message(chat_id, text)` returning `(success: bool, response: dict, attempts: int)`. Handle 429 by parsing `parameters.retry_after`, sleep, retry max 5 times. Handle 5xx via tenacity (3 attempts, exp backoff). Handle 4xx (except 429): no retry, return failure
- [ ] 4.2 Write `tests/unit/test_telegram_client.py`: success path, 429 once then success, 429 5 times → dropped, 400 → failed (no retry), 500 once → retry → success, network timeout → retry
- [ ] 4.3 Tests green using httpx_mock

## 5. Rate Limiter (TDD)

- [ ] 5.1 Write `src/telegram/rate_limiter.py`: `ChatRateLimiter(rate=1.0, capacity=5)` token bucket per chat_id with async `acquire(chat_id)`. Internal `dict[str, TokenBucket]` lazily created
- [ ] 5.2 Write `tests/unit/test_rate_limiter.py`: 5 immediate acquires for one chat all succeed instantly; 6th waits ~1s; different chat_ids do not contend; bucket refills over time
- [ ] 5.3 Tests green using `freezegun` to control clock

## 6. Alert Formatter (TDD)

- [ ] 6.1 Write `src/formatter/volume_alert.py`: `format_volume_alert(payload: dict) -> str` returning HTML message body matching the existing `candleforge src/alerts/formatter.py` output (so users see no visible difference). Include emoji + symbol + tier + ratio + level
- [ ] 6.2 Write `tests/unit/test_volume_alert_formatter.py`: known input produces expected HTML; missing optional fields handled; HTML-special characters escaped
- [ ] 6.3 Tests green

## 7. ClickHouse Audit Client

- [ ] 7.1 Write `src/audit/ch_audit.py`: `AuditWriter(ch_client)` with `async write_row(alert_id, source, chat_id, status, attempts, last_response_code, last_response_body, elapsed_ms)` doing INSERT into `telegram_audit`. Wrap in try/except — failures log warn, do NOT raise
- [ ] 7.2 Write `tests/unit/test_ch_audit.py`: success writes one row; ClickHouse down → warn log + return without raising
- [ ] 7.3 Tests green

## 8. POST /alerts Endpoint

- [ ] 8.1 Write `src/api/alerts.py`: `POST /alerts` accepts AlertEvent pydantic model (matches proposal.md schema), returns 202 Accepted with `{"alert_id": ..., "received_at": ...}`. Schedules background asyncio task that: dedupes via LRU → rate-limits → format → send → audit
- [ ] 8.2 Validate `schema_version == 1`; return 400 with explicit message on mismatch
- [ ] 8.3 Write `tests/unit/test_alerts_endpoint.py`: happy path (202 + background task triggers Telegram), duplicate alert_id within 1h returns 202 without Telegram call, wrong schema_version returns 400, malformed JSON returns 400
- [ ] 8.4 Tests green

## 9. FastAPI App + Lifespan + /health

- [ ] 9.1 Write `src/main.py`: FastAPI app with lifespan wiring (`TelegramClient`, `ChatRateLimiter`, `AuditWriter`)
- [ ] 9.2 Implement `/api/v1/health` with sub-probes per design.md Decision 7
- [ ] 9.3 Each sub-probe wrapped with `_safe_subprobe()` (copy pattern from candleforge)
- [ ] 9.4 `jsonable_encoder()` on JSONResponse body
- [ ] 9.5 Verdict ladder: any failed → 503; only degraded → 200; healthy → 200
- [ ] 9.6 Write `tests/unit/test_health_endpoint.py` covering: all ok → 200; telegram_api 429-storm degraded → 200; clickhouse audit down → degraded 200; bot token missing → failed 503; datetime serialization works
- [ ] 9.7 Tests green

## 10. End-to-End Integration Test

- [ ] 10.1 Write `tests/integration/test_alerts_e2e.py`: starts TestClient + httpx_mock Telegram + aiochclient mock → POST a sample alert → assert (a) HTTP 202; (b) Telegram client received `sendMessage` with expected text within 5s; (c) one ClickHouse audit row with `status=sent`
- [ ] 10.2 Repeat with `alert_id` reused → assert NO second Telegram call, NEW audit row with `status=duplicate`
- [ ] 10.3 Tests green

## 11. Docker Wiring

- [ ] 11.1 Finalize Dockerfile (conda env activation + uvicorn entrypoint)
- [ ] 11.2 Finalize docker-compose.yml (joins candleforge network, env vars, port 8300)
- [ ] 11.3 `docker compose build` local succeeds

## 12. jarvis Deploy

- [ ] 12.1 Push this repo to gitea (new repo `telegram-bot`)
- [ ] 12.2 ssh jarvis: clone next to `volume-monitor`
- [ ] 12.3 cp .env.example .env; fill `TELEGRAM_BOT_TOKEN` (re-use existing bot token from candleforge .env), `TELEGRAM_DEFAULT_CHAT_ID`, CH creds (read-only fine for audit table SELECT, write to telegram_audit)
- [ ] 12.4 jarvis `docker compose up -d` → container healthy
- [ ] 12.5 curl `http://192.168.110.51:8300/api/v1/health` returns 200 + all sub-probes ok
- [ ] 12.6 Smoke: `curl -X POST -H 'Content-Type: application/json' -d @sample_alert.json http://192.168.110.51:8300/alerts` returns 202, Telegram receives message, audit table has new row

## 13. Switchover from candleforge Internal Notifier

> **Coordination point with volume-monitor**: the moment volume-monitor starts POSTing alerts to telegram-bot, alerts will be sent twice (candleforge internal notifier + telegram-bot). De-dupe via `(symbol, level, hour_bucket)` key on Telegram side OR temporarily disable candleforge notifier.

- [ ] 13.1 Decide switchover strategy with user (recommended: stop candleforge internal notifier first, then enable volume-monitor → telegram-bot path)
- [ ] 13.2 If chosen "stop candleforge notifier first": set `ALERTS_DRY_RUN=true` in candleforge .env on jarvis and restart candleforge
- [ ] 13.3 Verify volume-monitor → telegram-bot pipeline produces same Telegram messages as before (manual eye-check + audit table grep)

## 14. Archive

- [ ] 14.1 Push final commit to gitea
- [ ] 14.2 `openspec archive bootstrap-telegram-bot --yes`
- [ ] 14.3 Trigger follow-up: candleforge opens `cleanup-business-modules` change to delete `src/alerts/`
