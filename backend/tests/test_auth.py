import httpx
import pytest


async def test_register_returns_access_token_and_sets_refresh_cookie(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/auth/register", json={"email": "new@example.com", "password": "supersecret1"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    cookie = response.cookies.get("refresh_token")
    assert cookie
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=lax" in response.headers["set-cookie"].lower()


async def test_register_rejects_duplicate_email(client: httpx.AsyncClient) -> None:
    payload = {"email": "dup@example.com", "password": "supersecret1"}
    first = await client.post("/api/auth/register", json=payload)
    assert first.status_code == 201
    second = await client.post("/api/auth/register", json=payload)
    assert second.status_code == 409
    assert second.json()["code"] == "email_taken"


async def test_register_rejects_short_password(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/auth/register", json={"email": "short@example.com", "password": "abc"}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


async def test_login_with_wrong_password_fails(client: httpx.AsyncClient) -> None:
    await client.post(
        "/api/auth/register", json={"email": "login@example.com", "password": "supersecret1"}
    )
    response = await client.post(
        "/api/auth/login", json={"email": "login@example.com", "password": "wrongpassword"}
    )
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


async def test_refresh_rotates_token(client: httpx.AsyncClient) -> None:
    register = await client.post(
        "/api/auth/register", json={"email": "rotate@example.com", "password": "supersecret1"}
    )
    first_token = register.cookies.get("refresh_token")
    assert first_token

    refreshed = await client.post("/api/auth/refresh")
    assert refreshed.status_code == 200
    second_token = refreshed.cookies.get("refresh_token")
    assert second_token
    assert second_token != first_token
    assert refreshed.json()["access_token"]


async def test_refresh_reuse_revokes_all_tokens(client: httpx.AsyncClient) -> None:
    register = await client.post(
        "/api/auth/register", json={"email": "reuse@example.com", "password": "supersecret1"}
    )
    stale_token = register.cookies.get("refresh_token")
    refreshed = await client.post("/api/auth/refresh")
    fresh_token = refreshed.cookies.get("refresh_token")
    assert fresh_token

    reused = await client.post("/api/auth/refresh", cookies={"refresh_token": stale_token or ""})
    assert reused.status_code == 401
    assert reused.json()["code"] == "refresh_reused"

    after_reuse = await client.post(
        "/api/auth/refresh", cookies={"refresh_token": fresh_token or ""}
    )
    assert after_reuse.status_code == 401
    assert after_reuse.json()["code"] == "refresh_reused"


async def test_refresh_without_cookie_fails(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/auth/refresh")
    assert response.status_code == 401
    assert response.json()["code"] == "refresh_missing"


async def test_logout_revokes_refresh_token(client: httpx.AsyncClient) -> None:
    register = await client.post(
        "/api/auth/register", json={"email": "logout@example.com", "password": "supersecret1"}
    )
    token = register.cookies.get("refresh_token")
    logout = await client.post("/api/auth/logout")
    assert logout.status_code == 204

    response = await client.post("/api/auth/refresh", cookies={"refresh_token": token or ""})
    assert response.status_code == 401


async def test_me_requires_valid_access_token(
    client: httpx.AsyncClient, registered_user: dict[str, str]
) -> None:
    unauthorized = await client.get("/api/me")
    assert unauthorized.status_code == 401
    assert unauthorized.json()["code"] == "unauthorized"

    authorized = await client.get(
        "/api/me", headers={"Authorization": f"Bearer {registered_user['access_token']}"}
    )
    assert authorized.status_code == 200
    assert authorized.json()["email"] == "owner@example.com"
    assert authorized.json()["tariff"] == "free"


@pytest.mark.parametrize("bad_token", ["Bearer", "Bearer nonsense", "Basic abc"])
async def test_invalid_authorization_headers_rejected(
    client: httpx.AsyncClient, bad_token: str
) -> None:
    response = await client.get("/api/me", headers={"Authorization": bad_token})
    assert response.status_code == 401
