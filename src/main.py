import uvicorn

from coreBundle.config import settings
from ingestBundle.receiver import app

if __name__ == "__main__":
    uvicorn.run(app, host=settings.server.host, port=settings.server.port)
