# Ordered list of ClickHouse table definitions. Each entry: (table_name, ddl).
# Append new entries at the bottom. Never edit or remove existing entries.

CH_TABLES: list[tuple[str, str]] = [
    (
        "spans",
        """
        CREATE TABLE IF NOT EXISTS spans (
            tenant_id       UInt32,
            trace_id        String,
            span_id         String,
            parent_span_id  Nullable(String),
            service_name    String,
            span_name       String,
            target          Nullable(String),
            start_time_ns   UInt64,
            end_time_ns     UInt64,
            duration_ns     UInt64 MATERIALIZED end_time_ns - start_time_ns,
            status_code     LowCardinality(String),
            http_status     Nullable(Int32),
            received_at     DateTime DEFAULT now()
        ) ENGINE = MergeTree()
        ORDER BY (tenant_id, service_name, span_name, start_time_ns)
        TTL received_at + INTERVAL 30 DAY
        """,
    ),
    (
        "metrics",
        """
        CREATE TABLE IF NOT EXISTS metrics (
            tenant_id     UInt32,
            service_name  String,
            metric_name   LowCardinality(String),
            value         Float64,
            attributes    String,
            received_at   DateTime DEFAULT now()
        ) ENGINE = MergeTree()
        ORDER BY (tenant_id, metric_name, received_at)
        TTL received_at + INTERVAL 30 DAY
        """,
    ),
]
