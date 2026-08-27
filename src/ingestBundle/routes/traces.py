import logging

from fastapi import APIRouter, Request, Response

from ingestBundle.deps import require_tenant
from ingestBundle.parser import parse_traces
from storageBundle import spans

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/v1/traces")
async def receive_traces(request: Request) -> Response:
    tenant_id = require_tenant(request)
    if tenant_id is None:
        return Response(status_code=401)
    body = await request.body()
    records = parse_traces(body, request.headers.get("content-type", ""))
    spans.insert_spans(tenant_id, records)
    logger.info("TRACES  tenant=%d  spans=%d", tenant_id, len(records))
    return Response(status_code=200)
