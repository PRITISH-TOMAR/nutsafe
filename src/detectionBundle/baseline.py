"""
EWMA baseline per (tenant_id, span_name, target).
Updated at ingest time for every incoming span.

Algorithm (alpha = 0.10):
    ewma_ns     = alpha * duration_ns + (1 - alpha) * ewma_ns
    ewma_var_ns = (1 - alpha) * (ewma_var_ns + alpha * (duration_ns - ewma_ns)^2)

ewma_var uses the OLD ewma_ns before the mean is advanced, matching the
Welford-style online estimator referenced in the detection spec.
"""

import sqlite3

ALPHA = 0.10


def update(
    conn: sqlite3.Connection,
    tenant_id: int,
    span_name: str,
    target: str | None,
    duration_ns: int,
) -> None:
    """Read current baseline, apply EWMA update, upsert back. Must be called
    inside a transaction (the caller owns BEGIN/COMMIT via sqlite_tx)."""
    target_key = target or ""
    row = conn.execute(
        "SELECT ewma_ns, ewma_var_ns FROM baselines"
        " WHERE tenant_id = ? AND span_name = ? AND target = ?",
        (tenant_id, span_name, target_key),
    ).fetchone()

    if row is None:
        # First observation: seed ewma to the observed duration, variance to 0.
        new_ewma_ns = float(duration_ns)
        new_ewma_var_ns = 0.0
    else:
        old_ewma = row["ewma_ns"]
        old_var = row["ewma_var_ns"]
        # Advance variance first (uses old mean), then advance mean.
        new_ewma_var_ns = (1 - ALPHA) * (old_var + ALPHA * (duration_ns - old_ewma) ** 2)
        new_ewma_ns = ALPHA * duration_ns + (1 - ALPHA) * old_ewma

    conn.execute(
        """
        INSERT INTO baselines (tenant_id, span_name, target, ewma_ns, ewma_var_ns, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT (tenant_id, span_name, target) DO UPDATE SET
            ewma_ns     = excluded.ewma_ns,
            ewma_var_ns = excluded.ewma_var_ns,
            updated_at  = excluded.updated_at
        """,
        (tenant_id, span_name, target_key, new_ewma_ns, new_ewma_var_ns),
    )


def get(
    conn: sqlite3.Connection,
    tenant_id: int,
    span_name: str,
    target: str | None,
) -> tuple[float, float] | None:
    """Return (ewma_ns, ewma_var_ns) for the given key, or None if unseen."""
    target_key = target or ""
    row = conn.execute(
        "SELECT ewma_ns, ewma_var_ns FROM baselines"
        " WHERE tenant_id = ? AND span_name = ? AND target = ?",
        (tenant_id, span_name, target_key),
    ).fetchone()
    if row is None:
        return None
    return row["ewma_ns"], row["ewma_var_ns"]
