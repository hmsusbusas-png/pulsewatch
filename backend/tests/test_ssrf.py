import httpx
import pytest

from app import ssrf
from app.errors import ApiError
from app.ssrf import ensure_target_allowed, is_blocked_ip, normalize_url


async def test_dns_timeout_returns_target_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def timed_out(*args: object, **kwargs: object) -> None:
        raise TimeoutError

    monkeypatch.setattr(ssrf.asyncio, "wait_for", timed_out)
    with pytest.raises(ApiError) as error:
        await ensure_target_allowed("https://example.com", allow_private=False)
    assert error.value.status_code == 400
    assert error.value.code == "unresolvable_host"


def test_private_ranges_are_blocked() -> None:
    import ipaddress

    blocked = [
        "127.0.0.1",
        "10.0.0.5",
        "192.168.1.10",
        "172.16.4.4",
        "169.254.169.254",
        "0.0.0.0",
        "::1",
        "fd00:ec2::254",
    ]
    for candidate in blocked:
        assert is_blocked_ip(ipaddress.ip_address(candidate)), candidate


def test_public_addresses_are_allowed() -> None:
    import ipaddress

    for candidate in ["93.184.216.34", "8.8.8.8", "2606:2800:220:1:248:1893:25c8:1946"]:
        assert not is_blocked_ip(ipaddress.ip_address(candidate)), candidate


def test_normalize_url_rejects_non_http_schemes() -> None:
    with pytest.raises(ApiError) as exc_info:
        normalize_url("ftp://example.com")
    assert exc_info.value.code == "invalid_url"

    with pytest.raises(ApiError):
        normalize_url("file:///etc/passwd")

    with pytest.raises(ApiError):
        normalize_url("http://")


def test_normalize_url_rejects_oversized_url() -> None:
    with pytest.raises(ApiError):
        normalize_url("https://example.com/" + "a" * 2050)


async def test_ensure_target_allowed_blocks_localhost() -> None:
    with pytest.raises(ApiError) as exc_info:
        await ensure_target_allowed("http://localhost:8000/", allow_private=False)
    assert exc_info.value.code == "forbidden_target"


async def test_ensure_target_allowed_blocks_private_ip_literal() -> None:
    for target in [
        "http://192.168.1.5/",
        "http://10.0.0.1/",
        "http://169.254.169.254/latest/meta-data",
    ]:
        with pytest.raises(ApiError) as exc_info:
            await ensure_target_allowed(target, allow_private=False)
        assert exc_info.value.code == "forbidden_target"


async def test_ensure_target_allowed_permits_private_when_flag_enabled() -> None:
    await ensure_target_allowed("http://127.0.0.1:8000/", allow_private=True)


async def test_create_site_rejects_localhost(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/sites",
        json={"name": "local", "url": "http://localhost:9000/", "interval_seconds": 60},
        headers=auth_headers,
    )
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden_target"


async def test_create_site_rejects_private_network(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/sites",
        json={"name": "lan", "url": "http://192.168.1.20/", "interval_seconds": 60},
        headers=auth_headers,
    )
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden_target"


async def test_update_site_url_runs_ssrf_check(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = await client.post(
        "/api/sites",
        json={"name": "public", "url": "http://93.184.216.34/", "interval_seconds": 60},
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    site_id = created.json()["id"]

    response = await client.patch(
        f"/api/sites/{site_id}", json={"url": "http://127.0.0.1/"}, headers=auth_headers
    )
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden_target"
