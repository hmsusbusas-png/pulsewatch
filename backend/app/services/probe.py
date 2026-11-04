import asyncio
import ipaddress
import logging
import socket
import ssl
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import certifi
import httpx

from app.config import settings
from app.errors import ApiError
from app.ssrf import resolve_host

if TYPE_CHECKING:
    from app.models import Site
logger = logging.getLogger("pulsewatch.probe")
USER_AGENT = "pulsewatch/1.0"
MAX_REDIRECTS = 5
MAX_ERROR_LENGTH = 480
SUCCESS_STATUS_RANGE = range(200, 400)
EVIDENCE_BYTES = 4096
TCP_BANNER_BYTES = 4096


@dataclass(frozen=True)
class Timings:
    dns_ms: int | None = None
    connect_ms: int | None = None
    tls_ms: int | None = None
    ttfb_ms: int | None = None
    transfer_ms: int | None = None

    def as_dict(self) -> dict[str, int | None]:
        return {
            "dns_ms": self.dns_ms,
            "connect_ms": self.connect_ms,
            "tls_ms": self.tls_ms,
            "ttfb_ms": self.ttfb_ms,
            "transfer_ms": self.transfer_ms,
        }


@dataclass(frozen=True)
class Evidence:
    ip: str | None
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    status_code: int | None
    response_ms: int | None
    error: str | None
    timings: Timings | None = None
    evidence: Evidence | None = None
    records: tuple[str, ...] | None = None


_trust: tuple[tuple[str, float], ssl.SSLContext] | None = None


def trust_context() -> ssl.SSLContext:
    global _trust
    extra = Path(settings.extra_ca_file) if settings.extra_ca_file else None
    stamp = (settings.extra_ca_file, extra.stat().st_mtime if extra and extra.is_file() else 0.0)
    if _trust is None or _trust[0] != stamp:
        context = ssl.create_default_context(cafile=certifi.where())
        if extra is not None and stamp[1]:
            context.load_verify_locations(cafile=str(extra))
        _trust = (stamp, context)
    return _trust[1]


class RedirectRejected(Exception):
    pass


class PhaseClock:
    def __init__(self) -> None:
        self.marks: dict[str, float] = {}
        self.dns_seconds: float | None = None
        self.ip: str | None = None
        self.body_done: float | None = None

    def restart(self, dns_seconds: float, ip: str) -> None:
        self.marks.clear()
        self.body_done = None
        self.dns_seconds = dns_seconds
        self.ip = ip

    async def __call__(self, event: str, info: dict[str, Any]) -> None:
        self.marks[event] = time.perf_counter()

    def _span(self, start: str, end: str) -> int | None:
        if start in self.marks and end in self.marks:
            return max(0, round((self.marks[end] - self.marks[start]) * 1000))
        return None

    def timings(self) -> Timings:
        headers_done = self.marks.get("http11.receive_response_headers.complete")
        transfer = (
            max(0, round((self.body_done - headers_done) * 1000))
            if self.body_done is not None and headers_done is not None
            else None
        )
        return Timings(
            dns_ms=round(self.dns_seconds * 1000) if self.dns_seconds is not None else None,
            connect_ms=self._span(
                "connection.connect_tcp.started", "connection.connect_tcp.complete"
            ),
            tls_ms=self._span("connection.start_tls.started", "connection.start_tls.complete"),
            ttfb_ms=self._span(
                "http11.send_request_headers.started", "http11.receive_response_headers.complete"
            ),
            transfer_ms=transfer,
        )


def shorten_message(message: str) -> str:
    cleaned = " ".join(message.split())
    return cleaned[:MAX_ERROR_LENGTH]


def describe_exception(exc: BaseException) -> str:
    text = str(exc).strip()
    return shorten_message(text or exc.__class__.__name__)


def _is_blocked_address(candidate: str) -> bool:
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return True
    from app.ssrf import is_blocked_ip

    return is_blocked_ip(address)


async def target_ip(url: str, allow_private: bool) -> str:
    from app.ssrf import normalize_url

    normalize_url(url)
    parsed = urlparse(url)
    addresses = await resolve_host(
        str(parsed.hostname), parsed.port or (443 if parsed.scheme == "https" else 80)
    )
    if not allow_private and any(_is_blocked_address(address) for address in addresses):
        raise RedirectRejected("target address is not allowed")
    return addresses[0]


async def safe_request(
    url: str,
    allow_private: bool = False,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: str | bytes | None = None,
    request_timeout: int = 10,
    follow: bool = True,
    *,
    clock: PhaseClock | None = None,
) -> httpx.Response:
    current = httpx.URL(url)
    supplied = dict(headers or {})
    content = body.encode() if isinstance(body, str) else body
    async with asyncio.timeout(request_timeout):
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=request_timeout,
            trust_env=False,
            verify=trust_context(),
        ) as client:
            for _ in range(MAX_REDIRECTS + 1):
                resolving = time.perf_counter()
                address = await target_ip(str(current), allow_private)
                if clock is not None:
                    clock.restart(time.perf_counter() - resolving, address)
                port = current.port or (443 if current.scheme == "https" else 80)
                host = f"[{current.host}]" if ":" in current.host else current.host
                authority = (
                    host if port == (443 if current.scheme == "https" else 80) else f"{host}:{port}"
                )
                request_headers = {**supplied, "Host": authority, "User-Agent": USER_AGENT}
                pinned = current.copy_with(host=address)
                client.cookies.clear()
                request_headers["Accept-Encoding"] = "identity"
                extensions: dict[str, Any] = {"sni_hostname": current.host}
                if clock is not None:
                    extensions["trace"] = clock
                async with client.stream(
                    method,
                    pinned,
                    headers=request_headers,
                    content=content,
                    extensions=extensions,
                ) as response:
                    chunks = bytearray()
                    async for chunk in response.aiter_raw():
                        chunks.extend(chunk[: 1048576 - len(chunks)])
                        if len(chunks) >= 1048576:
                            break
                    if clock is not None:
                        clock.body_done = time.perf_counter()
                    response_headers = dict(response.headers)
                    response_headers.pop("content-encoding", None)
                    response_headers.pop("content-length", None)
                    result = httpx.Response(
                        response.status_code,
                        headers=response_headers,
                        content=bytes(chunks),
                        request=httpx.Request(method, current),
                    )
                location = result.headers.get("location")
                if not follow or not result.is_redirect or not location:
                    return result
                redirected = current.join(location)
                if (current.scheme, current.host, current.port) != (
                    redirected.scheme,
                    redirected.host,
                    redirected.port,
                ):
                    supplied = {}
                if result.status_code == 303 or (
                    result.status_code in (301, 302) and method == "POST"
                ):
                    method, content = "GET", None
                current = redirected
        raise RedirectRejected("too many redirects")


async def _fetch_following_redirects(url: str, allow_private: bool) -> httpx.Response:
    return await safe_request(
        url, allow_private, request_timeout=int(settings.check_timeout_seconds)
    )


async def probe_site(url: str, allow_private: bool) -> ProbeResult:
    started = time.perf_counter()
    try:
        response = await _fetch_following_redirects(url, allow_private)
    except (RedirectRejected, ApiError) as exc:
        return ProbeResult(False, None, None, describe_exception(exc))
    except (httpx.TimeoutException, TimeoutError):
        return ProbeResult(False, None, None, "request timed out")
    except httpx.TooManyRedirects as exc:
        return ProbeResult(False, None, None, describe_exception(exc))
    except ssl.SSLError as exc:
        reason = exc.reason if isinstance(exc.reason, str) else exc.__class__.__name__
        return ProbeResult(False, None, None, shorten_message(f"tls error: {reason}"))
    except httpx.HTTPError as exc:
        return ProbeResult(False, None, None, describe_exception(exc))
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    ok = response.status_code in SUCCESS_STATUS_RANGE
    error = None if ok else f"unexpected status code {response.status_code}"
    return ProbeResult(ok, response.status_code, elapsed_ms, error)


def _read_peer_certificate(hostname: str, port: int, address: str) -> dict[str, Any] | None:
    context = trust_context()
    with (
        socket.create_connection((address, port), timeout=settings.check_timeout_seconds) as raw,
        context.wrap_socket(raw, server_hostname=hostname) as tls_socket,
    ):
        certificate = tls_socket.getpeercert()
    return certificate if isinstance(certificate, dict) else None


async def fetch_ssl_expiry(url: str) -> datetime | None:
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or parsed.hostname is None:
        return None
    port = parsed.port or 443
    loop = asyncio.get_running_loop()
    try:
        address = await target_ip(url, settings.allow_private_targets)
        certificate = await loop.run_in_executor(
            None, _read_peer_certificate, parsed.hostname, port, address
        )
    except (OSError, ssl.SSLError, ApiError, RedirectRejected) as exc:
        logger.warning(
            "ssl_probe_failed",
            extra={"extra_fields": {"hostname": parsed.hostname, "error": describe_exception(exc)}},
        )
        return None
    if not certificate:
        return None
    not_after = certificate.get("notAfter")
    if not isinstance(not_after, str):
        return None
    try:
        expires = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
    except ValueError:
        logger.warning(
            "ssl_expiry_unparsed",
            extra={"extra_fields": {"hostname": parsed.hostname, "value": not_after}},
        )
        return None
    return expires


def _elapsed(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


async def _probe_tcp(site: "Site", started: float) -> ProbeResult:
    parsed = urlparse(site.url)
    resolving = time.perf_counter()
    address = await target_ip(site.url, settings.allow_private_targets)
    dns_ms = _elapsed(resolving)
    connecting = time.perf_counter()
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(address, parsed.port or 443), site.timeout_seconds
    )
    connect_ms = _elapsed(connecting)
    banner = b""
    try:
        if site.tcp_send:
            writer.write(site.tcp_send.encode().replace(b"\\r\\n", b"\r\n").replace(b"\\n", b"\n"))
            await writer.drain()
        if site.tcp_expect:
            exchanging = time.perf_counter()
            while site.tcp_expect.encode() not in banner and len(banner) < TCP_BANNER_BYTES:
                chunk = await asyncio.wait_for(
                    reader.read(TCP_BANNER_BYTES - len(banner)), site.timeout_seconds
                )
                if not chunk:
                    break
                banner += chunk
            ttfb_ms = _elapsed(exchanging)
        else:
            ttfb_ms = None
    finally:
        writer.close()
        await writer.wait_closed()
    timings = Timings(dns_ms=dns_ms, connect_ms=connect_ms, ttfb_ms=ttfb_ms)
    evidence = Evidence(ip=address, body=banner[:EVIDENCE_BYTES])
    if site.tcp_expect and site.tcp_expect.encode() not in banner:
        return ProbeResult(
            False,
            None,
            _elapsed(started),
            "Ответ сервера не содержит ожидаемую строку",
            timings,
            evidence,
        )
    return ProbeResult(True, None, _elapsed(started), None, timings, evidence)


async def probe_monitor(site: "Site", headers: dict[str, str] | None = None) -> ProbeResult:
    from app.crypto import decrypt_config
    from app.services.assertions import evaluate_assertions

    if headers is None:
        headers = decrypt_config(site.headers_encrypted) if site.headers_encrypted else {}

    started = time.perf_counter()
    try:
        if site.monitor_type == "tcp":
            return await _probe_tcp(site, started)
        if site.monitor_type == "dns":
            from app.services.dns_probe import probe_dns

            return await probe_dns(site, started)
        if site.monitor_type == "ssl-only":
            expiry = await fetch_ssl_expiry(site.url)
            return ProbeResult(
                expiry is not None and expiry > datetime.now(UTC),
                None,
                None,
                None if expiry is not None else "Ошибка сертификата TLS",
            )
        clock = PhaseClock()
        response = await safe_request(
            site.url,
            settings.allow_private_targets,
            site.method,
            headers,
            site.body,
            site.timeout_seconds,
            site.follow_redirects,
            clock=clock,
        )
        elapsed = _elapsed(started)
        evidence = Evidence(
            ip=clock.ip, headers=dict(response.headers), body=response.content[:EVIDENCE_BYTES]
        )
        timings = clock.timings() if clock.ip is not None else None
        ok = response.status_code in site.expected_codes
        reason = None if ok else f"HTTP {response.status_code}"
        if ok and site.monitor_type == "keyword":
            ok = ((site.keyword or "") in response.text) == site.keyword_present
            reason = None if ok else "Условие ключевого слова не выполнено"
        if ok and site.assertions:
            failed = [
                outcome
                for outcome in evaluate_assertions(
                    site.assertions, dict(response.headers), response.text
                )
                if not outcome.passed
            ]
            if failed:
                ok = False
                reason = shorten_message(f"Условие не выполнено: {failed[0].message}")
        return ProbeResult(ok, response.status_code, elapsed, reason, timings, evidence)
    except (ApiError, RedirectRejected, httpx.HTTPError, OSError, TimeoutError) as exc:
        return ProbeResult(False, None, None, describe_exception(exc))
