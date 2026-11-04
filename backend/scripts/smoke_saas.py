import asyncio
import json
import secrets
import sys
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy import select, update

from app.config import settings, validate_runtime
from app.crypto import decrypt_text
from app.database import engine, session_factory
from app.main import app
from app.models import MailJob, Site, Subscription, User
from app.scheduler import heartbeat, run_due_checks
from app.services.notifications import deliver_pending


class Target(BaseHTTPRequestHandler):
    status_code = 200

    def do_GET(self) -> None:
        self.send_response(Target.status_code)
        self.end_headers()
        self.wfile.write(b"service ready")

    def log_message(self, *_: object) -> None:
        return None


async def scenario(url: str) -> list[str]:
    steps: list[str] = []
    email = f"smoke-{secrets.token_hex(6)}@example.com"
    password = secrets.token_urlsafe(20)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://smoke") as client:
        registered = await client.post(
            "/api/auth/register", json={"email": email, "password": password}
        )
        assert registered.status_code == 202, registered.text
        async with session_factory() as session:
            job = await session.scalar(
                select(MailJob).where(MailJob.recipient == email).order_by(MailJob.id.desc())
            )
            assert job
            token = decrypt_text(job.body).split("token=")[1].split()[0]
        verified = await client.post(
            "/api/auth/email/confirm", json={"token": token, "password": password}
        )
        assert verified.status_code == 204
        registered = await client.post(
            "/api/auth/login", json={"email": email, "password": password}
        )
        assert registered.status_code == 200
        headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
        trial = (await client.get("/api/billing/subscription", headers=headers)).json()
        assert trial["status"] == "trialing" and trial["plan"]["code"] == "pro"
        steps.extend(["registration", "pro_trial"])
        for index in range(3):
            created = await client.post(
                "/api/sites", headers=headers, json={"name": f"Service {index + 1}", "url": url}
            )
            assert created.status_code == 201, created.text
        steps.append("three_sites")
        async with session_factory() as session:
            user = await session.scalar(select(User).where(User.email == email))
            assert user
            user_id = user.id
            sub = await session.scalar(select(Subscription).where(Subscription.user_id == user.id))
            assert sub
            sub.current_period_end = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()
        free = (await client.get("/api/billing/subscription", headers=headers)).json()
        assert free["plan"]["code"] == "free"
        blocked = await client.post(
            "/api/sites", headers=headers, json={"name": "Excess", "url": url}
        )
        assert blocked.status_code == 402 and blocked.json()["code"] == "plan_limit_sites"
        steps.append("free_limit")
        checkout = await client.post(
            "/api/billing/checkout", headers=headers, json={"plan_code": "pro"}
        )
        assert checkout.status_code == 200, checkout.text
        assert (await client.get(checkout.json()["url"])).status_code == 200
        assert (await client.post(checkout.json()["url"])).status_code == 200
        paid = (await client.get("/api/billing/subscription", headers=headers)).json()
        assert paid["status"] == "active" and paid["plan"]["code"] == "pro"
        steps.append("dev_checkout_pro")
        sent: list[str] = []

        async def telegram(method: str, payload: dict[str, object]) -> httpx.Response:
            sent.append(str(payload.get("text", "")))
            return httpx.Response(
                200, json={"ok": True}, request=httpx.Request("POST", "https://api.telegram.org/")
            )

        with (
            patch(
                "app.routers.telegram.fetch_bot_username",
                AsyncMock(return_value="PulsewatchSmokeBot"),
            ),
            patch("app.services.telegram.telegram_request", telegram),
            patch.object(settings, "bot_token", "smoke-token"),
            patch.object(settings, "public_url", "https://smoke.invalid"),
            patch.object(settings, "telegram_webhook_secret", "smoke-webhook-secret"),
        ):
            link = await client.post("/api/telegram/link", headers=headers)
            assert link.status_code == 200
            linked = await client.post(
                "/api/telegram/webhook",
                headers={"x-telegram-bot-api-secret-token": "smoke-webhook-secret"},
                json={
                    "update_id": 1,
                    "message": {
                        "chat": {"id": user_id + 100000, "type": "private"},
                        "text": f"/start {link.json()['link_token']}",
                    },
                },
            )
            assert linked.status_code == 200
            assert (await client.get("/api/telegram/status", headers=headers)).json()["linked"]
            steps.append("telegram_link_mock_api")
            Target.status_code = 503
            for _ in range(2):
                async with session_factory() as session:
                    await session.execute(
                        update(Site)
                        .where(Site.user_id == user_id)
                        .values(next_check_at=datetime.now(UTC))
                    )
                    await session.commit()
                await run_due_checks()
            async with session_factory() as session:
                await deliver_pending(session)
            assert sum("HTTP 503" in text for text in sent) == 3
            steps.append("failure_confirmed_notifications_sent")
            Target.status_code = 200
            async with session_factory() as session:
                await session.execute(
                    update(Site)
                    .where(Site.user_id == user_id)
                    .values(next_check_at=datetime.now(UTC))
                )
                await session.commit()
            await run_due_checks()
            async with session_factory() as session:
                await deliver_pending(session)
            assert sum("Простой" in text for text in sent) == 3
            assert all(
                site["status"] == "up"
                for site in (await client.get("/api/sites", headers=headers)).json()
            )
            steps.append("recovery_notifications_sent")
        await heartbeat()
        assert (await client.get("/readyz")).status_code == 200
        removed = await client.request(
            "DELETE", "/api/account", headers=headers, json={"email": email, "password": password}
        )
        assert removed.status_code == 204
    return steps


async def main() -> None:
    if settings.app_env != "dev" or not settings.database_url.rsplit("/", 1)[-1].endswith("_test"):
        raise RuntimeError("Smoke требует APP_ENV=dev и отдельную БД с суффиксом _test")
    if not settings.allow_private_targets or settings.payment_provider != "dev":
        raise RuntimeError("Smoke требует ALLOW_PRIVATE_TARGETS=true и PAYMENT_PROVIDER=dev")
    validate_runtime()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Target)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        steps = await scenario(f"http://127.0.0.1:{server.server_port}/")
        sys.stdout.write(json.dumps({"ok": True, "steps": steps}, ensure_ascii=False) + "\n")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
