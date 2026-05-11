## ADDED Requirements

### Requirement: HTTP Alert Receive Endpoint

The service SHALL expose `POST /alerts` accepting `application/json` body with the schema documented in `design.md` Decision 4. The endpoint SHALL:

1. Validate `schema_version == 1` (return HTTP 400 otherwise with explicit error)
2. Validate required fields: `alert_id` (uuid string), `source_service` (string), `format` (string matching a known formatter), `payload` (object)
3. Schedule the actual Telegram send as an asyncio background task
4. Return HTTP 202 Accepted with body `{"alert_id": "<echoed>", "received_at": "<iso8601>"}`

The endpoint SHALL NOT block on Telegram API latency. Producers receive ack within 100 ms regardless of Telegram conditions.

#### Scenario: Standard alert accepted

- **WHEN** a producer POSTs a valid VolumeAlert payload
- **THEN** response is HTTP 202 within 100 ms with body containing `alert_id` and `received_at`; Telegram receives the message within 5 seconds; one ClickHouse audit row is written with `status=sent`

#### Scenario: Wrong schema_version rejected immediately

- **WHEN** a producer POSTs `{"schema_version": 2, ...}`
- **THEN** response is HTTP 400 with body `{"error": "unsupported schema_version: 2", "supported": [1]}`; no background task scheduled, no audit row

#### Scenario: Malformed JSON rejected

- **WHEN** a producer POSTs body that is not valid JSON
- **THEN** FastAPI returns HTTP 422; no background task; no audit row

### Requirement: Idempotency by alert_id

The service SHALL maintain an in-memory LRU cache of seen `alert_id` values for at least 1 hour with capacity ≥ 10000. If the same `alert_id` arrives twice within the cache window, the second arrival SHALL:

- Return HTTP 202 immediately (no Telegram call)
- Write one audit row with `status: "duplicate"` and `attempts: 0`

The cache MUST NOT be persisted across restarts (acceptable for v0; producers should not repeatedly fire the same alert_id within a 60-second window).

#### Scenario: Duplicate alert_id within 1 hour

- **WHEN** the same `alert_id` arrives twice within 5 minutes
- **THEN** the first call sends to Telegram and audits `status=sent`; the second call returns 202 without Telegram call and audits `status=duplicate`

#### Scenario: Same alert_id after restart

- **WHEN** the service restarts and the same `alert_id` re-arrives 1 minute later
- **THEN** the cache is empty after restart; the alert is treated as new; Telegram receives a duplicate message (accepted v0 trade-off)

### Requirement: Per-Chat Token Bucket Rate Limiting

For each `chat_id`, the service SHALL enforce a token bucket with default refill rate 1 token/second and capacity 5 tokens. Sends SHALL await an available token before calling Telegram. Different `chat_id` values SHALL NOT contend with each other (each has its own bucket).

#### Scenario: Burst of 5 alerts to one chat

- **WHEN** 5 alerts to the same `chat_id` arrive within 100 ms
- **THEN** all 5 are sent to Telegram instantly (consuming the burst capacity); subsequent alerts to that chat are paced at 1/sec

#### Scenario: Different chats parallel

- **WHEN** 5 alerts to chat A and 5 alerts to chat B arrive within 100 ms
- **THEN** all 10 are sent to Telegram in parallel (different chats, separate buckets)

### Requirement: Telegram 429 Rate Limit Handling

When Telegram returns HTTP 429, the service SHALL parse `parameters.retry_after` from the response body, sleep `retry_after + 0.5` seconds, and retry up to 5 times. After 5 failed attempts the alert SHALL be considered `dropped` — audit row written with `status: "dropped", last_response_code: 429, attempts: 5`. The producer SHALL NOT be informed of the drop (the 202 was already returned).

#### Scenario: Single 429 then success

- **WHEN** Telegram returns 429 with `retry_after: 2` once, then 200 on retry
- **THEN** the message is delivered after ~2.5s; audit row has `status=sent, attempts=2`

#### Scenario: Persistent 429 hits drop limit

- **WHEN** Telegram returns 429 on all 5 attempts
- **THEN** total elapsed time ~ sum of all retry_after values; audit row has `status=dropped, attempts=5`; ERROR log entry with full alert payload

### Requirement: Telegram Non-Recoverable Error Handling

The service SHALL distinguish recoverable from non-recoverable Telegram API errors and apply distinct policies:

- **HTTP 400 / 401 / 403** (request structure or auth wrong): the service MUST NOT retry, MUST write an audit row with `status: "failed"` and `last_response_code` + `last_response_body` (truncated to 2000 chars), and MUST log at ERROR level.
- **HTTP 5xx** (transient server errors): the service MUST retry with exponential backoff (1s/2s/4s, max 3 attempts) before declaring the alert `failed`.

#### Scenario: Bad parse_mode rejected (400)

- **WHEN** Telegram returns 400 "Bad Request: can't parse entities"
- **THEN** no retry; audit `status=failed, attempts=1`; ERROR log

#### Scenario: Bot blocked by user (403)

- **WHEN** Telegram returns 403 "Forbidden: bot was blocked by the user"
- **THEN** no retry; audit `status=failed, attempts=1`; ERROR log

#### Scenario: Telegram 500 then 200

- **WHEN** Telegram returns 500 once then 200 on retry
- **THEN** message delivered; audit `status=sent, attempts=2`

### Requirement: ClickHouse Audit Logging

For every delivery attempt the service SHALL INSERT one row into `crypto_data.telegram_audit` with the following columns: `received_at`, `alert_id`, `source_service`, `chat_id`, `status` (one of `queued`/`sent`/`failed`/`dropped`/`duplicate`), `attempts`, `last_response_code`, `last_response_body`, `last_attempt_at`, `elapsed_ms`. Audit INSERT failures (ClickHouse down) MUST log at WARN level and MUST NOT raise into the delivery flow.

#### Scenario: Successful send produces final audit row

- **WHEN** a Telegram send succeeds on first try
- **THEN** exactly one row exists in `telegram_audit` for that `alert_id` with `status=sent, attempts=1`

#### Scenario: ClickHouse down does not block delivery

- **WHEN** ClickHouse audit INSERT fails (connection refused) but Telegram send succeeded
- **THEN** the alert is delivered to the user; the service logs WARN about audit failure; `clickhouse_audit` sub-probe of `/health` reports `status=degraded`

### Requirement: Health Endpoint Conventions

The service SHALL expose `GET /api/v1/health` with the same conventions as data-service and volume-monitor:

- Body shape: `{status: "healthy" | "degraded" | "unhealthy", details: {running, telegram_api, clickhouse_audit, chat_rate_limiter}}`
- HTTP status: 200 for healthy + degraded, 503 for unhealthy
- Each sub-probe wrapped in `_safe_subprobe()` so exceptions surface as `{status: failed, reason}` not 500
- All datetime values pass through `jsonable_encoder()` before JSONResponse construction

#### Scenario: All systems ok

- **WHEN** Telegram bot token configured + reachable, ClickHouse reachable, no rate-limiter contention
- **THEN** HTTP 200, body `status: healthy`, all sub-probes `status: ok`

#### Scenario: ClickHouse audit degraded but Telegram fine

- **WHEN** ClickHouse is unreachable but the bot can still send Telegram messages
- **THEN** HTTP 200, body `status: degraded`, `details.clickhouse_audit.status: degraded`, alerts continue to deliver

#### Scenario: Bot token missing

- **WHEN** `TELEGRAM_BOT_TOKEN` env var is empty / unset
- **THEN** HTTP 503, body `status: unhealthy`, `details.telegram_api.status: failed, reason: "TELEGRAM_BOT_TOKEN not set"`
