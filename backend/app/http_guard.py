import asyncio
import json
import re
import secrets

from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings
from app.idempotency import principal
from app.logging_conf import request_id_var
from app.services.devices import client_ip
from app.services.rate_limit import RateDecision, allow_persistent

API_VERSION = "2026-11-04"
RATE_EXEMPT = ("/api/stream", "/api/agent/", "/api/health")
SENSITIVE_PREFIXES = (
    "/api/auth/",
    "/api/status/",
    "/api/public/",
    "/api/telegram/",
    "/api/channels",
    "/api/billing/webhook/",
    "/api/heartbeat/",
    "/api/badge/",
)


def headers() -> dict[str, str]:
    result = {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'none'; form-action 'self'; frame-ancestors 'none'",
        "X-API-Version": API_VERSION,
    }
    if settings.app_env == "production":
        result["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return result


def shallow_json(body: bytes | bytearray) -> bool:
    depth, quoted, escaped = 0, False, False
    for char in body:
        if quoted:
            if escaped:
                escaped = False
            elif char == 92:
                escaped = True
            elif char == 34:
                quoted = False
        elif char == 34:
            quoted = True
        elif char in (91, 123):
            depth += 1
            if depth > settings.max_json_depth:
                return False
        elif char in (93, 125):
            depth -= 1
    return True


class HttpGuard:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope["headers"])
        candidate = incoming.get(b"x-request-id", b"").decode("latin-1")
        request_id = (
            candidate if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", candidate) else secrets.token_hex(16)
        )
        scope.setdefault("state", {})["request_id"] = request_id
        context = request_id_var.set(request_id)
        response_headers = {**headers(), "X-Request-ID": request_id}

        async def fail(code: int, message: str) -> None:
            await JSONResponse(
                {
                    "code": {
                        429: "rate_limited",
                        403: "origin_forbidden",
                        413: "request_too_large",
                        408: "request_timeout",
                        400: "bad_request",
                    }.get(code, "request_rejected"),
                    "message": message,
                    "details": None,
                    "request_id": request_id,
                },
                status_code=code,
                headers=response_headers,
            )(scope, receive, send)

        async def secured(message: Message) -> None:
            if message["type"] == "http.response.start":
                current = dict(message.get("headers", []))
                for key, value in response_headers.items():
                    name = key.lower().encode()
                    if name == b"cache-control" and name in current:
                        continue
                    current[name] = value.encode()
                message["headers"] = list(current.items())
            await send(message)

        try:
            path = scope["path"]
            decision: object = None
            if path.startswith(SENSITIVE_PREFIXES):
                ip = client_ip(HTTPConnection(scope)) or "unknown"
                limit = (
                    settings.auth_rate_limit_per_minute if path.startswith("/api/auth/") else 120
                )
                decision = await allow_persistent(f"ip:{path.split('/')[2]}:{ip}", limit)
            elif path.startswith("/api/") and not path.startswith(RATE_EXEMPT):
                ip = client_ip(HTTPConnection(scope)) or "unknown"
                who = principal(incoming) or f"ip:{ip}"
                if who.startswith("key:"):
                    ip_decision = await allow_persistent(
                        f"api-key-ip:{ip}", settings.api_rate_limit_per_minute
                    )
                    if not ip_decision:
                        response_headers.update(ip_decision.headers())
                        await fail(429, "Слишком много запросов")
                        return
                decision = await allow_persistent(f"api:{who}", settings.api_rate_limit_per_minute)
            if isinstance(decision, RateDecision):
                response_headers.update(decision.headers())
            if decision is not None and not decision:
                await fail(429, "Слишком много запросов")
                return
            if path in {"/api/auth/refresh", "/api/auth/logout"}:
                origin = incoming.get(b"origin", b"").decode("latin-1")
                if origin and origin not in settings.cors_origins:
                    await fail(403, "Источник запроса запрещён")
                    return
            body = bytearray()
            try:
                async with asyncio.timeout(10):
                    while True:
                        chunk = await receive()
                        if chunk["type"] == "http.disconnect":
                            return
                        body.extend(chunk.get("body", b""))
                        if len(body) > settings.max_request_bytes:
                            await fail(413, "Тело запроса слишком велико")
                            return
                        if not chunk.get("more_body", False):
                            break
            except TimeoutError:
                await fail(408, "Таймаут чтения запроса")
                return
            if b"application/json" in incoming.get(b"content-type", b"") and body:
                if not shallow_json(body):
                    await fail(413, "Слишком глубокая структура JSON")
                    return
                try:
                    json.loads(body)
                except (ValueError, RecursionError):
                    await fail(400, "Некорректный JSON")
                    return
            delivered = False

            async def buffered() -> Message:
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            await self.app(scope, buffered, secured)
        finally:
            request_id_var.reset(context)
