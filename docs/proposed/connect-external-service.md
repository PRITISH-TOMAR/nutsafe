# Connecting an External Service to nutsafe

How to attach any running service to the nutsafe observability platform
so it gets automatic latency, error rate, throughput, and resource monitoring.

**The customer changes zero application code.**
They attach two agents and set env vars. That's it.

---

## Two agents, one API key

| Agent | What it sends | Setup |
|---|---|---|
| OTel agent | Spans, traces, logs — application-level signals | Per service (JVM flag / sidecar / init container) |
| Telegraf | CPU, RAM, disk, network, I/O — machine-level signals | Once per machine |

Both agents send to the same endpoint with the same API key.

---

## Prerequisites

- nutsafe stack running (`docker compose -f deploy/docker-compose.prod.yaml up -d`)
- Collector reachable on port 4318 (HTTP) or 4317 (gRPC)
- A tenant row in SQLite (the seed `test-key-123` works for local testing)

To add a real tenant:
```bash
docker exec deploy-intelligence-1 sqlite3 /data/nutsafe.db \
  "INSERT INTO tenants (name, api_key) VALUES ('my-company', 'my-secret-key');"
```

---

## Node.js / Express

**Step 1 — install OTel packages (dev dependency, not shipped in prod image)**

```bash
npm install \
  @opentelemetry/sdk-node \
  @opentelemetry/auto-instrumentations-node \
  @opentelemetry/exporter-trace-otlp-http \
  @opentelemetry/resources \
  @opentelemetry/semantic-conventions
```

**Step 2 — create `tracing.cjs` in the project root**

Use `.cjs` so it works with ESM apps (`"type":"module"` in package.json).

```js
'use strict';
const { NodeSDK } = require('@opentelemetry/sdk-node');
const { getNodeAutoInstrumentations } = require('@opentelemetry/auto-instrumentations-node');
const { OTLPTraceExporter } = require('@opentelemetry/exporter-trace-otlp-http');
const { Resource } = require('@opentelemetry/resources');
const { SEMRESATTRS_SERVICE_NAME } = require('@opentelemetry/semantic-conventions');

const sdk = new NodeSDK({
  resource: new Resource({
    [SEMRESATTRS_SERVICE_NAME]: process.env.OTEL_SERVICE_NAME || 'my-service',
  }),
  traceExporter: new OTLPTraceExporter({
    url: (process.env.OTEL_EXPORTER_OTLP_ENDPOINT || 'http://localhost:4318') + '/v1/traces',
    headers: { 'api-key': process.env.OTEL_API_KEY || 'test-key-123' },
  }),
  instrumentations: [
    getNodeAutoInstrumentations({
      '@opentelemetry/instrumentation-fs': { enabled: false }, // too noisy
    }),
  ],
});

sdk.start();
process.on('SIGTERM', () => sdk.shutdown());
process.on('SIGINT',  () => sdk.shutdown());
```

**Step 3 — add a traced start script to `package.json`**

```json
"scripts": {
  "start": "node index.js",
  "start:traced": "node --require ./tracing.cjs index.js"
}
```

**Step 4 — set env vars and run**

```bash
OTEL_SERVICE_NAME=my-service \
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 \
OTEL_API_KEY=test-key-123 \
npm run start:traced
```

**What gets auto-instrumented:** HTTP requests, Express routes, MongoDB/Mongoose,
Redis, gRPC, DNS — anything the OTel Node.js contrib plugins cover.

---

## Python / FastAPI / Flask / Django

**Step 1 — install**

```bash
pip install opentelemetry-distro opentelemetry-exporter-otlp
opentelemetry-bootstrap -a install   # detects and installs framework plugins
```

**Step 2 — run with the agent wrapper**

```bash
OTEL_SERVICE_NAME=my-service \
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 \
OTEL_EXPORTER_OTLP_HEADERS="api-key=test-key-123" \
opentelemetry-instrument python app.py
```

No code changes. The `opentelemetry-instrument` command wraps the process
and monkey-patches all supported libraries.

---

## Java (Spring Boot, Quarkus, any JVM)

**Step 1 — download the agent**

```bash
curl -L https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/latest/download/opentelemetry-javaagent.jar \
  -o otel-agent.jar
```

**Step 2 — add the agent JVM flag**

```bash
java \
  -javaagent:otel-agent.jar \
  -DOTEL_SERVICE_NAME=my-service \
  -DOTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 \
  -DOTEL_EXPORTER_OTLP_HEADERS="api-key=test-key-123" \
  -jar my-service.jar
```

Or via env vars if you don't control the start command:
```bash
export JAVA_TOOL_OPTIONS="-javaagent:/path/to/otel-agent.jar"
export OTEL_SERVICE_NAME=my-service
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
export OTEL_EXPORTER_OTLP_HEADERS="api-key=test-key-123"
```

**What gets auto-instrumented:** Spring MVC/WebFlux, JDBC (all DBs),
Hibernate, Kafka, gRPC, HTTP clients, JVM metrics (heap, GC, threads).

---

## Docker Compose service

Add OTel env vars to the service's block — no image rebuild needed:

```yaml
services:
  my-service:
    image: my-service:latest
    environment:
      - OTEL_SERVICE_NAME=my-service
      - OTEL_EXPORTER_OTLP_ENDPOINT=http://nutsafe-collector:4318
      - OTEL_EXPORTER_OTLP_HEADERS=api-key=test-key-123
      # Java only:
      - JAVA_TOOL_OPTIONS=-javaagent:/otel-agent.jar
```

If the service and nutsafe run in **separate Compose files**, connect them
to the same external network:

```yaml
# nutsafe deploy/docker-compose.prod.yaml — add this
networks:
  internal:
    name: nutsafe-internal   # give it a stable name
    driver: bridge

# customer's docker-compose.yml
networks:
  default:
    external:
      name: nutsafe-internal

services:
  my-service:
    environment:
      - OTEL_EXPORTER_OTLP_ENDPOINT=http://collector:4318
```

---

## Kubernetes

Patch the deployment — no image change:

```yaml
spec:
  template:
    spec:
      containers:
        - name: my-service
          env:
            - name: OTEL_SERVICE_NAME
              value: my-service
            - name: OTEL_EXPORTER_OTLP_ENDPOINT
              value: http://<collector-service-ip>:4318
            - name: OTEL_EXPORTER_OTLP_HEADERS
              value: api-key=test-key-123
```

Or use the **OpenTelemetry Operator** — it injects the agent and env vars
via an `Instrumentation` custom resource with zero manifest edits.

---

## Telegraf — machine metrics

Install Telegraf once per machine. Configure it to push to the same endpoint:

```toml
# /etc/telegraf/telegraf.conf

[[inputs.cpu]]
  percpu = false
  totalcpu = true

[[inputs.mem]]

[[inputs.disk]]
  ignore_fs = ["tmpfs", "devtmpfs"]

[[inputs.diskio]]

[[inputs.net]]

[[outputs.http]]
  url = "http://localhost:4318/v1/metrics"
  data_format = "prometheusremotewrite"

  [outputs.http.headers]
    api-key = "test-key-123"
    Content-Type = "application/x-protobuf"
```

This feeds CPU, RAM, disk, and network metrics into the same ingest pipeline.
Resource anomalies (CPU > 85%, memory > 90%, disk > 85%) fire alerts immediately.

---

## What to check after connecting

**1. Are spans arriving?**
```bash
docker logs deploy-intelligence-1 --tail 20
# look for: tenant=X stored N span(s)
```

**2. Is the baseline building?**
```bash
docker exec deploy-intelligence-1 sqlite3 /data/nutsafe.db \
  "SELECT span_name, target, n, round(ewma_ns/1e6) AS ewma_ms FROM baselines;"
```

**3. Are anomalies firing?**
```bash
docker exec deploy-intelligence-1 cat alerts.log
```

Anomalies start firing after **10 spans per (span_name, target) key**.
Under normal traffic this takes seconds to a few minutes.

---

## Verified: psychobeings-backend (Node.js + Express + MongoDB Atlas)

This is the reference integration for this project.

Files added (no source edits):
- `tracing.cjs` — OTel bootstrap loaded via `--require`
- `.env.example` — env var template

Start command:
```bash
npm run start:traced
```

Spans produced automatically:
- `GET /session-form/details` — HTTP handler latency
- `POST /session-form/register` — HTTP handler + MongoDB insert
- `PUT /session-form/:id` — HTTP handler + MongoDB update
- `DELETE /session-form/:id` — HTTP handler + MongoDB delete
- `POST /api/email/sendmessage` — HTTP handler + SMTP call
- MongoDB operations as child spans with `db.name` → baseline target
