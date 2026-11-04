import logging
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("pulsewatch.errors")

DEFAULT_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
}

ERROR_CATALOG: dict[str, str] = {
    "bad_request": "Запрос не может быть обработан",
    "validation_error": "Тело или параметры запроса не прошли проверку; детали в details",
    "unauthorized": "Нет действующего access-токена или API-ключа",
    "invalid_credentials": "Неверный email или пароль",
    "invalid_api_key": "API-ключ отозван или не существует",
    "api_key_scope": "Область действия API-ключа не разрешает операцию",
    "refresh_missing": "Нет refresh-cookie",
    "refresh_invalid": "Refresh-токен не распознан",
    "refresh_reused": "Повторное использование refresh-токена, все сессии отозваны",
    "login_locked": "Слишком много попыток входа",
    "invalid_token": "Ссылка или одноразовый токен недействительны",
    "invalid_password": "Неверный пароль",
    "email_taken": "Email уже используется",
    "admin_required": "Нужны права администратора",
    "forbidden": "Доступ запрещён",
    "not_found": "Ресурс не найден",
    "site_not_found": "Монитор не найден или принадлежит другому аккаунту",
    "incident_not_found": "Инцидент не найден",
    "channel_not_found": "Канал не найден",
    "page_not_found": "Статус-страница не найдена",
    "rule_not_found": "Правило эскалации не найдено",
    "conflict": "Конфликт состояния ресурса",
    "resource_conflict": "Ресурс уже существует или изменён параллельным запросом",
    "slug_taken": "Адрес статус-страницы занят",
    "domain_taken": "Домен уже привязан к другой странице",
    "rate_limited": "Превышен лимит запросов; см. Retry-After",
    "check_rate_limited": "Ручная проверка уже запрашивалась недавно",
    "stream_limit": "Превышено число одновременных потоков событий",
    "idempotency_key_reused": "Ключ идемпотентности использован с другим телом запроса",
    "idempotency_in_progress": "Запрос с этим ключом идемпотентности ещё выполняется",
    "invalid_url": "Некорректный URL цели",
    "forbidden_target": "Цель указывает на внутренний или служебный адрес",
    "forbidden_port": "Порт цели запрещён политикой",
    "unresolvable_host": "Имя хоста не разрешается",
    "invalid_hostname": "Имя хоста не является публичным доменом",
    "plan_limit_sites": "Достигнут лимит мониторов тарифа",
    "plan_limit_channels": "Достигнут лимит каналов тарифа",
    "plan_limit_interval": "Интервал меньше минимального для тарифа",
    "plan_limit_regions": "Регионов больше, чем разрешает тариф",
    "plan_feature_*": "Возможность недоступна на текущем тарифе; суффикс указывает имя возможности",
    "invalid_assertion": "Условие проверки ответа некорректно",
    "invalid_template": "Шаблон уведомления некорректен",
    "invalid_maintenance": "Окно обслуживания некорректно",
    "invalid_import": "Файл импорта не удалось разобрать",
    "invalid_period": "Период отчёта некорректен",
    "region_unknown": "Регион не зарегистрирован",
    "agent_unauthorized": "Токен регионального агента недействителен",
    "channel_disabled": "Канал отключён",
    "subscription_expired": "Подписка истекла",
    "payment_amount_mismatch": "Сумма или валюта платежа не совпадает",
    "invalid_signature": "Подпись запроса недействительна",
    "origin_forbidden": "Источник запроса запрещён",
    "request_too_large": "Тело запроса превышает лимит",
    "request_timeout": "Тело запроса не получено вовремя",
    "upstream_unavailable": "Внешний сервис временно недоступен",
    "internal_error": "Внутренняя ошибка; передайте request_id в поддержку",
}


Responses = dict[int | str, dict[str, Any]]


class ErrorDetail(BaseModel):
    field: str | None = None
    message: str
    type: str | None = None


class ErrorResponse(BaseModel):
    code: str = Field(description="Машинный код ошибки; перечень есть в описании API")
    message: str = Field(description="Сообщение для человека")
    details: list[ErrorDetail] | None = None
    request_id: str


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers or {}


def request_id_of(request: Request) -> str:
    return str(getattr(request.state, "request_id", None) or "unavailable")


def error_body(
    code: str, message: str, request_id: str, details: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {"code": code, "message": message, "details": details, "request_id": request_id}


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error_body(code, message, request_id_of(request), details),
        headers=headers,
    )


def _validation_details(exc: RequestValidationError) -> list[dict[str, Any]]:
    details = []
    for error in list(exc.errors())[:20]:
        location = ".".join(str(part) for part in error.get("loc", ()) if part != "body")
        details.append(
            {
                "field": location or None,
                "message": str(error.get("msg", "invalid value")),
                "type": str(error.get("type", "")) or None,
            }
        )
    return details


def _describe_validation_error(details: list[dict[str, Any]]) -> str:
    if not details:
        return "Request body is invalid"
    first = details[0]
    return f"{first['field']}: {first['message']}" if first["field"] else str(first["message"])


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(IntegrityError)
    async def resource_conflict(request: Request, exc: IntegrityError) -> JSONResponse:
        return error_response(
            request, 409, "resource_conflict", "Ресурс уже существует или изменён другим запросом"
        )

    @app.exception_handler(httpx.HTTPError)
    async def upstream_error(request: Request, exc: httpx.HTTPError) -> JSONResponse:
        logger.warning("upstream_unavailable", extra={"extra_fields": {"kind": type(exc).__name__}})
        return error_response(
            request, 502, "upstream_unavailable", "Внешний сервис временно недоступен"
        )

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(
            request, exc.status_code, exc.code, exc.message, exc.details, exc.headers or None
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict):
            code = str(detail.get("code", DEFAULT_CODES.get(exc.status_code, "error")))
            message = str(detail.get("message", code))
            return error_response(request, exc.status_code, code, message)
        code = DEFAULT_CODES.get(exc.status_code, "error")
        message = str(detail) if detail else code
        return error_response(request, exc.status_code, code, message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = _validation_details(exc)
        return error_response(
            request, 422, "validation_error", _describe_validation_error(details), details
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unhandled_error",
            extra={"extra_fields": {"path": request.url.path, "method": request.method}},
        )
        from app.http_guard import headers

        request_id = request_id_of(request)
        return JSONResponse(
            status_code=500,
            content=error_body("internal_error", ERROR_CATALOG["internal_error"], request_id),
            headers={**headers(), "X-Request-ID": request_id},
        )
