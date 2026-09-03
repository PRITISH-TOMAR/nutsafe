"""
Four detection rules. Each rule queries ClickHouse for a recent window,
compares against SQLite baselines (where needed), and returns a list of Anomaly.

Rules:
  latency          avg_window > ewma + 3 * sqrt(ewma_var)
  error_rate       error_count / total > 10%
  throughput       last_1min_count / avg_per_min_prior_59 < 20%
  cascade          2+ distinct (span_name, target) keys anomalous simultaneously
"""

import math
import logging

from coreBundle.database import get_clickhouse, get_sqlite
from coreBundle.types import Anomaly

logger = logging.getLogger(__name__)


def check_latency(tenant_id: int, window_minutes: int, min_spans: int) -> list[Anomaly]:
    """Fire when the window average duration exceeds ewma + 3 * stddev."""
    ch = get_clickhouse()
    sqlite_conn = get_sqlite()

    result = ch.query(
        """
        SELECT
            service_name,
            span_name,
            COALESCE(target, '') AS target_key,
            avg(duration_ns)     AS avg_ns,
            count()              AS n
        FROM spans
        WHERE tenant_id  = {tid:UInt32}
          AND received_at >= now() - INTERVAL {window:Int32} MINUTE
        GROUP BY service_name, span_name, target_key
        HAVING n >= {min_spans:Int32}
        """,
        parameters={"tid": tenant_id, "window": window_minutes, "min_spans": min_spans},
    )

    anomalies: list[Anomaly] = []
    for row in result.named_results():
        baseline = sqlite_conn.execute(
            "SELECT ewma_ns, ewma_var_ns FROM baselines"
            " WHERE tenant_id = ? AND span_name = ? AND target = ?",
            (tenant_id, row["span_name"], row["target_key"]),
        ).fetchone()
        if baseline is None:
            continue

        ewma_ns = baseline["ewma_ns"]
        ewma_var_ns = baseline["ewma_var_ns"]
        stddev_ns = math.sqrt(ewma_var_ns) if ewma_var_ns > 0 else 0.0
        threshold = ewma_ns + 3 * stddev_ns
        avg_ns = row["avg_ns"]

        # Only fire if the threshold is meaningful (need at least some variance).
        if avg_ns > threshold and stddev_ns > 0:
            score = (avg_ns - ewma_ns) / stddev_ns
            anomalies.append(Anomaly(
                kind="latency",
                tenant_id=tenant_id,
                service_name=row["service_name"],
                detail=(
                    f"{row['span_name']} -> {row['target_key'] or '(none)'}: "
                    f"avg {avg_ns / 1e6:.1f}ms vs baseline {ewma_ns / 1e6:.1f}ms "
                    f"(threshold {threshold / 1e6:.1f}ms)"
                ),
                score=round(score, 2),
                span_name=row["span_name"],
                target=row["target_key"] or None,
                mean_ns=ewma_ns,
                stddev_ns=stddev_ns,
            ))

    return anomalies


def check_error_rate(tenant_id: int, window_minutes: int, min_spans: int) -> list[Anomaly]:
    """Fire when more than 10% of spans in the window are in ERROR status."""
    ch = get_clickhouse()

    result = ch.query(
        """
        SELECT
            service_name,
            span_name,
            COALESCE(target, '') AS target_key,
            countIf(status_code = 'ERROR') AS error_count,
            count()                         AS total
        FROM spans
        WHERE tenant_id  = {tid:UInt32}
          AND received_at >= now() - INTERVAL {window:Int32} MINUTE
        GROUP BY service_name, span_name, target_key
        HAVING total >= {min_spans:Int32}
        """,
        parameters={"tid": tenant_id, "window": window_minutes, "min_spans": min_spans},
    )

    anomalies: list[Anomaly] = []
    for row in result.named_results():
        error_rate = row["error_count"] / row["total"]
        if error_rate > 0.10:
            anomalies.append(Anomaly(
                kind="error_rate",
                tenant_id=tenant_id,
                service_name=row["service_name"],
                detail=(
                    f"{row['span_name']} -> {row['target_key'] or '(none)'}: "
                    f"{error_rate * 100:.1f}% errors "
                    f"({row['error_count']}/{row['total']} spans)"
                ),
                score=round(error_rate, 4),
                span_name=row["span_name"],
                target=row["target_key"] or None,
            ))

    return anomalies


def check_throughput(tenant_id: int) -> list[Anomaly]:
    """Fire when traffic in the last 1 minute is less than 20% of the prior 59-minute rate."""
    ch = get_clickhouse()

    result = ch.query(
        """
        SELECT
            service_name,
            countIf(received_at >= now() - INTERVAL 1 MINUTE)  AS last_1min,
            countIf(received_at <  now() - INTERVAL 1 MINUTE) / 59.0 AS avg_per_min
        FROM spans
        WHERE tenant_id  = {tid:UInt32}
          AND received_at >= now() - INTERVAL 60 MINUTE
        GROUP BY service_name
        """,
        parameters={"tid": tenant_id},
    )

    anomalies: list[Anomaly] = []
    for row in result.named_results():
        avg_per_min = row["avg_per_min"]
        last_1min = row["last_1min"]
        # Skip services with no prior baseline traffic.
        if avg_per_min <= 0:
            continue
        ratio = last_1min / avg_per_min
        if ratio < 0.20:
            anomalies.append(Anomaly(
                kind="throughput",
                tenant_id=tenant_id,
                service_name=row["service_name"],
                detail=(
                    f"{row['service_name']}: {last_1min} spans last minute vs "
                    f"{avg_per_min:.1f}/min average ({ratio * 100:.1f}% of normal)"
                ),
                score=round(ratio, 4),
            ))

    return anomalies


def check_cascade(tenant_id: int, anomalies: list[Anomaly]) -> list[Anomaly]:
    """Fire when 2+ distinct (span_name, target) keys are anomalous simultaneously."""
    keys = {
        (a.span_name, a.target)
        for a in anomalies
        if a.span_name is not None
    }
    if len(keys) < 2:
        return []

    services = ", ".join(sorted({a.service_name for a in anomalies if a.service_name}))
    key_summary = ", ".join(
        f"{name}->{tgt or '(none)'}" for name, tgt in sorted(keys)
    )
    return [Anomaly(
        kind="cascade",
        tenant_id=tenant_id,
        service_name=services,
        detail=f"Cascade across {len(keys)} span keys: {key_summary}",
        score=float(len(keys)),
    )]
