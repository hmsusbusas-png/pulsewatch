import hashlib
import json
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.crypto import decrypt_text, encrypt_text
from app.database import session_factory
from app.errors import error_body
from app.models import IdempotencyKey
from app.security import access_claims, hash_refresh_token

CREATING_PATHS = re.compile(
    r"^/api/(sites|sites/import|channels|status-pages|status-pages/\d+/announcements|keys|"
    r"incidents/\d+/notes|escalations|billing/checkout)$"
)
KEY_PATTERN = re.compile(r"^[\x21-\x7e]{1,255}$")
STALE_AFTER = timedelta(seconds=60)
MAX_STORED_BODY = 262144


def principal(headers: dict[bytes, bytes]) -> str | None:
    scheme, _, token = headers.get(b"authorization", b"").decode("latin-1").partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    if token.startswith("pw_"):
        return f"key:{hash_refresh_token(token)[:40]}"
    claims = access_claims(token)
    return f"user:{claims['sub']}" if claims else None


class IdempotencyGuard:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        key = headers.get(b"idempotency-key", b"").decode("latin-1").strip()
        owner = principal(headers) if key else None
        if not key or not CREATING_PATHS.match(scope["path"]) or owner is None:
            await self.app(scope, receive, send)
            return
        request_id = str(scope.get("state", {}).get("request_id", "unavailable"))
        if not KEY_PATTERN.match(key):
            await JSONResponse(
                error_body(
                    "validation_error", "Idempotency-Key: 1-255 печатных ASCII-символов", request_id
                ),
                status_code=422,
            )(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            body.extend(message.get("body", b""))
            if not message.get("more_body", False):
                break
        digest = hashlib.sha256(
            scope["method"].encode() + scope["path"].encode() + b"\n" + bytes(body)
        ).hexdigest()
        replay = await self._reserve(owner, key, scope["path"], digest, request_id)
        if replay is not None:
            await replay(scope, receive, send)
            return
        status_code = 500
        captured = bytearray()
        content_type = b"application/json"

        async def replayable() -> Message:
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        async def recording(message: Message) -> None:
            nonlocal status_code, content_type
            if message["type"] == "http.response.start":
                status_code = message["status"]
                content_type = dict(message.get("headers", [])).get(b"content-type", content_type)
            elif message["type"] == "http.response.body" and len(captured) < MAX_STORED_BODY:
                captured.extend(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, replayable, recording)
        finally:
            await self._settle(owner, key, status_code, content_type, bytes(captured))

    async def _reserve(
        self, owner: str, key: str, path: str, digest: str, request_id: str
    ) -> Response | None:
        async with session_factory() as session, session.begin():
            inserted = await session.scalar(
                insert(IdempotencyKey)
                .values(scope=owner, key=key, method="POST", path=path, request_hash=digest)
                .on_conflict_do_nothing(index_elements=["scope", "key"])
                .returning(IdempotencyKey.id)
            )
            if inserted is not None:
                return None
            existing = await session.scalar(
                select(IdempotencyKey)
                .where(IdempotencyKey.scope == owner, IdempotencyKey.key == key)
                .with_for_update()
            )
            if existing is None:
                return None
            if existing.request_hash != digest or existing.path != path:
                return JSONResponse(
                    error_body(
                        "idempotency_key_reused",
                        "Ключ уже использован с другим запросом",
                        request_id,
                    ),
                    status_code=422,
                )
            if existing.status_code is None:
                if existing.created_at > datetime.now(UTC) - STALE_AFTER:
                    return JSONResponse(
                        error_body(
                            "idempotency_in_progress",
                            "Запрос с этим ключом ещё выполняется",
                            request_id,
                        ),
                        status_code=409,
                    )
                existing.created_at = datetime.now(UTC)
                return None
            stored = json.loads(decrypt_text(existing.response_encrypted or ""))
            return Response(
                stored["body"].encode(),
                status_code=existing.status_code,
                media_type=stored["content_type"],
                headers={"Idempotent-Replayed": "true"},
            )

    async def _settle(
        self, owner: str, key: str, status_code: int, content_type: bytes, body: bytes
    ) -> None:
        async with session_factory() as session, session.begin():
            if status_code >= 500:
                await session.execute(
                    delete(IdempotencyKey).where(
                        IdempotencyKey.scope == owner, IdempotencyKey.key == key
                    )
                )
                return
            stored = json.dumps(
                {
                    "content_type": content_type.decode("latin-1"),
                    "body": body.decode("utf-8", "replace"),
                }
            )
            await session.execute(
                update(IdempotencyKey)
                .where(IdempotencyKey.scope == owner, IdempotencyKey.key == key)
                .values(status_code=status_code, response_encrypted=encrypt_text(stored))
            )
