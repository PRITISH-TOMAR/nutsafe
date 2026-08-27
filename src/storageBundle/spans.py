from coreBundle.database import get_clickhouse
from coreBundle.types import SpanRecord


_COLUMNS = [
    "tenant_id", "trace_id", "span_id", "parent_span_id",
    "service_name", "span_name", "target",
    "start_time_ns", "end_time_ns",
    "status_code", "http_status",
]


def insert_spans(tenant_id: int, records: list[SpanRecord]) -> None:
    if not records:
        return
    clickhouse_client = get_clickhouse()
    rows = [
        [
            tenant_id,
            record.trace_id,
            record.span_id,
            record.parent_span_id,
            record.service_name,
            record.span_name,
            record.target,
            record.start_time_ns,
            record.end_time_ns,
            record.status_code.value,
            record.http_status,
        ]
        for record in records
    ]
    clickhouse_client.insert("spans", rows, column_names=_COLUMNS)
