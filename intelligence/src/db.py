import logging
from contextlib import contextmanager
from typing import Generator

import psycopg2
from psycopg2.extensions import connection as PgConnection
from psycopg2.pool import ThreadedConnectionPool

from config import settings

logger = logging.getLogger(__name__)

_pool: ThreadedConnectionPool | None = None


def init_pool() -> None:
    global _pool
    _pool = ThreadedConnectionPool(
        minconn=settings.db.min_connections,
        maxconn=settings.db.max_connections,
        dsn=settings.db.url,
    )
    logger.info(
        "DB pool initialised (min=%d, max=%d)",
        settings.db.min_connections,
        settings.db.max_connections,
    )


def close_pool() -> None:
    if _pool:
        _pool.closeall()
        logger.info("DB pool closed")


@contextmanager
def connection() -> Generator[PgConnection, None, None]:
    conn = _pool.getconn()
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


@contextmanager
def transaction() -> Generator[PgConnection, None, None]:
    with connection() as conn:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
