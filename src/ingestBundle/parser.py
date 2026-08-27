from google.protobuf import json_format

from opentelemetry.proto.collector.trace.v1 import trace_service_pb2 as trace_svc
from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2 as metrics_svc

from coreBundle.types import SpanRecord, MetricPoint, SpanStatus


def _parse_attributes(key_value_list) -> dict:
    result = {}
    for key_value in key_value_list:
        attribute_value = key_value.value
        if attribute_value.HasField("string_value"):
            result[key_value.key] = attribute_value.string_value
        elif attribute_value.HasField("int_value"):
            result[key_value.key] = attribute_value.int_value
        elif attribute_value.HasField("double_value"):
            result[key_value.key] = attribute_value.double_value
        elif attribute_value.HasField("bool_value"):
            result[key_value.key] = attribute_value.bool_value
    return result


def _extract_target(attributes: dict) -> str | None:
    return (
        attributes.get("db.name")
        or attributes.get("net.peer.name")
        or attributes.get("peer.service")
        or attributes.get("server.address")
        or attributes.get("http.host")
    )


def _decode_request(body: bytes, content_type: str, message):
    if "json" in content_type:
        json_format.Parse(body, message)
    else:
        message.ParseFromString(body)
    return message


def parse_traces(body: bytes, content_type: str) -> list[SpanRecord]:
    request = _decode_request(body, content_type, trace_svc.ExportTraceServiceRequest())
    records = []
    for resource_span in request.resource_spans:
        resource_attributes = _parse_attributes(resource_span.resource.attributes)
        service = resource_attributes.get("service.name", "unknown")
        for scope_span in resource_span.scope_spans:
            for span in scope_span.spans:
                span_attributes = _parse_attributes(span.attributes)
                http_status_raw = (
                    span_attributes.get("http.status_code")
                    or span_attributes.get("http.response.status_code")
                )
                records.append(SpanRecord(
                    trace_id=span.trace_id.hex(),
                    span_id=span.span_id.hex(),
                    parent_span_id=span.parent_span_id.hex() if span.parent_span_id else None,
                    service_name=service,
                    span_name=span.name,
                    target=_extract_target(span_attributes),
                    start_time_ns=span.start_time_unix_nano,
                    end_time_ns=span.end_time_unix_nano,
                    status_code=SpanStatus.from_otlp(span.status.code),
                    http_status=int(http_status_raw) if http_status_raw is not None else None,
                ))
    return records


def parse_metrics(body: bytes, content_type: str) -> list[MetricPoint]:
    request = _decode_request(body, content_type, metrics_svc.ExportMetricsServiceRequest())
    points = []
    for resource_metric in request.resource_metrics:
        resource_attributes = _parse_attributes(resource_metric.resource.attributes)
        service = resource_attributes.get("service.name", "unknown")
        for scope_metric in resource_metric.scope_metrics:
            for metric in scope_metric.metrics:
                data_points = _extract_data_points(metric)
                for data_point, value in data_points:
                    points.append(MetricPoint(
                        service_name=service,
                        metric_name=metric.name,
                        value=value,
                        attributes={**resource_attributes, **_parse_attributes(data_point.attributes)},
                    ))
    return points


def _extract_data_points(metric) -> list[tuple]:
    if metric.HasField("gauge"):
        data_points = metric.gauge.data_points
        return [(data_point, _extract_number_value(data_point)) for data_point in data_points]
    if metric.HasField("sum"):
        data_points = metric.sum.data_points
        return [(data_point, _extract_number_value(data_point)) for data_point in data_points]
    if metric.HasField("histogram"):
        data_points = metric.histogram.data_points
        return [(data_point, data_point.sum / data_point.count if data_point.count else 0.0) for data_point in data_points]
    return []


def _extract_number_value(data_point) -> float:
    if data_point.HasField("as_double"):
        return data_point.as_double
    return float(data_point.as_int)
