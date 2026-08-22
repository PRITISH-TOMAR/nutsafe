"""
Database connections for both stores:
  - SQLite  : baselines, tenants, detection_configs, detection_triggers
  - ClickHouse : raw telemetry (spans, metrics) — batched writes, columnar reads
"""

import sqlite3
import threading
from contextlib import contextmanager

import clickhouse_connect

from core.config import settings

# ── SQLite ─────────────────────────────────────────────────────────────────────

_sqlite_local = threading.local()


def get_sqlite() -> sqlite3.Connection:
    if not hasattr(_sqlite_local, "conn") or _sqlite_local.conn is None:
        _sqlite_local.conn = sqlite3.connect(
            settings.sqlite.path,
            check_same_thread=False,
            isolation_level=None,   # autocommit; we manage transactions manually
        )
        _sqlite_local.conn.row_factory = sqlite3.Row
        _sqlite_local.conn.execute("PRAGMA journal_mode=WAL")
        _sqlite_local.conn.execute("PRAGMA foreign_keys=ON")
    return _sqlite_local.conn


@contextmanager
def sqlite_tx():
    conn = get_sqlite()
    conn.execute("BEGIN")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


# ── ClickHouse ─────────────────────────────────────────────────────────────────

_ch_client = None
_ch_lock = threading.Lock()


def get_clickhouse():
    global _ch_client
    if _ch_client is None:
        with _ch_lock:
            if _ch_client is None:
                cfg = settings.clickhouse
                _ch_client = clickhouse_connect.get_client(
                    host=cfg.host,
                    port=cfg.port,
                    database=cfg.database,
                    username=cfg.user,
                    password=cfg.password,
                )
    return _ch_client


def close_all() -> None:
    global _ch_client
    if _ch_client is not None:
        _ch_client.close()
        _ch_client = None
