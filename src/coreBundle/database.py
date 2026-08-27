"""
Database connections for both stores:
  - SQLite  : baselines, tenants, detection_configs, detection_triggers
  - ClickHouse : raw telemetry (spans, metrics) — batched writes, columnar reads
"""

import sqlite3
import threading
from contextlib import contextmanager

import clickhouse_connect

from coreBundle.config import settings

# ── SQLite ─────────────────────────────────────────────────────────────────────

_sqlite_local = threading.local()


def get_sqlite() -> sqlite3.Connection:
    if not hasattr(_sqlite_local, "connection") or _sqlite_local.connection is None:
        _sqlite_local.connection = sqlite3.connect(
            settings.sqlite.path,
            check_same_thread=False,
            isolation_level=None,   # autocommit; we manage transactions manually
        )
        _sqlite_local.connection.row_factory = sqlite3.Row
        _sqlite_local.connection.execute("PRAGMA journal_mode=WAL")
        _sqlite_local.connection.execute("PRAGMA foreign_keys=ON")
    return _sqlite_local.connection


@contextmanager
def sqlite_tx():
    connection = get_sqlite()
    connection.execute("BEGIN")
    try:
        yield connection
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise


# ── ClickHouse ─────────────────────────────────────────────────────────────────

_clickhouse_client = None
_clickhouse_lock = threading.Lock()


def get_clickhouse():
    global _clickhouse_client
    if _clickhouse_client is None:
        with _clickhouse_lock:
            if _clickhouse_client is None:
                clickhouse_config = settings.clickhouse
                _clickhouse_client = clickhouse_connect.get_client(
                    host=clickhouse_config.host,
                    port=clickhouse_config.port,
                    database=clickhouse_config.database,
                    username=clickhouse_config.user,
                    password=clickhouse_config.password,
                )
    return _clickhouse_client


def close_all() -> None:
    global _clickhouse_client
    if _clickhouse_client is not None:
        _clickhouse_client.close()
        _clickhouse_client = None
