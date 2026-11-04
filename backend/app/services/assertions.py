import json
import re
from dataclasses import dataclass
from typing import Any

from app.errors import ApiError

SOURCES = ("json", "header", "body")
OPERATORS = ("equals", "not_equals", "contains", "regex", "exists", "lt", "gt")
MAX_ASSERTIONS = 10
MAX_PATTERN = 200
REGEX_SUBJECT_LIMIT = 10000
_MISSING = object()
_TOKEN = re.compile(
    r"""\.(?P<name>[A-Za-z_$][\w$-]*)|\[(?P<index>-?\d+)\]|\[(?P<quote>['"])(?P<key>(?:(?!(?P=quote)).)*)(?P=quote)\]|\[(?P<star>\*)\]|\.(?P<dotstar>\*)"""
)
_NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*[*+}]\)\s*[*+{]")
_BACKREFERENCE = re.compile(r"\\[1-9]|\(\?P=")


@dataclass(frozen=True)
class AssertionOutcome:
    index: int
    passed: bool
    actual: str | None
    message: str


def parse_path(path: str) -> list[str | int | None]:
    if not path.startswith("$"):
        raise ValueError("JSONPath должен начинаться с $")
    steps: list[str | int | None] = []
    position = 1
    while position < len(path):
        match = _TOKEN.match(path, position)
        if match is None:
            raise ValueError(
                f"Не удалось разобрать JSONPath около «{path[position : position + 12]}»"
            )
        if match.group("name") is not None:
            steps.append(match.group("name"))
        elif match.group("index") is not None:
            steps.append(int(match.group("index")))
        elif match.group("key") is not None:
            steps.append(match.group("key"))
        else:
            steps.append(None)
        position = match.end()
    return steps


def select(document: object, steps: list[str | int | None]) -> list[object]:
    current: list[object] = [document]
    for step in steps:
        following: list[object] = []
        for node in current:
            if step is None:
                if isinstance(node, list):
                    following.extend(node)
                elif isinstance(node, dict):
                    following.extend(node.values())
            elif isinstance(step, int):
                if isinstance(node, list) and -len(node) <= step < len(node):
                    following.append(node[step])
            elif isinstance(node, dict) and step in node:
                following.append(node[step])
        current = following
    return current


def ensure_safe_pattern(pattern: str) -> re.Pattern[str]:
    if len(pattern) > MAX_PATTERN:
        raise ValueError(f"Регулярное выражение длиннее {MAX_PATTERN} символов")
    if _NESTED_QUANTIFIER.search(pattern) or _BACKREFERENCE.search(pattern):
        raise ValueError("Вложенные квантификаторы и обратные ссылки запрещены")
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"Некорректное регулярное выражение: {exc}") from exc


def validate_assertions(items: list[dict[str, str]]) -> list[dict[str, str]]:
    if len(items) > MAX_ASSERTIONS:
        raise ApiError(422, "invalid_assertion", f"Не более {MAX_ASSERTIONS} условий")
    clean = []
    for position, item in enumerate(items):
        source = item.get("source", "json")
        operator = item.get("op", "equals")
        path = str(item.get("path", "")).strip()
        value = str(item.get("value", ""))
        try:
            if source not in SOURCES:
                raise ValueError("Источник: json, header или body")
            if operator not in OPERATORS:
                raise ValueError("Неизвестная операция")
            if source == "json":
                parse_path(path)
            elif source == "header" and not re.fullmatch(
                r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,100}", path
            ):
                raise ValueError("Укажите имя заголовка")
            if operator == "regex":
                ensure_safe_pattern(value)
            if operator in ("lt", "gt"):
                float(value)
            if len(value) > 500:
                raise ValueError("Значение длиннее 500 символов")
        except ValueError as exc:
            raise ApiError(
                422,
                "invalid_assertion",
                f"Условие {position + 1}: {exc}",
                [{"field": f"assertions.{position}", "message": str(exc), "type": "assertion"}],
            ) from exc
        clean.append({"source": source, "path": path, "op": operator, "value": value})
    return clean


def _render(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _compare(operator: str, actual: object, expected: str) -> bool:
    if operator == "exists":
        return actual is not _MISSING
    if actual is _MISSING:
        return operator == "not_equals"
    text = _render(actual)
    if operator == "equals":
        return text == expected
    if operator == "not_equals":
        return text != expected
    if operator == "contains":
        return expected in text
    if operator == "regex":
        return ensure_safe_pattern(expected).search(text[:REGEX_SUBJECT_LIMIT]) is not None
    try:
        number = float(text)
    except ValueError:
        return False
    return number < float(expected) if operator == "lt" else number > float(expected)


def evaluate_assertions(
    items: list[dict[str, str]], headers: dict[str, str], body: str
) -> list[AssertionOutcome]:
    outcomes = []
    document: Any = _MISSING
    lowered = {name.lower(): value for name, value in headers.items()}
    for position, item in enumerate(items):
        source, operator, path, expected = item["source"], item["op"], item["path"], item["value"]
        actual: object = _MISSING
        if source == "header":
            actual = lowered.get(path.lower(), _MISSING)
        elif source == "body":
            actual = body
        else:
            if document is _MISSING:
                try:
                    document = json.loads(body)
                except ValueError:
                    document = None
            if document is not None:
                found = select(document, parse_path(path))
                if found:
                    actual = found[0] if len(found) == 1 else found
        try:
            passed = _compare(operator, actual, expected)
        except ValueError:
            passed = False
        shown = None if actual is _MISSING else _render(actual)[:300]
        label = path or source
        message = (
            f"{label} {operator} {expected}"
            if passed
            else f"{label}: ожидалось {operator} «{expected}», получено «{shown}»"
            if shown is not None
            else f"{label}: значение не найдено"
        )
        outcomes.append(AssertionOutcome(position, passed, shown, message))
    return outcomes
