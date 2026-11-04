import re
import time
from typing import TYPE_CHECKING

import dns.asyncresolver
import dns.exception
import dns.resolver

from app.config import settings
from app.errors import ApiError

if TYPE_CHECKING:
    from app.models import Site
    from app.services.probe import ProbeResult

RECORD_TYPES = ("A", "AAAA", "CNAME", "MX", "TXT", "NS")
SPECIAL_SUFFIXES = (
    "localhost",
    "local",
    "internal",
    "intranet",
    "lan",
    "home",
    "corp",
    "home.arpa",
    "in-addr.arpa",
    "ip6.arpa",
    "test",
    "invalid",
    "example",
    "onion",
)
_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


def public_hostname(value: str) -> str:
    name = value.strip().lower().rstrip(".")
    if "://" in name:
        raise ApiError(
            422, "invalid_hostname", "Для DNS укажите имя без схемы, например example.com"
        )
    try:
        name = name.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ApiError(422, "invalid_hostname", "Некорректное доменное имя") from exc
    labels = name.split(".")
    if (
        len(name) > 253
        or len(labels) < 2
        or not all(_LABEL.match(label) for label in labels)
        or labels[-1].isdigit()
        or any(name == suffix or name.endswith(f".{suffix}") for suffix in SPECIAL_SUFFIXES)
    ):
        raise ApiError(
            422, "invalid_hostname", "Нужно публичное доменное имя, например example.com"
        )
    return name


def _normalize(value: str) -> str:
    return value.strip().strip('"').rstrip(".").lower()


async def resolve_records(name: str, record_type: str, lifetime: float) -> list[str]:
    resolver = dns.asyncresolver.Resolver()
    if settings.dns_resolvers:
        resolver.nameservers = list(settings.dns_resolvers)
    answer = await resolver.resolve(name, record_type, lifetime=lifetime)
    values = []
    for record in answer:
        if record_type == "MX":
            values.append(f"{record.preference} {_normalize(record.exchange.to_text())}")
        elif record_type == "TXT":
            values.append(b"".join(record.strings).decode("utf-8", "replace"))
        else:
            values.append(_normalize(record.to_text()))
    return sorted(values)


async def probe_dns(site: "Site", started: float) -> "ProbeResult":
    from app.services.probe import ProbeResult, Timings

    record_type = site.dns_record_type or "A"
    try:
        values = await resolve_records(site.url, record_type, float(site.timeout_seconds))
    except dns.resolver.NXDOMAIN:
        return ProbeResult(False, None, None, "NXDOMAIN: имя не существует", records=())
    except dns.resolver.NoAnswer:
        return ProbeResult(False, None, None, f"Нет записей {record_type}", records=())
    except (dns.exception.Timeout, dns.resolver.LifetimeTimeout):
        return ProbeResult(False, None, None, "Таймаут DNS-запроса")
    except dns.exception.DNSException as exc:
        return ProbeResult(False, None, None, f"Ошибка DNS: {type(exc).__name__}")
    elapsed = int((time.perf_counter() - started) * 1000)
    timings = Timings(dns_ms=elapsed)
    expected = _normalize(site.dns_expected or "")
    if expected and not any(expected in _normalize(value) for value in values):
        return ProbeResult(
            False,
            None,
            elapsed,
            f"Ожидалось значение «{site.dns_expected}», получено: {', '.join(values)[:300]}",
            timings,
            records=tuple(values),
        )
    return ProbeResult(True, None, elapsed, None, timings, records=tuple(values))
