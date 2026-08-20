import logging
import os
from pathlib import Path

import yaml
from pydantic import BaseModel


class ServerSettings(BaseModel):
    host: str
    port: int


class DBSettings(BaseModel):
    url: str
    min_connections: int
    max_connections: int


class LoggingSettings(BaseModel):
    level: str


class Settings(BaseModel):
    server: ServerSettings
    db: DBSettings
    logging: LoggingSettings


def _load() -> Settings:
    config_path = Path(__file__).parent.parent / "config" / "settings.yaml"
    with open(config_path) as f:
        raw = yaml.safe_load(f)

    raw["db"]["url"] = os.environ["DATABASE_URL"]

    return Settings(**raw)


settings = _load()

logging.basicConfig(
    level=settings.logging.level,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
