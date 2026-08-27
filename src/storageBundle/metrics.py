import json

from coreBundle.database import get_clickhouse
from coreBundle.types import MetricPoint


_COLUMNS = ["tenant_id", "service_name", "metric_name", "value", "attributes"]


def insert_metrics(tenant_id: int, points: list[MetricPoint]) -> None:
    if not points:
        return
    clickhouse_client = get_clickhouse()
    rows = [
        [
            tenant_id,
            point.service_name,
            point.metric_name,
            point.value,
            json.dumps(point.attributes),
        ]
        for point in points
    ]
    clickhouse_client.insert("metrics", rows, column_names=_COLUMNS)
