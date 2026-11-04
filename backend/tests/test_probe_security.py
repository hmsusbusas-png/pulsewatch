import httpx
import pytest

from app.services import probe
from app.ssrf import normalize_url


@pytest.mark.parametrize(
    "url",
    ["http://user:password@8.8.8.8/", "http://8.8.8.8:bad", "http://8.8.8.8:0", "file:///tmp/file"],
)
def test_url_validation(url: str) -> None:
    from app.errors import ApiError

    with pytest.raises(ApiError):
        normalize_url(url)


async def test_dns_pinned_sni_and_body_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    resolutions = 0

    async def resolve(host: str, port: int) -> list[str]:
        nonlocal resolutions
        resolutions += 1
        return ["8.8.8.8"]

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, stream=httpx.ByteStream(b"x" * (2 * 1048576)))

    real_client = httpx.AsyncClient
    monkeypatch.setattr(probe, "resolve_host", resolve)
    monkeypatch.setattr("app.ssrf.resolve_host", resolve)
    monkeypatch.setattr(
        probe.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw),
    )
    result = await probe.safe_request("https://monitor.example.org/a")
    assert len(result.content) == 1048576
    assert calls[0].url.host == "8.8.8.8"
    assert calls[0].headers["host"] == "monitor.example.org"
    assert calls[0].extensions["sni_hostname"] == "monitor.example.org"


async def test_redirect_strips_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def target(url: str, allow: bool) -> str:
        return "8.8.8.8"

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return (
            httpx.Response(
                302,
                headers={"location": "https://other.example.org/final"},
                stream=httpx.ByteStream(b""),
            )
            if len(calls) == 1
            else httpx.Response(200, stream=httpx.ByteStream(b""))
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(probe, "target_ip", target)
    monkeypatch.setattr(
        probe.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw),
    )
    await probe.safe_request("https://first.example.org/", headers={"Authorization": "secret"})
    assert calls[0].headers["authorization"] == "secret"
    assert "authorization" not in calls[1].headers


async def test_rebinding_to_private_fails_before_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    async def public(host: str, port: int) -> list[str]:
        return ["8.8.8.8"]

    async def private(host: str, port: int) -> list[str]:
        return ["127.0.0.1"]

    monkeypatch.setattr("app.ssrf.resolve_host", public)
    monkeypatch.setattr(probe, "resolve_host", private)
    with pytest.raises(probe.RedirectRejected):
        await probe.target_ip("http://monitor.example.org/", False)
