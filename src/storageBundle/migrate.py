import logging

from coreBundle.db import get_sqlite
from storageBundle.migrations import MIGRATIONS

logger = logging.getLogger(__name__)


def run() -> None:
    """
    Apply any unapplied migrations in order.
    Tracks applied versions in the schema_migrations table.
    Safe to call on every startup — already-applied migrations are skipped.
    """
    conn = get_sqlite()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    TEXT NOT NULL PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()

    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}

    for version, sql in MIGRATIONS:
        if version in applied:
            continue
        logger.info("Applying migration: %s", version)
        conn.executescript(sql)
        conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
        conn.commit()
        logger.info("Migration applied: %s", version)
