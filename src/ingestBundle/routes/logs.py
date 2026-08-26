import logging

from fastapi import APIRouter, Request, Response

from ingestBundle.deps import require_tenant

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/v1/logs")
async def receive_logs(request: Request) -> Response:
    tenant_id = require_tenant(request)
    if tenant_id is None:
        return Response(status_code=401)
    body = await request.body()
    logger.info("LOGS  tenant=%d  bytes=%d", tenant_id, len(body))
    return Response(status_code=200)
