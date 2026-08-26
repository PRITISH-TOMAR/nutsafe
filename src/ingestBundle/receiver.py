import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from storageBundle import migrate
from ingestBundle.routes import traces, metrics, logs

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    migrate.run()
    yield


app = FastAPI(lifespan=lifespan)
app.include_router(traces.router)
app.include_router(metrics.router)
app.include_router(logs.router)
