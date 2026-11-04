import base64
import os
import secrets
from collections.abc import AsyncIterator
from pathlib import Path

TEST_DATABASE_URL = os.environ["TEST_DATABASE_URL"]
if not TEST_DATABASE_URL.rsplit("/", 1)[-1].endswith("_test"):
    raise RuntimeError("TEST_DATABASE_URL must end in _test")
os.environ["APP_ENV"] = "dev"
os.environ["ENCRYPTION_KEY"] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()

os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["ALLOW_PRIVATE_TARGETS"] = "false"
os.environ["COOKIE_SECURE"] = "false"
os.environ["JWT_SECRET"] = secrets.token_urlsafe(48)
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["AUTH_RATE_LIMIT_PER_MINUTE"] = "10000"
os.environ["BOT_TOKEN"] = ""
os.environ["PAYMENT_PROVIDER"] = "dev"
os.environ["FERNET_KEY"] = os.environ["ENCRYPTION_KEY"]

import httpx
import pytest
from alembic.config import Config
from sqlalchemy import text

from alembic import command
from app.config import settings
from app.database import engine, session_factory
from app.main import app

BACKEND_DIR = Path(__file__).resolve().parent.parent
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"


def run_migrations() -> None:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.upgrade(config, "head")


@pytest.fixture(scope="session", autouse=True)
def apply_migrations() -> None:
    run_migrations()


@pytest.fixture(scope="session", autouse=True)
async def dispose_engine() -> AsyncIterator[None]:
    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
async def clean_tables() -> AsyncIterator[None]:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "truncate table incidents, checks, sites, refresh_tokens, users "
                "restart identity cascade"
            )
        )
    async with session_factory() as session:
        from app.seed import seed_plans

        await seed_plans(session)
        await session.execute(
            text("truncate table rate_buckets, worker_heartbeats, regions, idempotency_keys")
        )
        await session.commit()
    yield


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client


async def activate(client: httpx.AsyncClient, email: str, password: str) -> None:
    from sqlalchemy import select

    from app.crypto import decrypt_text
    from app.models import MailJob

    async with session_factory() as session:
        job = await session.scalar(
            select(MailJob).where(
                MailJob.recipient == email, MailJob.dedupe_key.startswith("account:signup:")
            )
        )
        assert job
        token = decrypt_text(job.body).split("token=")[1].split()[0]
    response = await client.post(
        "/api/auth/email/confirm", json={"token": token, "password": password}
    )
    assert response.status_code == 204, response.text


@pytest.fixture
async def registered_user(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post(
        "/api/auth/register", json={"email": "owner@example.com", "password": "supersecret1"}
    )
    assert response.status_code == 202, response.text
    await activate(client, "owner@example.com", "supersecret1")
    response = await client.post(
        "/api/auth/login", json={"email": "owner@example.com", "password": "supersecret1"}
    )
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    return {"access_token": token, "email": "owner@example.com", "password": "supersecret1"}


@pytest.fixture
def auth_headers(registered_user: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {registered_user['access_token']}"}


@pytest.fixture
def settings_override() -> type[object]:
    return type(settings)


@pytest.fixture
async def session_factory_fixture() -> AsyncIterator[object]:
    yield session_factory
