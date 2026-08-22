import os
from pathlib import Path

import yaml
from pydantic import BaseModel


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 4319


class LoggingConfig(BaseModel):
    level: str = "INFO"


class ClickHouseConfig(BaseModel):
    host: str = "clickhouse"
    port: int = 9000
    database: str = "nutsafe"
    user: str = "default"
    password: str = ""


class SQLiteConfig(BaseModel):
    path: str = "/data/nutsafe.db"


class AlertsConfig(BaseModel):
    webhook_url: str = ""
    slack_url: str = ""
    pagerduty_key: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_to: str = ""
    sns_topic_arn: str = ""


class Settings(BaseModel):
    server: ServerConfig = ServerConfig()
    logging: LoggingConfig = LoggingConfig()
    clickhouse: ClickHouseConfig = ClickHouseConfig()
    sqlite: SQLiteConfig = SQLiteConfig()
    alerts: AlertsConfig = AlertsConfig()


def _load_settings() -> Settings:
    config_path = Path(__file__).parent.parent.parent / "config" / "settings.yaml"
    data: dict = {}
    if config_path.exists():
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

    # Override with environment variables
    if url := os.getenv("CLICKHOUSE_HOST"):
        data.setdefault("clickhouse", {})["host"] = url
    if path := os.getenv("SQLITE_PATH"):
        data.setdefault("sqlite", {})["path"] = path
    for alert_key in (
        "webhook_url", "slack_url", "pagerduty_key",
        "smtp_host", "smtp_user", "smtp_password", "smtp_to", "sns_topic_arn",
    ):
        env_val = os.getenv(f"ALERT_{alert_key.upper()}", "")
        if env_val:
            data.setdefault("alerts", {})[alert_key] = env_val

    return Settings(**data)


settings = _load_settings()
