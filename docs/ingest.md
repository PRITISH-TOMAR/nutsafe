# Ingest Layer

The ingest layer is the entry point of the intelligence service. It receives raw OTLP
telemetry from the collector, authenticates the request, parses the payload, writes
spans to ClickHouse, updates EWMA baselines in SQLite, and returns a response.
Detection is decoupled — it runs in the background scheduler, not here.

---

## Where it lives

```
src/
└── ingest/
    ├── parser.py        # OTLP bytes → list[SpanRecord] / list[MetricPoint]
    └── router.py        # POST /v1/traces and POST /v1/metrics endpoints
```

Part of the bundle-wise structure. All ingest concerns live here. No detection logic.

---

## Traces request flow

```
collector
    │
    │  POST /v1/traces
    │  api-key: <tenant-key>
    │  Content-Type: application/x-protobuf
    │  body: ExportTraceServiceRequest (protobuf)
    ▼
ingest/router.py — receive_traces()
    │
    ├─ 1. read api-key header → missing: 422
    │
    ├─ 2. resolve tenant (SQLite)
    │       SELECT id FROM tenants WHERE api_key = ?
    │       not found → 401
    │
    ├─ 3. parser.parse_traces(body)
    │       deserialise protobuf
    │       walk ResourceSpans → ScopeSpans → Span
    │       extract fields into list[SpanRecord]
    │
    ├─ 4. buffer spans → batch write to ClickHouse
    │       spans accumulated in memory, flushed every 500ms or 500 rows
    │       ON DUPLICATE KEY UPDATE IGNORE (safe for duplicate delivery)
    │
    ├─ 5. detect.update_baselines(tenant_id, records)  [SQLite]
    │       per-span EWMA update on baselines table
    │       ewma_ns, ewma_var_ns updated — no anomaly detection here
    │
    └─ 6. return {"stored": N}
```

Detection fires separately via the background scheduler — not inline with ingest.

---

## Metrics request flow

```
collector (Telegraf / OTel hostmetrics)
    │
    │  POST /v1/metrics
    │  api-key: <tenant-key>
    │  Content-Type: application/x-protobuf
    │  body: ExportMetricsServiceRequest (protobuf)
    ▼
ingest/router.py — receive_metrics()
    │
    ├─ 1. validate api-key → tenant_id (same as traces)
    │
    ├─ 2. parser.parse_metrics(body)
    │       deserialise protobuf
    │       walk ResourceMetrics → ScopeMetrics → Metric
    │       only gauge and sum types — histograms skipped
    │       extract into list[MetricPoint]
    │
    ├─ 3. detect.process_metrics(tenant_id, points)  [inline — stateless]
    │       fixed threshold rules — no baseline, no scheduler needed
    │       CPU > 85%, memory > 90%, disk > 85% → Anomaly(resource)
    │       returns list[Anomaly]
    │
    ├─ 4. alerting.send(anomalies)  [only if anomalies detected]
    │
    └─ 5. return {"points": N}
```

Metrics are not written to ClickHouse — threshold-checked immediately and discarded.
Only resource anomalies (if any) are passed to alerting.

---

## Parser — SpanRecord

`parser.py` is pure — no HTTP, no DB, no side effects.

OTLP nests data three levels deep:

```
ExportTraceServiceRequest
└── resource_spans[]
    ├── resource.attributes   ← service.name lives here
    └── scope_spans[]
        └── spans[]           ← individual span data
```

`SpanRecord` fields:

| Field | Source |
|---|---|
| `trace_id` | `span.trace_id.hex()` |
| `span_id` | `span.span_id.hex()` |
| `parent_span_id` | `span.parent_span_id.hex() or None` |
| `service_name` | resource attribute `service.name` |
| `span_name` | `span.name` |
| `target` | first match of: `db.name`, `net.peer.name`, `http.host` |
| `start_time_ns` | `span.start_time_unix_nano` |
| `end_time_ns` | `span.end_time_unix_nano` |
| `status_code` | `span.status.code` → `"OK"` / `"ERROR"` / `"UNSET"` |
| `http_status` | span attribute `http.status_code` (int, or None) |

`target` is the key detection field. It identifies what the span was talking to —
a DB, a downstream service, an external host. Detection keys baselines on
`(span_name, target)`, which localises a break to the failing dependency
rather than just saying "the API is slow."

Priority order: `db.name` > `net.peer.name` > `http.host`. Database spans are
the most specific signal, so they take precedence.

---

## Parser — MetricPoint

`MetricPoint` fields:

| Field | Source |
|---|---|
| `service_name` | resource attribute `service.name` |
| `metric_name` | `metric.name` (e.g. `system.cpu.utilization`) |
| `value` | data point value cast to `float` |
| `attributes` | dict of data point attributes (e.g. `state`, `device`, `mountpoint`) |

Only `gauge` and `sum` types are parsed. Histograms skipped — no single
representative value to threshold against.

---

## Storage

Two stores. Ingest writes to both.

**ClickHouse** — raw telemetry. One row per span, written in batches.
30-day TTL. Columnar — fast for the scheduler's aggregate queries.

**SQLite** — baseline state. One row per `(tenant_id, span_name, target)` key.
Row-level updates on every span via EWMA algorithm.
Also holds tenants, detection_configs, detection_triggers.

```
span arrives → buffer (memory)
                    │
           every 500ms or 500 rows
                    ▼
             ClickHouse INSERT   ← telemetry record (long-term)
             SQLite UPDATE       ← EWMA baseline update (detection state)
```
