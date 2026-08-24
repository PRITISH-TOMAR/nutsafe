from dataclasses import dataclass, field


@dataclass
class SpanRecord:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    service_name: str
    span_name: str
    target: str | None
    start_time_ns: int
    end_time_ns: int
    status_code: str        # OK | ERROR | UNSET
    http_status: int | None


@dataclass
class MetricPoint:
    service_name: str
    metric_name: str
    value: float
    attributes: dict = field(default_factory=dict)


@dataclass
class Anomaly:
    kind: str               # latency | error_rate | throughput | cascade | resource
    tenant_id: int
    service_name: str
    detail: str
    score: float
    span_name: str | None = None
    target: str | None = None
    duration_ns: int | None = None
    mean_ns: float | None = None
    stddev_ns: float | None = None
