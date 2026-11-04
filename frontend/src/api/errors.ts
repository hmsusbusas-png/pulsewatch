export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId?: string;
  readonly retryAt?: number;

  constructor(status: number, code: string, message: string, requestId?: string, retryAt?: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.retryAt = retryAt;
  }
}

type ErrorBody = {
  code?: unknown;
  message?: unknown;
  detail?: unknown;
  request_id?: unknown;
};

const FALLBACK_BY_STATUS: Record<number, string> = {
  400: "Запрос отклонён сервером.",
  401: "Требуется авторизация.",
  403: "Недостаточно прав для этого действия.",
  404: "Запрошенные данные не найдены.",
  409: "Действие конфликтует с текущим состоянием.",
  422: "Проверьте заполнение полей.",
  429: "Слишком много запросов. Попробуйте позже.",
  500: "Ошибка на сервере. Попробуйте ещё раз.",
  502: "Сервер недоступен. Попробуйте ещё раз.",
  503: "Сервис временно недоступен.",
};

const HUMAN_BY_CODE: Record<string, string> = {
  invalid_credentials: "Неверный email или пароль.",
  email_taken: "Если адрес доступен для регистрации, мы отправим письмо.",
  rate_limited: "Слишком много попыток. Подождите минуту и повторите.",
  unauthorized: "Сессия истекла. Войдите снова.",
  refresh_missing: "Сессия истекла. Войдите снова.",
  refresh_invalid: "Сессия истекла. Войдите снова.",
  refresh_reused: "Сессия была отозвана. Войдите снова.",
  site_not_found: "Сайт не найден.",
  status_page_not_found: "Публичная страница статуса не найдена.",
  tariff_limit_reached: "Лимит тарифа исчерпан. Посмотрите доступные тарифы.",
  invalid_url: "URL должен начинаться с http:// или https:// и содержать домен.",
  unresolvable_host: "Домен не удалось разрешить. Проверьте адрес сайта.",
  forbidden_target: "Этот адрес недоступен для мониторинга из соображений безопасности.",
  validation_error: "Проверьте заполнение полей.",
  internal_error: "Ошибка на сервере. Попробуйте ещё раз.",
};

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const suffix = error.requestId ? ` Код запроса: ${error.requestId}` : "";
    if (error.code.startsWith("plan_limit_"))
      return "Лимит тарифа исчерпан. Посмотрите тарифы." + suffix;
    if (/token|expired|signature/.test(error.code) && error.status !== 401)
      return "Ссылка недействительна или срок её действия истёк. Запросите новое письмо." + suffix;
    if (error.status === 429)
      return (
        `Слишком много запросов. Повторите через ${Math.max(0, Math.ceil(((error.retryAt ?? Date.now()) - Date.now()) / 1000))} секунд.` +
        suffix
      );
    if ([408, 413, 500].includes(error.status))
      return (
        ({
          408: "Сервер не успел обработать запрос.",
          413: "Слишком большой запрос.",
          500: "Ошибка на сервере. Попробуйте ещё раз.",
        }[error.status] ?? "Ошибка запроса.") + suffix
      );
    const human = HUMAN_BY_CODE[error.code];
    if (human) {
      return human + suffix;
    }
    if (error.message) {
      return error.message + suffix;
    }
    return FALLBACK_BY_STATUS[error.status] ?? "Не удалось выполнить запрос.";
  }
  if (error instanceof TypeError) {
    return "Нет связи с сервером. Проверьте подключение и повторите.";
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return "Неизвестная ошибка. Попробуйте ещё раз.";
}

export async function parseErrorResponse(response: Response): Promise<ApiError> {
  let code = `http_${response.status}`;
  let message = FALLBACK_BY_STATUS[response.status] ?? `Ошибка ${response.status}.`;
  let requestId = response.headers.get("X-Request-ID") ?? undefined;
  const retry = response.headers.get("Retry-After");
  const retryAt =
    response.status === 429
      ? retry && !Number.isNaN(Number(retry))
        ? Date.now() + Number(retry) * 1000
        : retry && Number.isFinite(Date.parse(retry))
          ? Date.parse(retry)
          : Date.now() + 60_000
      : undefined;

  const raw = await response.text();
  if (raw) {
    try {
      const body = JSON.parse(raw) as ErrorBody;
      if (typeof body.request_id === "string") requestId = body.request_id;
      if (typeof body.code === "string" && body.code) {
        code = body.code;
      }
      if (typeof body.message === "string" && body.message) {
        message = body.message;
      } else if (typeof body.detail === "string" && body.detail) {
        message = body.detail;
      }
    } catch {
      message = FALLBACK_BY_STATUS[response.status] ?? message;
    }
  }

  return new ApiError(response.status, code, message, requestId, retryAt);
}
