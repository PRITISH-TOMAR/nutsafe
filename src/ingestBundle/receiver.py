import datetime
import logging
import os

from fastapi import FastAPI, Request, Response

# ── debug flag ─────────────────────────────────────────────────────────────────
# True  → log every incoming request to logs/telemetry.log
# False → skip file logging (flip off once src/storage/ is wired up)
DEBUG_LOG_TO_FILE = True

_LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "logs", "telemetry.log")

# ── app ────────────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()


# ── internal helpers ───────────────────────────────────────────────────────────

def _log(signal: str, request: Request, body: bytes) -> None:
    if not DEBUG_LOG_TO_FILE:
        return

    api_key = request.headers.get("api-key", "<none>")
    masked = (api_key[:4] + "****") if len(api_key) > 4 else "****"

    log_path = os.path.abspath(_LOG_PATH)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    with open(log_path, "a") as f:
        f.write(
            f"[{datetime.datetime.utcnow().isoformat()}Z] "
            f"{signal} | key={masked} | "
            f"content-type={request.headers.get('content-type', '')} | "
            f"bytes={len(body)}\n"
            f"  hex preview: {body[:80].hex()}\n\n"
        )

    logger.info("%s received — %d bytes", signal, len(body))


# ── endpoints ──────────────────────────────────────────────────────────────────

@app.post("/v1/traces")
async def receive_traces(request: Request) -> Response:
    body = await request.body()
    _log("TRACES", request, body)
    return Response(status_code=200)


@app.post("/v1/metrics")
async def receive_metrics(request: Request) -> Response:
    body = await request.body()
    _log("METRICS", request, body)
    return Response(status_code=200)


@app.post("/v1/logs")
async def receive_logs(request: Request) -> Response:
    body = await request.body()
    _log("LOGS", request, body)
    return Response(status_code=200)
