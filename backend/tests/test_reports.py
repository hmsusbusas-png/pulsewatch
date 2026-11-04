from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select

from app.config import settings
from app.crypto import decrypt_text
from app.database import session_factory
from app.models import MailJob
from app.services.notifications import _smtp
from app.services.reports import weekly_reports


async def test_weekly_mail_deduplicated(
    client: httpx.AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import reports

    class Monday(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 9, 9, tzinfo=UTC)

    monkeypatch.setattr(reports, "datetime", Monday)
    await client.post(
        "/api/sites", headers=auth_headers, json={"name": "API", "url": "https://8.8.8.8"}
    )
    async with session_factory() as session:
        await weekly_reports(session)
        await weekly_reports(session)
        jobs = list(
            await session.scalars(select(MailJob).where(MailJob.dedupe_key.startswith("weekly:")))
        )
        assert len(jobs) == 1
        assert "API" in decrypt_text(jobs[0].body) and "Инцидентов" in decrypt_text(jobs[0].body)


def test_smtp_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "smtp_host", "smtp.test")
    monkeypatch.setattr(settings, "smtp_username", "user")
    monkeypatch.setattr(settings, "smtp_password", "password")
    monkeypatch.setattr(settings, "smtp_from", "alerts@example.com")
    calls = []

    class SMTP:
        def __init__(self, *args, **kwargs):
            calls.append("connect")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def starttls(self, context):
            assert context.check_hostname
            calls.append("tls")

        def login(self, username, password):
            calls.append("login")

        def send_message(self, message):
            assert message["To"] == "owner@example.com"
            calls.append("sent")

    monkeypatch.setattr("app.services.notifications.smtplib.SMTP", SMTP)
    _smtp("owner@example.com", "Pulsewatch", "test")
    assert calls == ["connect", "tls", "login", "sent"]
