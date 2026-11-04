import json
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from app.config import settings
from app.errors import ApiError

WEBHOOK_TYPES = ("webhook", "slack", "discord", "teams", "ntfy")
EVENT_TITLES = {
    "down": ("Недоступен", 0xEF4444, 5, "rotating_light"),
    "up": ("Восстановлен", 0x10B981, 3, "white_check_mark"),
    "degraded": ("Деградация отклика", 0xF59E0B, 4, "warning"),
    "recovered": ("Отклик в норме", 0x10B981, 3, "white_check_mark"),
    "expiring": ("Истекает срок", 0xF59E0B, 4, "hourglass"),
    "dns_changed": ("DNS-запись изменилась", 0x3B82F6, 4, "globe_with_meridians"),
    "escalation": ("Затянувшийся простой", 0xEF4444, 5, "sos"),
    "verification": ("Проверка канала", 0x3B82F6, 2, "link"),
    "test": ("Проверка уведомлений", 0x3B82F6, 2, "bell"),
}


@dataclass(frozen=True)
class OutboundRequest:
    url: str
    headers: dict[str, str]
    body: bytes


def validate_config(kind: str, config: dict[str, str]) -> dict[str, str]:
    url = config.get("url", "").strip()
    if kind != "webhook" and not url.startswith("https://") and not settings.allow_private_targets:
        raise ApiError(422, "invalid_channel_config", "Адрес должен начинаться с https://")
    if kind == "webhook":
        if set(config) != {"url", "secret"} or not 16 <= len(config["secret"]) <= 256:
            raise ApiError(
                422, "invalid_channel_config", "Нужны url и секрет длиной 16-256 символов"
            )
        return {"url": url, "secret": config["secret"]}
    allowed = {"url", "token"} if kind == "ntfy" else {"url"}
    if not config.keys() <= allowed or not url:
        raise ApiError(422, "invalid_channel_config", f"Допустимые поля: {', '.join(allowed)}")
    if kind == "ntfy" and len(urlsplit(url).path.strip("/").split("/")) != 1:
        raise ApiError(422, "invalid_channel_config", "Укажите адрес темы: https://ntfy.sh/тема")
    clean = {"url": url}
    if config.get("token"):
        clean["token"] = config["token"][:200]
    return clean


def _slack_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_request(kind: str, config: dict[str, str], payload: dict[str, object]) -> OutboundRequest:
    event = str(payload.get("event") or "test")
    title, color, priority, tag = EVENT_TITLES.get(event, EVENT_TITLES["test"])
    name = str(payload.get("name") or "Pulsewatch")
    text = str(payload.get("text", ""))
    heading = f"{name}: {title}" if payload.get("name") else title
    json_headers = {"Content-Type": "application/json"}
    if kind == "slack":
        document: dict[str, object] = {
            "text": f"{heading}\n{text}",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{_slack_escape(heading)}*\n{_slack_escape(text)}",
                    },
                }
            ],
        }
    elif kind == "discord":
        document = {
            "embeds": [{"title": heading[:256], "description": text[:4000], "color": color}],
            "allowed_mentions": {"parse": []},
        }
    elif kind == "teams":
        document = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "body": [
                            {
                                "type": "TextBlock",
                                "text": heading,
                                "weight": "Bolder",
                                "size": "Medium",
                                "wrap": True,
                            },
                            {"type": "TextBlock", "text": text, "wrap": True},
                        ],
                    },
                }
            ],
        }
    elif kind == "ntfy":
        parts = urlsplit(config["url"])
        topic = parts.path.strip("/")
        server = urlunsplit((parts.scheme, parts.netloc, "/", "", ""))
        document = {
            "topic": topic,
            "title": heading,
            "message": text,
            "priority": priority,
            "tags": [tag],
        }
        if config.get("token"):
            json_headers["Authorization"] = f"Bearer {config['token']}"
        return OutboundRequest(
            server, json_headers, json.dumps(document, ensure_ascii=False).encode()
        )
    else:
        raise ApiError(422, "invalid_channel_config", "Неизвестный тип канала")
    return OutboundRequest(
        config["url"], json_headers, json.dumps(document, ensure_ascii=False).encode()
    )
