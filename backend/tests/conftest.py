import os
from collections.abc import AsyncIterator
from pathlib import Path

TEST_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:5433/pulsewatch_test"

os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["ALLOW_PRIVATE_TARGETS"] = "false"
os.environ["COOKIE_SECURE"] = "false"
os.environ["JWT_SECRET"] = "test-secret"
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["AUTH_RATE_LIMIT_PER_MINUTE"] = "10000"

import httpx  # noqa: E402
import pytest  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import text  # noqa: E402

from alembic import command  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import engine, session_factory  # noqa: E402
from app.main import app  # noqa: E402

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
    yield


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client


@pytest.fixture
async def registered_user(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post(
        "/api/auth/register", json={"email": "owner@example.com", "password": "supersecret1"}
    )
    assert response.status_code == 201, response.text
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
