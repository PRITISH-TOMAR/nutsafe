-- storage/init.sql
-- Runs once when the Postgres container first starts.
-- Creates three tables: tenants, spans, baselines.

-- ─────────────────────────────────────────────
-- TENANTS
-- Maps an API key to a named customer account.
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tenants (
    id         SERIAL PRIMARY KEY,
    name       TEXT NOT NULL,
    api_key    TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed one test tenant so local smoke tests work out of the box.
INSERT INTO tenants (name, api_key)
VALUES ('local-test', 'test-key-123')
ON CONFLICT (api_key) DO NOTHING;

-- ─────────────────────────────────────────────
-- SPANS
-- One row per ingested span.
-- Stores the raw feature values we care about.
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS spans (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       INTEGER     NOT NULL REFERENCES tenants(id),
    received_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- identity
    trace_id        TEXT        NOT NULL,
    span_id         TEXT        NOT NULL,
    parent_span_id  TEXT,
    service_name    TEXT        NOT NULL,
    span_name       TEXT        NOT NULL,

    -- the "target" of this span: DB host, HTTP peer, etc.
    -- NULL for spans that have no outbound target (e.g. top-level HTTP handler)
    target          TEXT,

    -- timing (nanoseconds, as OTLP sends them)
    start_time_ns   BIGINT      NOT NULL,
    end_time_ns     BIGINT      NOT NULL,
    duration_ns     BIGINT GENERATED ALWAYS AS (end_time_ns - start_time_ns) STORED,

    -- outcome
    status_code     TEXT,        -- OK | ERROR | UNSET
    http_status     INTEGER,     -- e.g. 200, 500 — NULL if not HTTP

    UNIQUE (tenant_id, trace_id, span_id)
);

-- ─────────────────────────────────────────────
-- BASELINES
-- Rolling statistics per (tenant, span_name, target).
-- Updated by the detect layer after each new span.
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS baselines (
    id              SERIAL PRIMARY KEY,
    tenant_id       INTEGER     NOT NULL REFERENCES tenants(id),

    -- the key: what operation on what target
    span_name       TEXT        NOT NULL,
    target          TEXT        NOT NULL DEFAULT '',

    -- Welford online algorithm state for duration_ns
    n               BIGINT      NOT NULL DEFAULT 0,   -- sample count
    mean_ns         DOUBLE PRECISION NOT NULL DEFAULT 0,
    m2_ns           DOUBLE PRECISION NOT NULL DEFAULT 0,  -- sum of squared deviations

    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (tenant_id, span_name, target)
);
