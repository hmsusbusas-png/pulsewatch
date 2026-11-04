import json
import logging
import re
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


SENSITIVE = re.compile(
    r"password|passwd|secret|token|authorization|cookie|headers|config|payload|body|(?:^|_)key",
    re.I,
)
PATTERNS = [
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), "[redacted-key]"),
    (re.compile(r"\b(?:sk|pk)[_-][A-Za-z0-9_-]{20,}\b"), "[redacted-key]"),
    (
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?"
            r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
            re.S,
        ),
        "[redacted-private-key]",
    ),
    (
        re.compile(r"(?i)(?:postgres(?:ql)?(?:\+asyncpg)?|https?)://[^\s/@]+:[^\s@]+@"),
        "[redacted-url]@",
    ),
    (re.compile(r"(?i)(?:bearer|basic)\s+[^\s\"']+"), "[redacted-authorization]"),
    (re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"), "[redacted-bot-token]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), "[redacted-jwt]"),
    (re.compile(r"(?i)([?&](?:token|signature|key|code)=)[^&\s]+"), r"\1[redacted]"),
    (
        re.compile(
            r"(?i)((?:password|secret|token|authorization|api[_-]?key)[\"']?\s*[:=]\s*[\"']?)[^\"'\s,}]+"
        ),
        r"\1[redacted]",
    ),
]


def redact(value: object, key: str = "") -> object:
    if SENSITIVE.search(key):
        return "[redacted]"
    if isinstance(value, dict):
        return {str(name): redact(item, str(name)) for name, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if not isinstance(value, str):
        return value
    from app.config import settings

    for secret in (
        settings.jwt_secret,
        settings.encryption_key,
        *settings.fernet_previous_keys,
        settings.bot_token,
        settings.telegram_bot_token,
        settings.yookassa_secret_key,
        settings.smtp_password,
        settings.telegram_webhook_secret,
        settings.metrics_token,
    ):
        if secret and len(secret) >= 4:
            value = value.replace(secret, "[redacted]")
    for pattern, replacement in PATTERNS:
        value = pattern.sub(replacement, value)
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info and record.exc_info[0]:
            payload["exception"] = record.exc_info[0].__name__
        clean = redact(payload)
        preview = getattr(record, "dev_mail", None)
        if isinstance(clean, dict) and isinstance(preview, dict):
            from app.config import settings

            if settings.app_env == "dev" and not settings.smtp_host:
                clean["mail"] = preview
        return json.dumps(clean, ensure_ascii=False, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)
