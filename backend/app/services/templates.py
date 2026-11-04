import re

from app.errors import ApiError

PLACEHOLDERS = ("name", "url", "status", "code", "duration", "error")
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
DEFAULT_TEMPLATES = {
    "down": "🔴 {name} недоступен\n{error}\nКод ответа: {code}\n{url}",
    "up": "🟢 {name} снова работает\nПростой: {duration}\n{url}",
    "degraded": "🟠 {name}: отклик выше нормы\n{error}\n{url}",
    "expiring": "⏳ {name}: {error}\n{url}",
}
SAMPLE_CONTEXT = {
    "name": "Платёжный шлюз",
    "url": "https://pay.example.com/health",
    "status": "недоступен",
    "code": "503",
    "duration": "4 мин 20 с",
    "error": "HTTP 503",
}


def validate_template(body: str) -> str:
    unknown = sorted({name for name in _PLACEHOLDER.findall(body) if name not in PLACEHOLDERS})
    if unknown:
        raise ApiError(
            422,
            "invalid_template",
            "Неизвестные подстановки: " + ", ".join(f"{{{name}}}" for name in unknown),
        )
    if not body.strip():
        raise ApiError(422, "invalid_template", "Шаблон не может быть пустым")
    return body


def render_template(body: str, context: dict[str, str]) -> str:
    return _PLACEHOLDER.sub(
        lambda match: context.get(match[1], "н/д") if match[1] in PLACEHOLDERS else match[0], body
    )


def human_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} с"
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} мин {rest} с" if rest else f"{minutes} мин"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours} ч {minutes} мин" if minutes else f"{hours} ч"
    days, hours = divmod(hours, 24)
    return f"{days} д {hours} ч" if hours else f"{days} д"
