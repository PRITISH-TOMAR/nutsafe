import logging

from fastapi import APIRouter, Request, Response

from ingestBundle.deps import require_tenant
from ingestBundle.parser import parse_metrics

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/v1/metrics")
async def receive_metrics(request: Request) -> Response:
    tenant_id = require_tenant(request)
    if tenant_id is None:
        return Response(status_code=401)
    body = await request.body()
    points = parse_metrics(body, request.headers.get("content-type", ""))
    logger.info("METRICS  tenant=%d  points=%d", tenant_id, len(points))
    return Response(status_code=200)
