# nutsafe — High-Level Application Flow

Full end-to-end journey of telemetry through the system, from customer service to alert and investigation.

---

## Architecture

```
  CUSTOMER ENVIRONMENT                          OUR PRODUCT
  ┌───────────────────────────────┐            ┌──────────────────────────────────────────────┐
  │  Their service                │            │                                              │
  │  + OTel agent (spans/logs)    │  OTLP      │  edge (TLS termination)                      │
  │  + Telegraf  (CPU/RAM/disk)   │ ─────────► │    │                                         │
  │                               │  + api-key │    ▼                                         │
  │  3 env vars (OTel only):      │            │  collector                                   │
  │    OTEL_SERVICE_NAME          │            │    │  receives OTLP + Telegraf metrics        │
  │    OTLP_ENDPOINT              │            │    │  forwards to our service                 │
  │    OTLP_HEADERS=api-key=...   │            │    ▼                                         │
  └───────────────────────────────┘            │  our service                                 │
                                               │    │                                         │
                                               │    ├── src/ingest/     parse + auth          │
                                               │    ├── src/storage/    write telemetry       │
                                               │    ├── src/detection/  baselines + rules     │
                                               │    └── src/alerting/   fire alerts           │
                                               │                                              │
                                               └──────────────────────────────────────────────┘
                                                           │
                                                           ▼
                                               Slack / Webhook / PagerDuty / Email / SNS
```

---

## Customer side — two agents, no code changes

| Agent | What it sends | How |
|---|---|---|
| OTel agent | Spans, traces, logs | Attached at launch (JVM flag / sidecar / init container) |
| Telegraf | CPU, RAM, disk, network, I/O | One instance per machine, runs as a background agent |

The customer sets three env vars for the OTel agent:

```
OTEL_SERVICE_NAME=orders-api
OTEL_EXPORTER_OTLP_ENDPOINT=https://ingest.ourproduct.com
OTEL_EXPORTER_OTLP_HEADERS=api-key=<tenant-key>
```

Telegraf is configured once per machine to push metrics to the same endpoint.
No source code edits. No per-language integration. Anything that speaks OTLP or Prometheus remote_write plugs in.

---

## Directory structure

```
src/
  ingest/       ← receive and parse incoming telemetry (OTLP spans, Telegraf metrics)
  detection/    ← baselines (EWMA state), anomaly rules, scheduler
  alerting/     ← alert channels: Slack, webhook, PagerDuty, email, SNS
  storage/      ← schema, queries, migrations — unified telemetry + state store
  core/         ← shared: config, DB connection, auth, types
  main          ← wires all bundles together, starts the service
```

Each bundle owns its logic end-to-end. No bundle reaches into another bundle's internals — only through defined interfaces.

---

## Step-by-step flow

### 1. Edge — TLS termination

- Terminates HTTPS. Auto-renews TLS certificates.
- Reverse-proxies traffic to the collector on the internal network.
- Validates `api-key` header before forwarding (forward-auth to our service).

Only internet-facing component. All other services have no public ports.

---

### 2. Collector — receive and forward

- Receives OTLP (spans, metrics, logs) from OTel agents and Telegraf.
- Forwards to our service unchanged on the internal network.
- No custom logic here — pure forwarding.

---

### 3. src/ingest — parse and authenticate

On each incoming request:

1. **Auth** — reads `api-key`, resolves `tenant_id` from the tenants store. Rejects unknown keys.
2. **Parse spans** — OTLP protobuf → structured span records:
   - `service_name`, `span_name`, `target` (DB host / HTTP peer), `start_time_ns`, `end_time_ns`, `status_code`
3. **Parse metrics** — Telegraf / OTLP metrics → metric point records:
   - `metric_name`, `value`, `service_name`, `attributes`
4. Hands records to `storage` and `detection`.

---

### 4. src/storage — write telemetry (unified store)

**One write serves two consumers:**

```
span record arrives
        │
        ▼
  write to telemetry store   ──────────────────► investigation
  (long-term, queryable)                         "what happened and when?"
        │
        ▼
  update baseline state      ──────────────────► detection
  (EWMA per span key)                            "is something broken now?"
```

- **Telemetry store (ClickHouse)** — raw spans and metrics. Batched writes for high throughput. Columnar aggregates for fast scheduler queries. 30-day TTL built in.
- **Baseline store (SQLite)** — EWMA state per `(tenant_id, span_name, target)`. Row-level updates on every ingest. Lives as a file inside our service container.
- **Config store (SQLite)** — tenants, detection configs, scheduler triggers. Same SQLite file.

---

### 5. src/detection — baselines and rules

Runs as a background scheduler, decoupled from the ingest path.

**Baseline update (at ingest time):**
```
ewma_ns     = α * duration + (1-α) * ewma_ns
ewma_var_ns = (1-α) * (ewma_var_ns + α * (duration - ewma_ns)²)
α = 0.10
```

**Scheduler tick (every N seconds per detection config):**

1. Queries the telemetry store for window aggregates — `avg_duration_ns`, `n`, `error_count` per span key in the last `detection_window_s` seconds.
2. Reads EWMA baseline state from the baseline store.
3. Applies four rules:

| Rule | Logic | Fires when |
|---|---|---|
| Latency | `avg_window > ewma + 3 * sqrt(ewma_var)` | Window avg drifts above EWMA threshold |
| Error rate | `error_count / n > 10%` | More than 1 in 10 spans erroring |
| Throughput collapse | `last_1min_count / avg_59min_rate < 20%` | Traffic drops to <20% of normal |
| Cascade | 2+ span keys simultaneously anomalous | Failure spreading across dependencies |

4. Reschedules trigger: `next_run_at = now + schedule_interval_s`.

---

### 6. src/alerting — fire alerts

For each anomaly produced by detection:

| Channel | Fires when |
|---|---|
| `alerts.log` | Always |
| Webhook | `ALERT_WEBHOOK_URL` configured |
| Slack | `ALERT_SLACK_URL` configured |
| PagerDuty | `ALERT_PAGERDUTY_KEY` configured |
| Email (SMTP) | `ALERT_SMTP_HOST` configured |
| AWS SNS | `ALERT_SNS_TOPIC_ARN` configured |

Each channel is opt-in via config. The service runs without any channel set.

Alert message example:
```
ANOMALY  latency
service: orders-api
span:    SELECT orders → orders-db-primary
avg:     94.1ms  |  EWMA: 8.0ms ±1.2ms  |  score: 71.8x
window:  300s    |  tenant: acme-corp
```

---

## The two questions answered from one write

```
  "Is something broken now?"          "What happened and when?"
           │                                    │
           │                                    │
           ▼                                    ▼
    detection scheduler              engineer queries telemetry store
    reads window aggregates          for the time window around the alert
    from telemetry store             sees raw spans, metrics, surrounding context
           │
           ▼
    fires alert to on-call
```

Single ingest write. No dual pipelines.

---

## Container map

| Container | Role | Public ports |
|---|---|---|
| Edge | TLS termination, Caddy forward-auth (`/auth` on our service) | 80, 443 |
| Collector | OTLP + Telegraf receiver, forwards internally | None |
| Our service (Python) | ingest + detection + alerting + SQLite state file | None |
| ClickHouse | Raw telemetry store — spans, metrics. 30-day TTL | None |

**No Postgres. No TimescaleDB.**
- Raw telemetry → ClickHouse (high write throughput, columnar aggregates, built-in TTL)
- Baselines + config + scheduler state → SQLite (file inside our service container, row-level updates)

---

## Roadmap status

| Phase | Description | Status |
|---|---|---|
| 0 | Telemetry pipe — collector receives + prints spans | Done |
| 1 | Ingest → detect → report, synchronous | Done |
| 2 | Scheduler-based detection | Done |
| 3 | Algorithm evaluation — selected EWMA | Done |
| 4 | EWMA latency detection | Done |
| 5 | Additional alert channels (PagerDuty, Email, SNS) | Next |
| 6 | Edge TLS + Caddy | Planned |
| 7 | Migrate to bundle-wise src/ structure | Planned |
| 8 | Storage layer decision + unified telemetry store | Planned |
