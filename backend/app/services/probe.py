import asyncio
import ipaddress
import logging
import socket
import ssl
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import certifi
import httpx

from app.config import settings
from app.ssrf import resolve_host

logger = logging.getLogger("pulsewatch.probe")

USER_AGENT = "pulsewatch/1.0"
MAX_REDIRECTS = 5
MAX_ERROR_LENGTH = 480
SUCCESS_STATUS_RANGE = range(200, 400)


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    status_code: int | None
    response_ms: int | None
    error: str | None


class RedirectRejected(Exception):
    """Raised when a redirect hop points at a target the SSRF policy forbids."""


def shorten_message(message: str) -> str:
    cleaned = " ".join(message.split())
    return cleaned[:MAX_ERROR_LENGTH]


def describe_exception(exc: Exception) -> str:
    text = str(exc).strip()
    return shorten_message(text or exc.__class__.__name__)


def _is_blocked_address(candidate: str) -> bool:
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return True
    from app.ssrf import is_blocked_ip

    return is_blocked_ip(address)


async def _redirect_allowed(url: str, allow_private: bool) -> bool:
    if allow_private:
        return True
    parsed = urlparse(url)
    if parsed.hostname is None:
        return False
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    try:
        addresses = await resolve_host(parsed.hostname, port)
    except Exception:
        return False
    return all(not _is_blocked_address(candidate) for candidate in addresses)


async def _fetch_following_redirects(url: str, allow_private: bool) -> httpx.Response:
    current_url = url
    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=settings.check_timeout_seconds,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            if not await _redirect_allowed(current_url, allow_private):
                raise RedirectRejected(
                    f"redirect target {urlparse(current_url).hostname} is not allowed"
                )
            response = await client.get(current_url)
            if not response.is_redirect:
                return response
            location = response.headers.get("location")
            if not location:
                return response
            current_url = str(response.url.join(location))
    raise httpx.TooManyRedirects(f"more than {MAX_REDIRECTS} redirects", request=None)  # type: ignore[arg-type]


async def probe_site(url: str, allow_private: bool) -> ProbeResult:
    started = time.perf_counter()
    try:
        response = await _fetch_following_redirects(url, allow_private)
    except RedirectRejected as exc:
        return ProbeResult(False, None, None, describe_exception(exc))
    except httpx.TimeoutException:
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


def _read_peer_certificate(hostname: str, port: int) -> dict[str, Any] | None:
    # OpenSSL on Windows ships no trust store, so the system CAs are invisible without certifi.
    context = ssl.create_default_context(cafile=certifi.where())
    with (
        socket.create_connection((hostname, port), timeout=settings.check_timeout_seconds) as raw,
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
        certificate = await loop.run_in_executor(
            None, _read_peer_certificate, parsed.hostname, port
        )
    except (OSError, ssl.SSLError) as exc:
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
