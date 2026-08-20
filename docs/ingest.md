# Ingest Layer

The ingest layer is the entry point of the intelligence service. It receives raw OTLP
telemetry from the collector, authenticates the request, parses the spans, and writes
them to storage.

---

## Where it lives

```
intelligence/
├── config/
│   └── settings.yaml        # server port, db pool size, log level
├── requirements.txt         # python dependencies
└── src/
    ├── config.py            # loads settings.yaml + DATABASE_URL env var
    ├── db.py                # postgres connection pool
    ├── main.py              # fastapi app + lifespan (startup/shutdown)
    └── ingest/
        ├── __init__.py      # empty — marks ingest/ as a python package
        ├── parser.py        # otlp bytes → list[SpanRecord]
        └── router.py        # POST /v1/traces — auth, parse, store
```

---

## Request flow

```
collector
    │
    │  POST /v1/traces
    │  api-key: <tenant-key>
    │  Content-Type: application/x-protobuf
    │  body: ExportTraceServiceRequest (protobuf)
    ▼
router.py — receive_traces()
    │
    ├─ 1. read api-key header
    │       missing → 422 (FastAPI auto-validates)
    │
    ├─ 2. _resolve_tenant(conn, api_key)
    │       SELECT id FROM tenants WHERE api_key = ?
    │       not found → 401
    │
    ├─ 3. parser.parse(body)
    │       deserialise protobuf
    │       walk ResourceSpans → ScopeSpans → Span
    │       extract fields into list[SpanRecord]
    │
    ├─ 4. _insert_spans(cur, tenant_id, records)
    │       executemany INSERT into spans table
    │       ON CONFLICT DO NOTHING (safe for duplicate delivery)
    │
    └─ 5. return {"stored": N}
```

---

## Config layer

`settings.yaml` holds non-secret structural config (ports, pool sizes, log level).
`DATABASE_URL` is the only secret — read from the environment.

`config.py` merges both at import time into a single typed `Settings` object.
Every other module imports from `config` — no `os.environ` calls scattered around.

```
deploy/.env          → DATABASE_URL (secret, never committed)
intelligence/config/settings.yaml → server.port, db.min_connections, logging.level
        ↓
intelligence/src/config.py  → settings object
        ↓
db.py, router.py, main.py
```

---

## DB layer

`db.py` owns the connection pool. It is shared across all requests and all future
pipeline stages (detect, report).

Two context managers are exposed:

| Context manager | When to use |
|---|---|
| `db.connection()` | read-only queries, or when you manage commit yourself |
| `db.transaction()` | any write — auto-commits on success, auto-rolls back on error |

The pool is initialised once at startup with a retry loop (up to 10 attempts, 2s apart)
to handle the race between intelligence starting and postgres being ready.

---

## Parser

`parser.py` is a pure function module — no HTTP, no DB, no side effects.

Input: raw protobuf bytes from the request body
Output: `list[SpanRecord]`

OTLP nests data three levels deep:

```
ExportTraceServiceRequest
└── resource_spans[]
    ├── resource.attributes   ← service.name lives here (shared across spans)
    └── scope_spans[]
        └── spans[]           ← individual span data
```

`SpanRecord` is a dataclass with these fields:

| Field | Source |
|---|---|
| `trace_id` | `span.trace_id.hex()` |
| `span_id` | `span.span_id.hex()` |
| `parent_span_id` | `span.parent_span_id.hex() or None` |
| `service_name` | resource attribute `service.name` |
| `span_name` | `span.name` |
| `target` | first of: `db.name`, `net.peer.name`, `http.host` |
| `start_time_ns` | `span.start_time_unix_nano` |
| `end_time_ns` | `span.end_time_unix_nano` |
| `status_code` | `span.status.code` → `"OK"` / `"ERROR"` / `"UNSET"` |
| `http_status` | span attribute `http.status_code` |

`target` is the most important field for detection. It identifies what the span was
talking to (a DB, a downstream service, an external host). The detect layer keys
baselines on `(span_name, target)` — this is what lets us localise a break to the
failing dependency rather than just saying "the API is slow".

---

## Storage

Two tables are written to by ingest:

**`tenants`** — looked up on every request to resolve `api-key → tenant_id`.
This is the auth and data-isolation boundary.

**`spans`** — one row per ingested span. The detect layer reads from here to
build and update baselines.

```sql
-- what ingest writes per span
INSERT INTO spans (
    tenant_id, trace_id, span_id, parent_span_id,
    service_name, span_name, target,
    start_time_ns, end_time_ns,
    status_code, http_status
)
ON CONFLICT (tenant_id, trace_id, span_id) DO NOTHING
```

`duration_ns` is a generated column — Postgres computes it automatically from
`end_time_ns - start_time_ns`. Ingest never writes it directly.

---

## Smoke test result

```
span sent:    SELECT orders on orders-db, duration ~945ms, service orders-api
response:     {"stored": 1}

db query:     SELECT span_name, service_name, target, duration_ns, status_code FROM spans;
result:       SELECT orders | orders-api | orders-db | 945001536 | UNSET
```

---

## What comes next

The detect layer reads from `spans`, compares each new span's `duration_ns` against
the baseline for that `(span_name, target)`, and writes anomalies. See `docs/detect.md`
once that layer is built.
