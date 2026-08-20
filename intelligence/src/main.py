import logging

import uvicorn
from fastapi import FastAPI

import db
from config import settings
from ingest.router import router as ingest_router

logger = logging.getLogger(__name__)

app = FastAPI(title="nutsafe-intelligence")

app.include_router(ingest_router)


@app.on_event("startup")
def on_startup() -> None:
    db.init_pool()
    logger.info("intelligence service started")


@app.on_event("shutdown")
def on_shutdown() -> None:
    db.close_pool()
    logger.info("intelligence service stopped")


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.server.host,
        port=settings.server.port,
        log_level=settings.logging.level.lower(),
    )
