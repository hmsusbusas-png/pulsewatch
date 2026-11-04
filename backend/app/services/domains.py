import logging
import time
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx

from app.errors import ApiError
from app.services.probe import RedirectRejected, safe_request

logger = logging.getLogger("pulsewatch.domains")
BOOTSTRAP_URL = "https://data.iana.org/rdap/dns.json"
BOOTSTRAP_TTL = 86400
SECOND_LEVEL = {
    "co.uk",
    "org.uk",
    "ac.uk",
    "com.au",
    "net.au",
    "co.jp",
    "com.br",
    "com.tr",
    "co.nz",
    "com.cn",
    "com.ua",
    "msk.ru",
    "spb.ru",
    "com.ru",
    "net.ru",
    "org.ru",
}
_bootstrap: tuple[float, dict[str, str]] = (0.0, {})


def registrable_domain(hostname: str) -> str:
    labels = hostname.lower().rstrip(".").split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def hostname_of(site_url: str) -> str | None:
    if "://" not in site_url:
        return site_url or None
    return urlparse(site_url).hostname


async def rdap_servers() -> dict[str, str]:
    global _bootstrap
    loaded_at, servers = _bootstrap
    if servers and time.monotonic() - loaded_at < BOOTSTRAP_TTL:
        return servers
    response = await safe_request(BOOTSTRAP_URL, request_timeout=15)
    document = response.json()
    mapping: dict[str, str] = {}
    for tlds, urls in document.get("services", []):
        https = [url for url in urls if url.startswith("https://")]
        if not https:
            continue
        for tld in tlds:
            mapping[tld.lower()] = https[0] if https[0].endswith("/") else f"{https[0]}/"
    _bootstrap = (time.monotonic(), mapping)
    return mapping


def expiration_from(document: dict[str, object]) -> datetime | None:
    events = document.get("events")
    if not isinstance(events, list):
        return None
    for event in events:
        if isinstance(event, dict) and event.get("eventAction") == "expiration":
            raw = str(event.get("eventDate", ""))
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC)
            except ValueError:
                return None
    return None


async def domain_expiry(hostname: str) -> datetime | None:
    domain = registrable_domain(hostname)
    try:
        servers = await rdap_servers()
        base = servers.get(domain.rsplit(".", 1)[-1])
        if base is None:
            return None
        response = await safe_request(
            f"{base}domain/{domain}",
            headers={"Accept": "application/rdap+json"},
            request_timeout=15,
        )
        if response.status_code != 200:
            return None
        document = response.json()
    except (httpx.HTTPError, ValueError, ApiError, RedirectRejected, TimeoutError, OSError) as exc:
        logger.warning(
            "rdap_lookup_failed",
            extra={"extra_fields": {"domain": domain, "kind": type(exc).__name__}},
        )
        return None
    return expiration_from(document) if isinstance(document, dict) else None
