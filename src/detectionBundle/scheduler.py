"""
Detection scheduler — runs in a background thread.

Every `settings.detection.interval_seconds` it:
  1. Fetches all active tenant IDs from SQLite.
  2. Runs all four detection rules against ClickHouse for each tenant.
  3. Logs detected anomalies (alerting bundle will dispatch them in Phase 5).
"""

import logging
import threading

from coreBundle.config import settings
from coreBundle.database import get_sqlite
from coreBundle.types import Anomaly
from detectionBundle import rules

logger = logging.getLogger(__name__)

_stop_event = threading.Event()
_thread: threading.Thread | None = None


def start() -> None:
    global _thread
    _stop_event.clear()
    _thread = threading.Thread(target=_run, daemon=True, name="detection-scheduler")
    _thread.start()
    logger.info(
        "Detection scheduler started (interval=%ds, window=%dm)",
        settings.detection.interval_seconds,
        settings.detection.window_minutes,
    )


def stop() -> None:
    _stop_event.set()
    if _thread is not None:
        _thread.join(timeout=5)
    logger.info("Detection scheduler stopped")


def _run() -> None:
    # Wait for the first interval before ticking so startup noise settles.
    while not _stop_event.wait(timeout=settings.detection.interval_seconds):
        _tick()


def _tick() -> None:
    try:
        tenant_ids = _active_tenant_ids()
        for tenant_id in tenant_ids:
            _run_tenant(tenant_id)
    except Exception:
        logger.exception("Detection tick failed")


def _active_tenant_ids() -> list[int]:
    conn = get_sqlite()
    rows = conn.execute(
        "SELECT tenant_id FROM tenants WHERE active = 1"
    ).fetchall()
    return [row["tenant_id"] for row in rows]


def _run_tenant(tenant_id: int) -> None:
    window = settings.detection.window_minutes
    min_spans = settings.detection.min_spans

    latency_anomalies = rules.check_latency(tenant_id, window, min_spans)
    error_anomalies = rules.check_error_rate(tenant_id, window, min_spans)
    throughput_anomalies = rules.check_throughput(tenant_id)

    point_anomalies = latency_anomalies + error_anomalies + throughput_anomalies
    cascade_anomalies = rules.check_cascade(tenant_id, point_anomalies)

    all_anomalies = point_anomalies + cascade_anomalies

    if all_anomalies:
        for anomaly in all_anomalies:
            logger.warning(
                "ANOMALY  tenant=%d  kind=%s  score=%.2f  detail=%s",
                anomaly.tenant_id,
                anomaly.kind,
                anomaly.score,
                anomaly.detail,
            )
        # Phase 5: alerting bundle will be called here to dispatch to
        # Slack / webhook / PagerDuty / email / SNS.
    else:
        logger.debug("Detection tick clean for tenant=%d", tenant_id)
