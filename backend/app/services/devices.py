import hashlib
import hmac
import ipaddress
import re
from dataclasses import dataclass

from starlette.requests import HTTPConnection

from app.config import settings

BROWSERS = (
    ("YaBrowser", "Яндекс Браузер"),
    ("Edg", "Edge"),
    ("EdgA", "Edge"),
    ("OPR", "Opera"),
    ("SamsungBrowser", "Samsung Internet"),
    ("Firefox", "Firefox"),
    ("FxiOS", "Firefox"),
    ("CriOS", "Chrome"),
    ("Chrome", "Chrome"),
    ("Version", "Safari"),
    ("curl", "curl"),
    ("python-httpx", "httpx"),
    ("python-requests", "requests"),
    ("PostmanRuntime", "Postman"),
)
_VERSION = re.compile(r"(?P<name>[A-Za-z-]+)/(?P<major>\d+)")


@dataclass(frozen=True)
class Device:
    browser: str | None
    os: str | None


def describe_agent(user_agent: str | None) -> Device:
    if not user_agent:
        return Device(None, None)
    versions = {match["name"]: match["major"] for match in _VERSION.finditer(user_agent)}
    browser = None
    for marker, label in BROWSERS:
        if marker in versions and (marker != "Version" or "Safari" in versions):
            browser = f"{label} {versions[marker]}"
            break
    return Device(browser, _operating_system(user_agent))


def _operating_system(user_agent: str) -> str | None:
    if match := re.search(r"Android (\d+)", user_agent):
        return f"Android {match[1]}"
    if match := re.search(r"(?:iPhone|CPU) OS (\d+)", user_agent):
        return f"iOS {match[1]}"
    if "iPad" in user_agent:
        return "iPadOS"
    if "Windows NT 10.0" in user_agent:
        return "Windows 10/11"
    if "Windows" in user_agent:
        return "Windows"
    if match := re.search(r"Mac OS X (\d+)[._](\d+)", user_agent):
        return f"macOS {match[1]}.{match[2]}"
    if "CrOS" in user_agent:
        return "ChromeOS"
    if "Linux" in user_agent:
        return "Linux"
    return None


def client_ip(connection: HTTPConnection) -> str | None:
    direct = connection.client.host if connection.client else None
    if direct is None or not settings.trusted_proxies:
        return direct
    networks = [ipaddress.ip_network(item, strict=False) for item in settings.trusted_proxies]

    def trusted(value: str) -> bool:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            return False
        return any(address in network for network in networks)

    if not trusted(direct):
        return direct
    chain = [part.strip() for part in connection.headers.get("x-forwarded-for", "").split(",")]
    for candidate in reversed([part for part in chain if part]):
        if not trusted(candidate):
            return candidate
    return direct


def mask_ip(value: str | None) -> str | None:
    if not value:
        return None
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    if isinstance(address, ipaddress.IPv4Address):
        return ".".join([*str(address).split(".")[:3], "x"])
    groups = address.exploded.split(":")[:3]
    return ":".join(group.lstrip("0") or "0" for group in groups) + "::x"


def hash_ip(value: str | None) -> str | None:
    if not value:
        return None
    return hmac.new(settings.jwt_secret.encode(), value.encode(), hashlib.sha256).hexdigest()
