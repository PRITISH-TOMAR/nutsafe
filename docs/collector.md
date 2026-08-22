# Collector

The collector is the "front door" of the product. It receives telemetry from customer services over OTLP and forwards it to the intelligence service. It also scrapes host metrics (CPU, memory, disk) from the machine it runs on.

We consume the stock `otel/opentelemetry-collector-contrib` image — no custom Go code. All configuration is in `collector/config/collector.yaml`.

---

## collector/config/collector.yaml

### Receivers

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

  hostmetrics:
    collection_interval: 30s
    scrapers:
      cpu:
      memory:
      filesystem:
```

There are two receivers:

**`otlp`** — accepts incoming telemetry from customer OTel agents.

- `protocols` — OTLP supports two transports, both enabled:
  - `grpc` on port `4317` — binary protobuf, efficient, what most agents default to.
  - `http` on port `4318` — protobuf over HTTP, easier to test with `curl`.
- `0.0.0.0` — listen on all network interfaces so external traffic and other containers can reach it.

**`hostmetrics`** — built-in scraper that reads system metrics from the Docker host OS (the physical or virtual machine running Docker, not individual containers).

The collector container shares the host kernel, so it can read `/proc` and `/sys` to get the host's resource usage.

- `collection_interval: 30s` — polls every 30 seconds.
- `scrapers` — which metric categories to collect:
  - `cpu` — per-state utilization (user, system, idle, etc.) as `system.cpu.utilization`
  - `memory` — per-state utilization (used, free, cached, etc.) as `system.memory.utilization`
  - `filesystem` — per-mount utilization as `system.filesystem.utilization`
- These metric names match the thresholds in `intelligence/src/detect/__init__.py` exactly — that is how the detect layer knows what it is looking at.

---

### Exporters

```yaml
exporters:
  otlphttp:
    endpoint: http://intelligence:4319
    headers:
      api-key: "${COLLECTOR_API_KEY}"
    compression: none
    tls:
      insecure: true
```

- `otlphttp` — sends data downstream over HTTP using the OTLP format (protobuf body).
- `endpoint: http://intelligence:4319` — the intelligence container's address. `intelligence` resolves because both containers are on the same Docker internal network.
- `headers: api-key` — injects an API key into every forwarded request. The intelligence service validates this key against the `tenants` table to resolve which tenant the data belongs to. `${COLLECTOR_API_KEY}` is read from the environment at container start (set in `deploy/.env`).
- `compression: none` — sends the raw protobuf body without gzip. The intelligence parser reads the bytes directly.
- `tls.insecure: true` — skips TLS verification because traffic is on an internal network (no public exposure between collector and intelligence).

---

### Pipelines

```yaml
service:
  pipelines:
    traces:
      receivers: [otlp]
      exporters: [otlphttp]
    metrics:
      receivers: [hostmetrics]
      exporters: [otlphttp]
```

- `service.pipelines` — the wiring. A collector does nothing unless receivers are connected to exporters here.
- `traces` pipeline — takes everything arriving through `otlp` (customer spans) and pushes it to `otlphttp` (intelligence `/v1/traces`).
- `metrics` pipeline — takes host metric data from `hostmetrics` and pushes it to `otlphttp` (intelligence `/v1/metrics`).

The collector itself does not inspect, filter, or transform any data — it is a forwarding layer only. All detection logic lives in intelligence.

---

## Ports

| Port | Protocol | Direction | Purpose |
|------|----------|-----------|---------|
| 4317 | gRPC | inbound from customer | OTLP traces/metrics/logs ingestion |
| 4318 | HTTP | inbound from customer | OTLP traces/metrics/logs ingestion (curl-friendly) |
| — | HTTP | outbound to intelligence:4319 | Forward spans + host metrics |

Ports 4317 and 4318 are the only internet-facing ports in the stack. Intelligence and storage have no public ports.

---

## collector/Dockerfile

```dockerfile
FROM otel/opentelemetry-collector-contrib:0.104.0
COPY config/collector.yaml /etc/otelcol-contrib/config.yaml
```

**`FROM otel/opentelemetry-collector-contrib:0.104.0`**

- `contrib` vs `core` — the contrib image includes extra receivers/exporters/processors built by the community. The `hostmetrics` receiver is a contrib component — it would not be available in the core image.
- `0.104.0` — pinned to a specific version. Never use `latest` in production; a silent upstream update can break your YAML config.

**`COPY config/collector.yaml /etc/otelcol-contrib/config.yaml`**

- Copies our config file into the container at `/etc/otelcol-contrib/config.yaml`. This is the exact path the collector binary looks for by default — no extra flag needed to point it there.

---

## Environment variables

| Variable | Where set | What it does |
|----------|-----------|--------------|
| `COLLECTOR_API_KEY` | `deploy/.env` | Injected as `api-key` header on every forward to intelligence. Must match a row in the `tenants` table. |

The seed value in `.env.example` is `test-key-123`, which matches the seed tenant in `storage/init.sql`.

---

## Data flow summary

```
customer agent  →  collector:4317/4318  →  intelligence:4319/v1/traces
host OS         →  collector hostmetrics →  intelligence:4319/v1/metrics
```

Both pipelines use the same `otlphttp` exporter and the same `api-key` header. The intelligence service routes them to separate handlers (`/v1/traces` and `/v1/metrics`) which call separate parse and detect functions.
