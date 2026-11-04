import json
import re

from app.logging_conf import PATTERNS, SENSITIVE

SECRET_HEADERS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
    "x-csrf-token",
    "x-amz-security-token",
}
BODY_LIMIT = 2048
_JSON_SECRET = re.compile(
    r'("(?:[^"\\]|\\.)*(?:password|passwd|secret|token|api[_-]?key|authorization|session)(?:[^"\\]|\\.)*"\s*:\s*)"(?:[^"\\]|\\.)*"',
    re.I,
)


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    clean = {}
    for name, value in list(headers.items())[:60]:
        lowered = name.lower()
        clean[lowered] = (
            "[redacted]" if lowered in SECRET_HEADERS or SENSITIVE.search(lowered) else value[:500]
        )
    return clean


def _scrub(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: "[redacted]" if SENSITIVE.search(str(key)) else _scrub(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_text(text: str) -> str:
    for pattern, replacement in PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_body(raw: bytes) -> str:
    text = raw.decode("utf-8", "replace")
    try:
        document = json.loads(text)
    except ValueError:
        document = None
    if isinstance(document, dict | list):
        text = json.dumps(_scrub(document), ensure_ascii=False)
    else:
        text = _JSON_SECRET.sub(r'\1"[redacted]"', redact_text(text))
    return text[:BODY_LIMIT]
