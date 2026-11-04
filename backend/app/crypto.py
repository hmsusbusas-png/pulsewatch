import json

from cryptography.fernet import Fernet, MultiFernet

from app.config import settings


def cipher() -> MultiFernet:
    return MultiFernet(
        [Fernet(key.encode()) for key in [settings.encryption_key, *settings.fernet_previous_keys]]
    )


def encrypt_text(value: str) -> str:
    return cipher().encrypt(value.encode()).decode()


def decrypt_text(value: str) -> str:
    return cipher().decrypt(value.encode()).decode()


def encrypt_config(value: dict[str, str]) -> str:
    return encrypt_text(json.dumps(value, ensure_ascii=False))


def decrypt_config(value: str) -> dict[str, str]:
    data: dict[str, str] = json.loads(decrypt_text(value))
    return data
