import sqlite3

from coreBundle.database import get_sqlite


def get_by_api_key(api_key: str) -> sqlite3.Row | None:
    connection = get_sqlite()
    return connection.execute(
        "SELECT tenant_id FROM tenants WHERE api_key = ? AND active = 1",
        (api_key,),
    ).fetchone()
