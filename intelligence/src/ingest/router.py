import logging

from fastapi import APIRouter, Header, HTTPException, Request, status

import db
from ingest.parser import parse

logger = logging.getLogger(__name__)

router = APIRouter()


def _resolve_tenant(conn, api_key: str) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM tenants WHERE api_key = %s", (api_key,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api-key")
    return row[0]


def _insert_spans(cur, tenant_id: int, records) -> None:
    cur.executemany(
        """
        INSERT INTO spans (
            tenant_id, trace_id, span_id, parent_span_id,
            service_name, span_name, target,
            start_time_ns, end_time_ns,
            status_code, http_status
        ) VALUES (
            %(tenant_id)s, %(trace_id)s, %(span_id)s, %(parent_span_id)s,
            %(service_name)s, %(span_name)s, %(target)s,
            %(start_time_ns)s, %(end_time_ns)s,
            %(status_code)s, %(http_status)s
        )
        ON CONFLICT (tenant_id, trace_id, span_id) DO NOTHING
        """,
        [
            {
                "tenant_id": tenant_id,
                "trace_id": r.trace_id,
                "span_id": r.span_id,
                "parent_span_id": r.parent_span_id,
                "service_name": r.service_name,
                "span_name": r.span_name,
                "target": r.target,
                "start_time_ns": r.start_time_ns,
                "end_time_ns": r.end_time_ns,
                "status_code": r.status_code,
                "http_status": r.http_status,
            }
            for r in records
        ],
    )


@router.post("/v1/traces", status_code=status.HTTP_200_OK)
async def receive_traces(
    request: Request,
    api_key: str = Header(..., alias="api-key"),
) -> dict:
    body = await request.body()

    with db.transaction() as conn:
        tenant_id = _resolve_tenant(conn, api_key)
        records = parse(body)
        with conn.cursor() as cur:
            _insert_spans(cur, tenant_id, records)

    logger.info("tenant=%d stored %d span(s)", tenant_id, len(records))
    return {"stored": len(records)}
