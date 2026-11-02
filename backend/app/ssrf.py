import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

from app.errors import ApiError

ALLOWED_SCHEMES = {"http", "https"}
METADATA_ADDRESSES = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("fd00:ec2::254"),
    ipaddress.ip_address("100.100.100.200"),
}


def is_blocked_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if address in METADATA_ADDRESSES:
        return True
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return is_blocked_ip(address.ipv4_mapped)
    return (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def normalize_url(raw_url: str) -> str:
    value = raw_url.strip()
    parsed = urlparse(value)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise ApiError(400, "invalid_url", "URL must use http or https scheme")
    if not parsed.hostname:
        raise ApiError(400, "invalid_url", "URL must contain a host")
    if len(value) > 2048:
        raise ApiError(400, "invalid_url", "URL must be 2048 characters or shorter")
    return value


async def resolve_host(hostname: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.run_in_executor(
            None, lambda: socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
        )
    except socket.gaierror as exc:
        raise ApiError(400, "unresolvable_host", f"Host {hostname} cannot be resolved") from exc
    addresses: list[str] = []
    for info in infos:
        candidate = str(info[4][0])
        if candidate not in addresses:
            addresses.append(candidate)
    if not addresses:
        raise ApiError(400, "unresolvable_host", f"Host {hostname} has no addresses")
    return addresses


async def ensure_target_allowed(url: str, allow_private: bool) -> None:
    if allow_private:
        return
    parsed = urlparse(url)
    if not parsed.hostname:
        raise ApiError(400, "invalid_url", "URL must contain a host")
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    addresses = await resolve_host(parsed.hostname, port)
    for candidate in addresses:
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError as exc:
            raise ApiError(
                400, "unresolvable_host", f"Host resolved to invalid address {candidate}"
            ) from exc
        if is_blocked_ip(address):
            raise ApiError(403, "forbidden_target", "Target address is not allowed")
