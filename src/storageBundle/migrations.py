# Ordered list of migrations. Each entry: (version_id, sql).
# Append new entries at the bottom. Never edit or remove existing entries.

MIGRATIONS: list[tuple[str, str]] = [
    (
        "001_create_tenants",
        """
        CREATE TABLE IF NOT EXISTS tenants (
            tenant_id  INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT    NOT NULL,
            api_key    TEXT    NOT NULL UNIQUE,
            active     INTEGER NOT NULL DEFAULT 1,
            created_at TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        """,
    ),
    (
        "002_seed_default_tenant",
        """
        INSERT OR IGNORE INTO tenants (name, api_key)
        VALUES ('default', 'dev-key-change-me');
        """,
    ),
    (
        "003_create_baselines",
        """
        CREATE TABLE IF NOT EXISTS baselines (
            tenant_id   INTEGER NOT NULL,
            span_name   TEXT    NOT NULL,
            target      TEXT    NOT NULL DEFAULT '',
            ewma_ns     REAL    NOT NULL,
            ewma_var_ns REAL    NOT NULL DEFAULT 0,
            updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (tenant_id, span_name, target)
        );
        """,
    ),
]
