import asyncio
import contextlib
import logging
import random
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from app.config import settings
from app.demo_model import SHAPES, latency

logger = logging.getLogger("pulsewatch.demo_target")
rng = random.Random()
STATE = {"payments": False, "api": True, "shop": True}
SHOP_PAGE = (
    "<!doctype html><html lang=ru><meta charset=utf-8><title>Полка. Книги и канцелярия</title>"
    "<h1>Полка</h1><p>Каталог обновлён.</p></html>"
)


async def pause(shape_key: str) -> None:
    await asyncio.sleep(latency(SHAPES[shape_key], datetime.now(UTC), rng) / 1000)


async def api_health(_: Request) -> Response:
    if not STATE["api"]:
        return JSONResponse({"status": "error", "detail": "connection pool exhausted"}, 503)
    await pause("api")
    return JSONResponse(
        {"status": "ok", "version": "2.14.3", "db": "ok", "queue_lag_ms": rng.randint(4, 30)}
    )


async def shop(_: Request) -> Response:
    if not STATE["shop"]:
        return HTMLResponse("<h1>502 Bad Gateway</h1>", 502)
    await pause("shop")
    return HTMLResponse(SHOP_PAGE)


async def payments(_: Request) -> Response:
    if not STATE["payments"]:
        await asyncio.sleep(rng.uniform(0.03, 0.09))
        return JSONResponse(
            {"accepting": False, "reason": "upstream provider timeout", "retry_after": 30}, 503
        )
    await pause("payments")
    return JSONResponse({"accepting": True, "provider": "demo-acquirer"})


async def control(request: Request) -> Response:
    target = request.path_params["target"]
    if target not in STATE:
        return JSONResponse({"error": "unknown target", "targets": sorted(STATE)}, 404)
    STATE[target] = request.path_params["state"] == "up"
    logger.warning("demo_state_changed %s=%s", target, STATE[target])
    return JSONResponse(STATE)


async def hook(request: Request) -> Response:
    payload = await request.body()
    logger.info("demo_hook_received %s %d bytes", request.path_params["name"], len(payload))
    return JSONResponse({"ok": True})


async def index(_: Request) -> Response:
    return JSONResponse({"targets": STATE})


routes = [
    Route("/", index),
    Route("/api/health", api_health, methods=["GET", "HEAD", "POST"]),
    Route("/shop", shop, methods=["GET", "HEAD"]),
    Route("/payments/health", payments, methods=["GET", "HEAD"]),
    Route("/hooks/{name:str}", hook, methods=["POST"]),
]
public = Starlette(routes=routes)
internal = Starlette(
    routes=[*routes, Route("/control/{target}/{state:str}", control, methods=["POST"])]
)


def certificate_pair(
    issuer_key: ec.EllipticCurvePrivateKey,
    issuer: x509.Name,
    days: float,
    directory: Path,
    label: str,
) -> tuple[Path, Path]:
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(
            x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, settings.demo_target_host)])
        )
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(hours=1))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName(settings.demo_target_host), x509.DNSName("localhost")]
            ),
            critical=False,
        )
        .sign(issuer_key, hashes.SHA256())
    )
    cert_path, key_path = directory / f"{label}.pem", directory / f"{label}.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def issue_certificates(directory: Path) -> tuple[tuple[Path, Path], tuple[Path, Path]]:
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Pulsewatch Demo CA")])
    now = datetime.now(UTC)
    authority = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    if settings.extra_ca_file:
        target = Path(settings.extra_ca_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(authority.public_bytes(serialization.Encoding.PEM))
    service = certificate_pair(ca_key, ca_name, 365, directory, "service")
    expiring = certificate_pair(ca_key, ca_name, 12.2, directory, "expiring")
    return service, expiring


async def database(_: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    await asyncio.sleep(latency(SHAPES["database"], datetime.now(UTC), rng) / 1000)
    writer.close()
    with contextlib.suppress(ConnectionError):
        await writer.wait_closed()


def server(app: Starlette, port: int, pair: tuple[Path, Path] | None = None) -> uvicorn.Server:
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=port,
        log_level="warning",
        ssl_certfile=str(pair[0]) if pair else None,
        ssl_keyfile=str(pair[1]) if pair else None,
    )
    return uvicorn.Server(config)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    with tempfile.TemporaryDirectory() as folder:
        service, expiring = issue_certificates(Path(folder))
        tcp = await asyncio.start_server(database, "0.0.0.0", 6432)
        logger.info("demo_target_ready ports=8080,8443,9443,6432")
        async with tcp:
            await asyncio.gather(
                server(internal, 8080).serve(),
                server(public, 8443, service).serve(),
                server(public, 9443, expiring).serve(),
            )


if __name__ == "__main__":
    asyncio.run(main())
