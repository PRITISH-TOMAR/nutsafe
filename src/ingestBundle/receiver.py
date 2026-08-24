import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from coreBundle.auth import authenticate
from storageBundle import migrate

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    migrate.run()
    yield


app = FastAPI(lifespan=lifespan)


def _require_tenant(request: Request) -> int | None:
    api_key = request.headers.get("api-key", "")
    return authenticate(api_key)


@app.post("/v1/traces")
async def receive_traces(request: Request) -> Response:
    tenant_id = _require_tenant(request)
    if tenant_id is None:
        return Response(status_code=401)
    body = await request.body()
    logger.info("TRACES  tenant=%d  bytes=%d", tenant_id, len(body))
    return Response(status_code=200)


@app.post("/v1/metrics")
async def receive_metrics(request: Request) -> Response:
    tenant_id = _require_tenant(request)
    if tenant_id is None:
        return Response(status_code=401)
    body = await request.body()
    logger.info("METRICS  tenant=%d  bytes=%d", tenant_id, len(body))
    return Response(status_code=200)


@app.post("/v1/logs")
async def receive_logs(request: Request) -> Response:
    tenant_id = _require_tenant(request)
    if tenant_id is None:
        return Response(status_code=401)
    body = await request.body()
    logger.info("LOGS  tenant=%d  bytes=%d", tenant_id, len(body))
    return Response(status_code=200)
