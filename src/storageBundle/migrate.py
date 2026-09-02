import logging

from coreBundle.config import settings
from coreBundle.database import get_sqlite, get_clickhouse
from storageBundle.migrations import MIGRATIONS
from storageBundle.clickhouse_migrations import CH_TABLES

logger = logging.getLogger(__name__)


def run() -> None:
    """
    Apply any unapplied migrations in order.
    Tracks applied versions in the schema_migrations table.
    Safe to call on every startup — already-applied migrations are skipped.
    """
    connection = get_sqlite()
    connection.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    TEXT NOT NULL PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    connection.commit()

    applied = {migration[0] for migration in connection.execute("SELECT version FROM schema_migrations")}

    for version, sql in MIGRATIONS:
        if version in applied:
            continue
        logger.info("Applying migration: %s", version)
        connection.executescript(sql)
        connection.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
        connection.commit()
        logger.info("Migration applied: %s", version)


def run_clickhouse() -> None:
    """
    Ensure the ClickHouse database and all tables exist.
    Safe to call on every startup — all statements are idempotent.
    """
    clickhouse_client = get_clickhouse()
    db = settings.clickhouse.database
    logger.info("Ensuring ClickHouse database: %s", db)
    clickhouse_client.command(f"CREATE DATABASE IF NOT EXISTS {db}")
    logger.info("ClickHouse database ready: %s", db)
    for table_name, ddl in CH_TABLES:
        logger.info("Ensuring ClickHouse table: %s", table_name)
        clickhouse_client.command(ddl.strip())
        logger.info("ClickHouse table ready: %s", table_name)
