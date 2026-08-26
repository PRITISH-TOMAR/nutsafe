from google.protobuf import json_format

from opentelemetry.proto.collector.trace.v1 import trace_service_pb2 as trace_svc
from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2 as metrics_svc

from coreBundle.types import SpanRecord, MetricPoint, SpanStatus


def _attrs(kv_list) -> dict:
    out = {}
    for kv in kv_list:
        v = kv.value
        if v.HasField("string_value"):
            out[kv.key] = v.string_value
        elif v.HasField("int_value"):
            out[kv.key] = v.int_value
        elif v.HasField("double_value"):
            out[kv.key] = v.double_value
        elif v.HasField("bool_value"):
            out[kv.key] = v.bool_value
    return out


def _target(attrs: dict) -> str | None:
    return (
        attrs.get("db.name")
        or attrs.get("net.peer.name")
        or attrs.get("peer.service")
        or attrs.get("server.address")
        or attrs.get("http.host")
    )


def _decode(body: bytes, content_type: str, msg):
    if "json" in content_type:
        json_format.Parse(body, msg)
    else:
        msg.ParseFromString(body)
    return msg


def parse_traces(body: bytes, content_type: str) -> list[SpanRecord]:
    req = _decode(body, content_type, trace_svc.ExportTraceServiceRequest())
    records = []
    for rs in req.resource_spans:
        res = _attrs(rs.resource.attributes)
        service = res.get("service.name", "unknown")
        for ss in rs.scope_spans:
            for span in ss.spans:
                attrs = _attrs(span.attributes)
                http_raw = attrs.get("http.status_code") or attrs.get("http.response.status_code")
                records.append(SpanRecord(
                    trace_id=span.trace_id.hex(),
                    span_id=span.span_id.hex(),
                    parent_span_id=span.parent_span_id.hex() if span.parent_span_id else None,
                    service_name=service,
                    span_name=span.name,
                    target=_target(attrs),
                    start_time_ns=span.start_time_unix_nano,
                    end_time_ns=span.end_time_unix_nano,
                    status_code=SpanStatus.from_otlp(span.status.code),
                    http_status=int(http_raw) if http_raw is not None else None,
                ))
    return records


def parse_metrics(body: bytes, content_type: str) -> list[MetricPoint]:
    req = _decode(body, content_type, metrics_svc.ExportMetricsServiceRequest())
    points = []
    for rm in req.resource_metrics:
        res = _attrs(rm.resource.attributes)
        service = res.get("service.name", "unknown")
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                data_points = _data_points(metric)
                for dp, value in data_points:
                    points.append(MetricPoint(
                        service_name=service,
                        metric_name=metric.name,
                        value=value,
                        attributes={**res, **_attrs(dp.attributes)},
                    ))
    return points


def _data_points(metric) -> list[tuple]:
    if metric.HasField("gauge"):
        dps = metric.gauge.data_points
        return [(dp, _number_value(dp)) for dp in dps]
    if metric.HasField("sum"):
        dps = metric.sum.data_points
        return [(dp, _number_value(dp)) for dp in dps]
    if metric.HasField("histogram"):
        dps = metric.histogram.data_points
        return [(dp, dp.sum / dp.count if dp.count else 0.0) for dp in dps]
    return []


def _number_value(dp) -> float:
    if dp.HasField("as_double"):
        return dp.as_double
    return float(dp.as_int)
