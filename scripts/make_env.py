import base64
import secrets
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parents[1] / ".env"


def token(size: int) -> str:
    return secrets.token_urlsafe(size)


def fernet_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def local_settings() -> list[str]:
    return [
        "APP_ENV=dev",
        "COOKIE_SECURE=false",
        "PAYMENT_PROVIDER=dev",
        "POSTGRES_USER=pulsewatch",
        "POSTGRES_DB=pulsewatch",
        f"POSTGRES_PASSWORD={token(36)}",
        f"JWT_SECRET={token(48)}",
        f"FERNET_KEY={fernet_key()}",
        "API_BASE_URL=http://localhost:8000",
        "FRONTEND_BASE_URL=http://localhost:8080",
        'CORS_ORIGINS=["http://localhost:8080","http://127.0.0.1:5173","http://localhost:5173"]',
        "ALLOW_PRIVATE_TARGETS=true",
        "DEMO_EMAIL=demo@example.com",
        f"AGENT_TOKEN_FRA={token(36)}",
        f"AGENT_TOKEN_AMS={token(36)}",
        "EXTRA_CA_FILE=/shared/demo-ca.pem",
    ]


def main() -> None:
    if TARGET.exists():
        sys.exit(".env already exists, left untouched")
    TARGET.write_text("\n".join(local_settings()) + "\n", encoding="utf-8")
    print(".env created with random local secrets")


if __name__ == "__main__":
    main()
