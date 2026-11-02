import httpx
import pytest

PUBLIC_TARGET = "http://93.184.216.34/"


async def create_site(
    client: httpx.AsyncClient, headers: dict[str, str], name: str = "site", url: str = PUBLIC_TARGET
) -> dict[str, object]:
    response = await client.post(
        "/api/sites",
        json={"name": name, "url": url, "interval_seconds": 60},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


async def test_create_and_list_site(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = await create_site(client, auth_headers, name="docs", url="http://93.184.216.34/docs")
    assert created["name"] == "docs"
    assert created["status"] == "unknown"
    assert created["interval_seconds"] == 60
    assert created["public_slug"]
    assert created["is_public"] is False
    assert created["uptime_24h"] is None

    listed = await client.get("/api/sites", headers=auth_headers)
    assert listed.status_code == 200
    assert [site["name"] for site in listed.json()] == ["docs"]


async def test_site_interval_below_minimum_rejected(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/sites",
        json={"name": "fast", "url": PUBLIC_TARGET, "interval_seconds": 30},
        headers=auth_headers,
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


async def test_free_tariff_limit_enforced(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    for index in range(5):
        await create_site(client, auth_headers, name=f"site-{index}")

    response = await client.post(
        "/api/sites",
        json={"name": "overflow", "url": PUBLIC_TARGET, "interval_seconds": 60},
        headers=auth_headers,
    )
    assert response.status_code == 403
    assert response.json()["code"] == "tariff_limit_reached"


async def test_site_owner_isolation(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = await create_site(client, auth_headers)
    site_id = created["id"]

    other = await client.post(
        "/api/auth/register", json={"email": "other@example.com", "password": "supersecret1"}
    )
    other_headers = {"Authorization": f"Bearer {other.json()['access_token']}"}

    read = await client.get(f"/api/sites/{site_id}", headers=other_headers)
    assert read.status_code == 404
    assert read.json()["code"] == "site_not_found"

    update = await client.patch(f"/api/sites/{site_id}", json={"name": "x"}, headers=other_headers)
    assert update.status_code == 404

    delete = await client.delete(f"/api/sites/{site_id}", headers=other_headers)
    assert delete.status_code == 404


async def test_update_and_delete_site(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = await create_site(client, auth_headers)
    site_id = created["id"]

    updated = await client.patch(
        f"/api/sites/{site_id}",
        json={"name": "renamed", "interval_seconds": 600, "is_public": True},
        headers=auth_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "renamed"
    assert updated.json()["interval_seconds"] == 600
    assert updated.json()["is_public"] is True

    deleted = await client.delete(f"/api/sites/{site_id}", headers=auth_headers)
    assert deleted.status_code == 204

    missing = await client.get(f"/api/sites/{site_id}", headers=auth_headers)
    assert missing.status_code == 404


async def test_dashboard_summary_groups_statuses(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    await create_site(client, auth_headers, name="alpha")
    await create_site(client, auth_headers, name="beta")

    response = await client.get("/api/dashboard/summary", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total_sites"] == 2
    assert body["unknown_count"] == 2
    assert body["up_count"] == 0
    assert body["down_count"] == 0
    assert body["average_response_ms"] is None
    assert body["open_incidents"] == 0
    assert len(body["sites"]) == 2
    assert body["sites"][0]["sparkline"] == []


async def test_checks_range_rejects_unknown_value(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = await create_site(client, auth_headers)
    site_id = created["id"]
    response = await client.get(f"/api/sites/{site_id}/checks?range=30d", headers=auth_headers)
    assert response.status_code == 422


@pytest.mark.parametrize("range_key", ["24h", "7d"])
async def test_checks_range_accepts_supported_values(
    client: httpx.AsyncClient, auth_headers: dict[str, str], range_key: str
) -> None:
    created = await create_site(client, auth_headers)
    site_id = created["id"]
    response = await client.get(
        f"/api/sites/{site_id}/checks?range={range_key}", headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json() == []
