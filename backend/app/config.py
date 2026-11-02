import json
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/pulsewatch"
    jwt_secret: str = "insecure-development-secret"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 14

    cookie_secure: bool = True
    cookie_domain: str | None = None
    cors_origins: list[str] = ["http://localhost:5173"]
    frontend_base_url: str = "http://localhost:5173"

    allow_private_targets: bool = False
    check_timeout_seconds: float = 10.0
    check_concurrency: int = 20
    scheduler_tick_seconds: int = 15
    ssl_refresh_hours: int = 6
    checks_retention_days: int = 30
    free_tariff_site_limit: int = 5

    auth_rate_limit_per_minute: int = 20
    telegram_bot_token: str | None = None
    telegram_poll_timeout_seconds: int = 25
    log_level: str = "INFO"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: Any) -> Any:
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            if text.startswith("["):
                return json.loads(text)
            return [item.strip() for item in text.split(",") if item.strip()]
        return value


settings = Settings()
