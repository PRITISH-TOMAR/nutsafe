# Collector

The collector is the "front door" of the product. It receives telemetry from customer services over OTLP and forwards it to the intelligence service.

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
```

- `receivers` — things that accept incoming data.
- `otlp` — the OpenTelemetry Protocol receiver. This is the standard format every OTel agent speaks.
- `protocols` — OTLP supports two transports, both enabled:
  - `grpc` on port `4317` — binary, efficient, what most agents default to.
  - `http` on port `4318` — JSON over HTTP, easier to test with `curl`.
- `0.0.0.0` — listen on all network interfaces so other containers can reach it.

### Exporters

```yaml
exporters:
  debug:
    verbosity: detailed
```

- `exporters` — things that send data somewhere after it is received.
- `debug` — built-in exporter that prints spans to stdout. Used in Phase 0 to confirm the pipe works. Will be replaced with an OTLP forward to `intelligence` in the next phase.
- `verbosity: detailed` — prints the full span content (name, attributes, timestamps), not just a summary.

### Pipeline

```yaml
service:
  pipelines:
    traces:
      receivers: [otlp]
      exporters: [debug]
```

- `service.pipelines` — the wiring. A collector does nothing unless receivers are connected to exporters here.
- `traces` — this is a traces pipeline (spans). `metrics` and `logs` pipelines follow the same pattern and will be added later.
- Reads: take everything arriving through `otlp`, push it out through `debug`.

---

## Ports

| Port | Protocol | Purpose |
|------|----------|---------|
| 4317 | gRPC | OTLP trace/metric/log ingestion |
| 4318 | HTTP | OTLP trace/metric/log ingestion (curl-friendly) |

---

## collector/Dockerfile

### Base image

```dockerfile
FROM otel/opentelemetry-collector-contrib:0.104.0
```

- `FROM` — every Dockerfile starts from a base image. This is the official OpenTelemetry Collector (contrib edition).
- `contrib` vs `core` — the contrib image includes extra receivers/exporters/processors built by the community. We need contrib because later we'll use the `hostmetrics` receiver (CPU/mem/disk). Core wouldn't have it.
- `0.104.0` — pin to a specific version. Never use `latest` in production; a silent upstream update can break your config.

### Copy config

```dockerfile
COPY config/collector.yaml /etc/otelcol-contrib/config.yaml
```

- `COPY` — takes a file from your local machine (build context) and puts it inside the image.
- `config/collector.yaml` — source, relative to the `collector/` directory (where the Dockerfile lives).
- `/etc/otelcol-contrib/config.yaml` — destination inside the container. This is the exact path the collector binary looks for its config by default. You don't need to tell it where the file is — it checks this path automatically.

---

## Phase progression

| Phase | Exporter | Why |
|-------|----------|-----|
| 0 (now) | `debug` | Confirm spans arrive — stdout only |
| 1 (ingest) | `otlp` forwarding to `intelligence:4317` | Feed the intelligence service |
