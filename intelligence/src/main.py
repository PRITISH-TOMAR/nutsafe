import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

import db
from config import settings
from ingest.router import router as ingest_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_pool()
    logger.info("intelligence service started")
    yield
    db.close_pool()
    logger.info("intelligence service stopped")


app = FastAPI(title="nutsafe-intelligence", lifespan=lifespan)

app.include_router(ingest_router)


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.server.host,
        port=settings.server.port,
        log_level=settings.logging.level.lower(),
    )
