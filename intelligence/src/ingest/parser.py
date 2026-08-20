import logging
from dataclasses import dataclass

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)

logger = logging.getLogger(__name__)

_STATUS_MAP = {0: "UNSET", 1: "OK", 2: "ERROR"}

# priority order — first match wins
_TARGET_KEYS = ("db.name", "net.peer.name", "http.host")


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
    status_code: str
    http_status: int | None


def parse(body: bytes) -> list[SpanRecord]:
    request = ExportTraceServiceRequest()
    request.ParseFromString(body)

    records: list[SpanRecord] = []

    for resource_spans in request.resource_spans:
        service_name = _extract_service_name(resource_spans.resource.attributes)
        for scope_spans in resource_spans.scope_spans:
            for span in scope_spans.spans:
                records.append(_to_record(span, service_name))

    logger.debug("parsed %d span(s)", len(records))
    return records


def _extract_service_name(attributes) -> str:
    for attr in attributes:
        if attr.key == "service.name":
            return attr.value.string_value
    return "unknown"


def _to_record(span, service_name: str) -> SpanRecord:
    target_candidates: dict[str, str] = {}
    http_status: int | None = None

    for attr in span.attributes:
        if attr.key in _TARGET_KEYS:
            target_candidates[attr.key] = attr.value.string_value
        elif attr.key == "http.status_code":
            http_status = attr.value.int_value

    target = next(
        (target_candidates[k] for k in _TARGET_KEYS if k in target_candidates),
        None,
    )

    return SpanRecord(
        trace_id=span.trace_id.hex(),
        span_id=span.span_id.hex(),
        parent_span_id=span.parent_span_id.hex() or None,
        service_name=service_name,
        span_name=span.name,
        target=target,
        start_time_ns=span.start_time_unix_nano,
        end_time_ns=span.end_time_unix_nano,
        status_code=_STATUS_MAP.get(span.status.code, "UNSET"),
        http_status=http_status,
    )
