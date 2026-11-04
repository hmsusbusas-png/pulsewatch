import asyncio
import logging
import socket
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

from cryptography import x509
from cryptography.x509.oid import NameOID

from app.config import settings
from app.errors import ApiError
from app.services.probe import RedirectRejected, describe_exception, target_ip, trust_context

logger = logging.getLogger("pulsewatch.certificates")
INCOMPLETE_CHAIN_CODES = {2, 20, 21}


@dataclass(frozen=True)
class CertificateReport:
    expires_at: datetime
    issuer: str
    subject: str
    tls_version: str | None
    cipher: str | None
    chain_complete: bool
    trusted: bool
    problem: str | None

    def details(self) -> dict[str, object]:
        return {
            "issuer": self.issuer,
            "subject": self.subject,
            "expires_at": self.expires_at.isoformat(),
            "tls_version": self.tls_version,
            "cipher": self.cipher,
            "chain_complete": self.chain_complete,
            "trusted": self.trusted,
            "problem": self.problem,
        }


def _name(value: x509.Name) -> str:
    for oid in (NameOID.COMMON_NAME, NameOID.ORGANIZATION_NAME):
        attributes = value.get_attributes_for_oid(oid)
        if attributes:
            return str(attributes[0].value)
    return value.rfc4514_string()


def _handshake(
    hostname: str, port: int, address: str, verify: bool
) -> tuple[bytes, str | None, str | None]:
    if verify:
        context = trust_context()
    else:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    with (
        socket.create_connection((address, port), timeout=settings.check_timeout_seconds) as raw,
        context.wrap_socket(raw, server_hostname=hostname) as tls_socket,
    ):
        der = tls_socket.getpeercert(binary_form=True) or b""
        cipher = tls_socket.cipher()
        return der, tls_socket.version(), cipher[0] if cipher else None


def read_certificate(hostname: str, port: int, address: str) -> CertificateReport:
    problem = None
    trusted = True
    chain_complete = True
    try:
        der, version, cipher = _handshake(hostname, port, address, verify=True)
    except ssl.SSLCertVerificationError as exc:
        trusted = False
        chain_complete = exc.verify_code not in INCOMPLETE_CHAIN_CODES
        problem = str(exc.verify_message or exc.reason or "verification failed")
        der, version, cipher = _handshake(hostname, port, address, verify=False)
    certificate = x509.load_der_x509_certificate(der)
    return CertificateReport(
        expires_at=certificate.not_valid_after_utc,
        issuer=_name(certificate.issuer),
        subject=_name(certificate.subject),
        tls_version=version,
        cipher=cipher,
        chain_complete=chain_complete,
        trusted=trusted,
        problem=problem,
    )


async def inspect_certificate(url: str) -> CertificateReport | None:
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or parsed.hostname is None:
        return None
    try:
        address = await target_ip(url, settings.allow_private_targets)
        return await asyncio.get_running_loop().run_in_executor(
            None, read_certificate, parsed.hostname, parsed.port or 443, address
        )
    except (OSError, ssl.SSLError, ValueError, ApiError, RedirectRejected) as exc:
        logger.warning(
            "certificate_inspection_failed",
            extra={"extra_fields": {"hostname": parsed.hostname, "error": describe_exception(exc)}},
        )
        return None


def days_left(expires_at: datetime, now: datetime | None = None) -> int:
    return int((expires_at - (now or datetime.now(UTC))).total_seconds() // 86400)
