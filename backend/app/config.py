import json
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(Path(__file__).resolve().parents[2] / ".env", ".env"),
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://localhost/pulsewatch"
    jwt_secret: str = ""
    jwt_algorithm: Literal["HS256"] = "HS256"
    access_token_minutes: int = Field(default=15, ge=1, le=15)
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

    auth_rate_limit_per_minute: int = 20
    telegram_bot_token: str | None = None
    telegram_poll_timeout_seconds: int = 25
    log_level: str = "INFO"
    app_env: Literal["dev", "test", "production"] = "production"
    encryption_key: str = Field(
        default="", validation_alias=AliasChoices("FERNET_KEY", "ENCRYPTION_KEY")
    )
    fernet_previous_keys: list[str] = []
    jwt_issuer: str = "pulsewatch"
    jwt_audience: str = "pulsewatch-api"
    max_request_bytes: int = Field(default=1048576, ge=1024, le=1048576)
    max_json_depth: int = Field(default=16, ge=1, le=32)
    payment_provider: Literal["dev", "yookassa"] = "yookassa"
    api_base_url: str = "http://localhost:8000"
    yookassa_shop_id: str = ""
    yookassa_secret_key: str = ""
    bot_token: str = ""
    public_url: str = ""
    telegram_webhook_secret: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True
    metrics_token: str = ""
    embedded_worker: bool = False
    demo_email: str = "user@example.com"
    dns_resolvers: list[str] = []
    worker_region: str = Field(default="msk", pattern=r"^[a-z0-9-]{2,16}$")
    worker_region_name: str = "Москва"
    region_agents: dict[str, dict[str, str]] = {}
    agent_api_url: str = "http://backend:8000"
    agent_token: str = ""
    stream_max_per_user: int = Field(default=5, ge=1, le=50)
    api_rate_limit_per_minute: int = Field(default=600, ge=10)
    check_now_per_user_per_minute: int = Field(default=10, ge=1)
    check_now_site_cooldown_seconds: int = Field(default=20, ge=1)
    trusted_proxies: list[str] = []
    status_cname_target: str = "status.pulsewatch.dev"
    extra_ca_file: str = ""
    demo_target_host: str = "demo-target"
    demo_slack_url: str = "https://demo-target:8443/hooks/slack"

    @field_validator("cors_origins", "dns_resolvers", "trusted_proxies", mode="before")
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


def weak_secret(value: str) -> bool:
    compact = value.lower().replace("_", "").replace("-", "")
    return (
        len(value) < 32
        or len(set(value)) < 12
        or any(
            marker in compact
            for marker in ("changeme", "example", "insecure", "password", "testsecret")
        )
    )


def validate_runtime() -> None:
    import base64
    from urllib.parse import urlsplit

    from cryptography.fernet import Fernet
    from sqlalchemy.engine import make_url

    if not settings.jwt_secret:
        raise RuntimeError("Укажите JWT_SECRET")
    for key in [settings.encryption_key, *settings.fernet_previous_keys]:
        Fernet(key.encode())
        if settings.app_env == "production" and len(set(base64.urlsafe_b64decode(key))) < 12:
            raise RuntimeError("FERNET_KEY: используйте случайный ключ")
    for origin in settings.cors_origins:
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or "*" in origin
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError("CORS_ORIGINS: нужны явные origins без wildcard")
    for address in (settings.api_base_url, settings.frontend_base_url, settings.public_url):
        if not address:
            continue
        parsed = urlsplit(address)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError("Некорректный публичный адрес")
    if settings.smtp_host and not settings.smtp_starttls:
        raise RuntimeError("SMTP требует STARTTLS с проверкой сертификата")
    if settings.app_env == "production":
        database = make_url(settings.database_url)
        if weak_secret(settings.jwt_secret) or weak_secret(database.password or ""):
            raise RuntimeError(
                "JWT_SECRET и пароль БД: минимум 32 случайных символа без плейсхолдеров"
            )
        if any(
            address and not address.startswith("https://")
            for address in (settings.api_base_url, settings.frontend_base_url, settings.public_url)
        ):
            raise RuntimeError("Production требует HTTPS для публичных адресов")
        if not settings.cookie_secure or settings.payment_provider == "dev":
            raise RuntimeError("Production требует Secure cookie и реальный платёжный адаптер")
        if any(not origin.startswith("https://") for origin in settings.cors_origins):
            raise RuntimeError("Production CORS требует HTTPS")
    if settings.allow_private_targets and settings.app_env != "dev":
        raise RuntimeError("ALLOW_PRIVATE_TARGETS разрешён только в dev")
