# nutsafe — Implementation Plan

Full phase-by-phase build plan agreed across sessions.
Updated as phases complete or decisions change.

---

## Agreed decisions (cross-cutting)

| Topic | Decision |
|---|---|
| Language | Python |
| Directory structure | Bundle-wise — all bundles directly under `src/`: `ingest/`, `detection/`, `alerting/`, `storage/`, `core/` |
| Detection timing | Scheduler-based (periodic), NOT per-request |
| Baseline updates | At ingest time (EWMA), so scheduler always reads fresh state |
| Metric detection | Stays inline in the ingest router — stateless threshold check, no scheduler needed |
| Detection configs | Per-tenant rows in `detection_configs` table, seeded via SQL for now |
| Alert channels | Webhook + Slack now; PagerDuty, email (SMTP), AWS SNS in Phase 5 |
| Telemetry store | ClickHouse — high write throughput, columnar aggregates, 30-day TTL built in. Batched writes (not row-per-span) |
| State store | SQLite (file inside our service container) — baselines (EWMA), tenants, detection_configs, detection_triggers |
| No Postgres / TimescaleDB | Removed — ClickHouse + SQLite replaces the single Postgres container |
| Edge auth | Caddy forward-auth — `GET /auth?key=<api-key>` on our service, injects `X-Tenant-ID` on success |
| Telemetry retention | 30 days. ClickHouse TTL policy. |
| Multi-tenancy | Row-level isolation — `tenant_id` on every table. ClickHouse partitioned on `(tenant_id, time)` |
| Customer side | OTel agent (spans/traces/logs) + Telegraf (CPU/RAM/disk/network) — zero code changes |
| Reference codebase | OpenObserve studied for inspiration only — not a dependency, not running in our stack |

---

## Phase 0 — Telemetry pipe ✅ DONE

**Goal:** prove that spans flow end to end before writing any intelligence logic.

**Files:**
- `collector/config/collector.yaml` — OTLP in (gRPC 4317, HTTP 4318) → debug exporter (stdout)
- `collector/Dockerfile` — FROM otel/opentelemetry-collector-contrib
- `deploy/docker-compose.prod.yaml` — collector service, internal bridge network
- `deploy/.env.example` — secrets template

**Smoke test:**
```bash
curl -X POST http://localhost:4318/v1/traces \
  -H "api-key: test-key-123" \
  -H "Content-Type: application/json" \
  -d @test-span.json
# collector stdout prints the span
```

---

## Phase 1 — Ingest → Detect → Report (per-request) ✅ DONE

**Goal:** build the intelligence service end to end so the first span triggers an alert.

**Files:**
- `storage/init.sql` — `tenants`, `spans`, `baselines` tables; seed `test-key-123`
- `intelligence/src/ingest/parser.py` — parse protobuf OTLP → `SpanRecord`, `MetricPoint`
- `intelligence/src/ingest/router.py` — `/v1/traces` + `/v1/metrics` FastAPI endpoints; tenant auth
- `intelligence/src/detect/__init__.py` — Welford baselines + 4 anomaly rules
- `intelligence/src/report/__init__.py` — `alerts.log` + webhook + Slack
- `intelligence/src/main.py` — FastAPI app + DB pool lifespan
- `intelligence/src/db.py` — `ThreadedConnectionPool` wrapper, `transaction()` context manager
- `intelligence/src/config.py` — `settings.yaml` loader
- `intelligence/Dockerfile`
- `deploy/docker-compose.prod.yaml` — intelligence + storage services added
- `collector/config/collector.yaml` — debug exporter swapped for OTLP forward to intelligence

**Detection rules (per-request, synchronous):**
1. Latency — per-span duration vs Welford baseline (> 3σ)
2. Error rate — span error rate for a key > 10%
3. Throughput collapse — last-1-min count < 20% of 59-min historical rate
4. Cascade — 2+ anomalous spans in same trace batch

---

## Phase 2 — Scheduler-Based Detection ✅ DONE

**Goal:** decouple detection from the request path. Ingest stores + updates baselines; a background scheduler owns detection.

### Architecture change

```
Before:  request → store → detect → alert   (inline, synchronous)
After:   request → store → update baselines
         scheduler tick → detect → alert     (background, periodic)
```

### New files

**`storage/migrations/001_scheduler.sql`**

Two new tables:

`detection_configs` — one row = one watch job per tenant.

| Column | Type | Notes |
|---|---|---|
| id | SERIAL | PK |
| tenant_id | INTEGER | FK → tenants |
| span_name | TEXT | NULL = watch all span names |
| target | TEXT | NULL = watch all targets |
| algo | TEXT | `welford` \| `ewma` \| `percentile` \| `rcf` |
| schedule_interval_s | INTEGER | default 300 (5 min) |
| detection_window_s | INTEGER | default 300 (look back 5 min) |
| is_active | BOOLEAN | toggle without deleting |

`detection_triggers` — scheduler clock per config.

| Column | Type | Notes |
|---|---|---|
| id | SERIAL | PK |
| config_id | INTEGER | FK → detection_configs, UNIQUE |
| next_run_at | TIMESTAMPTZ | scheduler fires when this passes NOW() |
| last_run_at | TIMESTAMPTZ | audit trail |

Seed a config via SQL:
```sql
INSERT INTO detection_configs (tenant_id, algo, schedule_interval_s, detection_window_s)
VALUES (<tenant_id>, 'welford', 300, 300);
-- Trigger row is auto-created by _ensure_triggers() on next service start.
```

**`intelligence/src/scheduler.py`**

- `run()` — asyncio background task, started in FastAPI lifespan
- `_ensure_triggers()` — on startup, creates trigger rows for any active config missing one
- `_tick()` — runs every 5s via `run_in_executor` (non-blocking):
  - `SELECT ... FOR UPDATE SKIP LOCKED` → fetch due triggers
  - For each: `detect.run_detection()` → `report.send()` if anomalies
  - `UPDATE next_run_at = NOW() + schedule_interval_s`

### Changed files

**`intelligence/src/detect/__init__.py`** — split into two public functions:

`update_baselines(tenant_id, records)` — pure Welford update, no detection, called at ingest time.

`run_detection(tenant_id, span_name_filter, target_filter, window_s)` — called by scheduler:
1. Queries per-`(span_name, target, service_name)` aggregates for the window
2. Reads baselines (read-only, no writes)
3. Applies the same 4 rules against window-level data:
   - Latency: `avg_duration_ns` in window vs historical `mean ± 3σ`
   - Error rate: `error_count / n` in window > 10%
   - Throughput collapse: last-1-min count < 20% of 59-min baseline
   - Cascade: 2+ keys simultaneously anomalous in window

`process_metrics()` — unchanged, stays inline in the ingest router.

**`intelligence/src/ingest/router.py`** — `/v1/traces` now calls `detect.update_baselines()` only; no inline detection; returns `{"stored": N}`.

**`intelligence/src/main.py`** — starts `asyncio.create_task(scheduler.run())` in lifespan; cancels cleanly on shutdown.

### How to apply

```bash
# Run migration
docker exec deploy-storage-1 psql -U nutsafe -d nutsafe \
  -f /migrations/001_scheduler.sql

# Restart intelligence to pick up scheduler
docker compose -f deploy/docker-compose.prod.yaml restart intelligence

# Confirm in logs:
# "registered 1 new detection trigger(s)"
# "scheduler started (poll=5s)"
```

---

## Phase 3 — Algorithm Evaluation & Selection ✅ DONE

**Goal:** decide which detection algorithm(s) to use before committing to Phase 4 implementation.

**Deliverable:** `docs/detection-algos.md`

**Decision:** Replace the Welford σ latency check with EWMA. Keep Welford columns in DB (still used for mean/variance storage). Add `ewma_ns` + `ewma_var_ns` columns to `baselines`. All other rules (error rate, throughput, cascade, resource) unchanged.

### Candidates to evaluate

| Algorithm | Mechanism | Training needed | Best for |
|---|---|---|---|
| **Welford + σ** (current) | Online mean/stddev; flag > 3σ | No | Baseline from span 10; simple |
| **Percentile / IQR** | Historical percentile or interquartile range | No (rolling window) | Non-normal distributions; intuitive thresholds |
| **EWMA** | Exponentially weighted mean; flag residual > threshold | No | Slow drift; trends over time |
| **RCF** (Random Cut Forest) | Ensemble of random trees; anomaly score per point | Yes (training window of historical data) | Complex/multivariate patterns; no distribution assumption |

### Evaluation criteria

1. **Cold-start** — how many spans needed before first meaningful detection?
2. **False-positive rate** — how often does it fire on healthy traffic?
3. **Sensitivity to non-normal distributions** — latency is typically right-skewed (long tail); Welford's stddev can fire spuriously
4. **Drift handling** — can it adapt as a service's baseline shifts gradually over days?
5. **Explainability** — can we say *why* something is anomalous in the alert?
6. **Implementation cost** — library deps, training step, model storage

### Deliverable

A written analysis doc (`docs/detection-algos.md`) comparing the four candidates against nutsafe's signal characteristics, with a recommendation on which to implement in Phase 4. May result in a hybrid (e.g. Welford for latency, EWMA for slow drift).

---

## Phase 4 — Detection Logic (EWMA) ✅ DONE

**Goal:** implement EWMA inside `run_detection()`, replacing the Welford σ latency check.

### If Welford (keep current)
- No structural changes needed — already implemented
- Tune thresholds if Phase 3 analysis shows 3σ causes false positives

### If EWMA
- Add `ewma_ns` and `ewma_var_ns` columns to `baselines` (or a new `baselines_ewma` table)
- Update at ingest: `ewma = α * duration + (1-α) * ewma`
- Detect at schedule time: flag if `duration > ewma + k * sqrt(ewma_var)`

### If Percentile / IQR
- Store a sorted sketch (e.g. T-Digest or reservoir of last N samples) in the DB
- At detection: compute P95/P99; flag if window avg exceeds it

### If RCF
- Add `detection_models` table: `config_id`, `model_blob` (serialized), `trained_at`
- Training job: on first detection run (or scheduled retrain), query last N days of spans for the key, fit RCF, serialize to DB
- Scoring: at each scheduler tick, load model, score each span in window, flag if anomaly score > threshold
- Library: `rrcf` (Python) or `sklearn`-compatible isolation forest as a simpler proxy

### Gating condition
Phase 4 only starts after Phase 3 produces a written recommendation.

---

## Phase 5 — Additional Alert Channels 🔜

**Goal:** extend `report/__init__.py` with PagerDuty, email (SMTP), and AWS SNS.

### Channels

| Channel | Method | Config keys |
|---|---|---|
| Webhook | HTTP POST JSON | `alerts.webhook_url` (exists) |
| Slack | HTTP POST to webhook URL | `alerts.slack_url` (exists) |
| PagerDuty | Events API v2 POST | `alerts.pagerduty_routing_key` |
| Email | SMTP via `smtplib` | `alerts.smtp_host/port/user/password/to` |
| AWS SNS | `boto3` publish | `alerts.sns_topic_arn` (uses ambient AWS creds) |

Each channel only fires if its config key is non-empty — same pattern as existing webhook/Slack.

### Changes
- `intelligence/config/settings.yaml` + `intelligence/src/config.py` — add new keys
- `intelligence/src/report/__init__.py` — add `_send_pagerduty()`, `_send_email()`, `_send_sns()`
- `deploy/.env.example` — document new env vars
- `intelligence/requirements.txt` — add `boto3` (SNS only; email uses stdlib)

---

## Phase 6 — Edge TLS + Multi-tenant Auth 🔜

**Goal:** make the ingest endpoint publicly reachable with HTTPS and validate API keys at the edge before any traffic hits intelligence.

### Files
- `edge/Caddyfile` — TLS termination (auto HTTPS via Let's Encrypt) + reverse proxy to collector on port 4318
- `deploy/docker-compose.prod.yaml` — add `edge` service; only edge gets public ports (80, 443)

### Auth options (decide in this phase)

**Option A — Edge validates key, injects tenant header**
Caddy calls a small auth endpoint on intelligence (e.g. `GET /auth?key=X`), on success injects `X-Tenant-ID` header, forwards to collector. No collector changes.

**Option B — Custom collector auth extension**
Write a minimal Go auth extension for the collector that validates the `api-key` header against the tenants table. Needed only if Caddy forward-auth adds too much latency at scale.

Recommendation: start with Option A (no Go required, consistent with "boring config" principle). Switch to Option B only if benchmarks show Caddy forward-auth is the bottleneck.

### Smoke test after Phase 6
```bash
curl -X POST https://ingest.ourproduct.com/v1/traces \
  -H "api-key: <tenant-key>" \
  -H "Content-Type: application/json" \
  -d @test-span.json
```

---

## Roadmap summary

| Phase | Description | Status |
|---|---|---|
| 0 | Telemetry pipe — collector receives and prints spans | ✅ Done |
| 1 | Ingest → detect → report, per-request, synchronous | ✅ Done |
| 2 | Scheduler-based detection — background worker, window queries | ✅ Done |
| 3 | Algorithm evaluation — Welford vs EWMA vs Percentile vs RCF | ✅ Done |
| 4 | Detection logic — EWMA latency check | ✅ Done |
| 5 | Additional alert channels — PagerDuty, email, SNS | ⬜ Later |
| 6 | Edge TLS + multi-tenant auth at Caddy | ⬜ Later |
