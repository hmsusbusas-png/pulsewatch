# Изменения

## 2026-11-04: версия контракта 2026-11-04

### Ломающие изменения
- Ошибки везде в формате `{code, message, details, request_id}`. Ответ 500 больше не
  `{request_id}`: code=`internal_error`, details=null, детали по-прежнему не раскрываются.
- Ссылки в письмах ведут на фронтенд (FRONTEND_BASE_URL): `/public/subscribers/confirm`,
  `/public/subscribers/unsubscribe`, `/channels/unsubscribe`, `/channels/verify`.
  Соответствующие API стали POST с телом `{token}`; прежние GET удалены, чтобы
  предзагрузка ссылок почтовыми сканерами не меняла состояние.
- `/public/pages/{slug}` возвращает типизированную модель: `overall`, `announcements`,
  у мониторов `days` (90 суток), `uptime_90d`, `degraded`, `maintenance`; у инцидентов
  `site_name`, `kind`, `duration_seconds`. URL показываются по флагу страницы `show_urls`.
- `POST /incidents/{id}/notes` перенесён в раздел incidents, путь не изменился.
- Открытый инцидент уникален в паре (монитор, вид): `down` и `degraded` независимы.
  `started_at`: время первой неудачной проверки, `confirmed_at`: подтверждение порогом.
- Email-канал может вести на любой адрес; чужой адрес работает только после перехода по
  ссылке из письма. Смена адреса канала не применяется до подтверждения.

### Новое
- Аналитика: `/sites/{id}/stats?range=24h|7d|30d|90d` (перцентили, этапы запроса, зоны
  инцидентов и обслуживания), `/sites/{id}/slo`, `/sites/daily`, `/sites/{id}/daily`,
  `/reports/sla`. `/sites/{id}/checks` принимает 30d и 90d, `order`, `failed`, отдаёт этапы.
- Живые события: `POST /stream/token`, `GET /stream` (SSE через LISTEN/NOTIFY).
- `POST /sites/{id}/check-now` с лимитами на пользователя и монитор.
- Типы мониторов `heartbeat` (`/heartbeat/{token}`, GET и POST, `status=fail`, `duration`)
  и `dns`; TCP с отправкой строки и ожидаемым ответом; условия по JSONPath, заголовкам и
  телу; `/sites/{id}/assertions/preview`; срок сертификата с деталями TLS и срок домена по
  RDAP с порогами `expiry_thresholds`.
- Регионы: `/regions`, API агентов `/agent/claim` и `/agent/results`, поле `regions` у
  монитора, `region_states` в ответе; сбой подтверждается большинством регионов.
- Диагностика сбоя в `/incidents/{id}`: лента событий, код, заголовки, 2 КБ тела без
  секретов, IP, DNS, этапы. `/incidents`, `/incidents/{id}/acknowledge`, `/postmortem`.
- Деградация отклика по медиане и MAD за 7 дней, инциденты `kind=degraded`.
- Каналы Slack, Discord, Microsoft Teams, ntfy; `POST /channels/{id}/verify`;
  `/escalations`; `/templates` с предпросмотром (Business).
- `/sites/bulk`, `/sites/import`, `/sites/export`; статус-страницы с логотипом, акцентом,
  объявлениями и своим доменом; `/badge/{slug}.svg`; `/public/pages/{slug}/feed.xml`.
- `POST /billing/downgrade`, `scheduled_plan` в подписке; `PATCH /me` (часовой пояс).
- `/account/sessions`: браузер, ОС, маска IP, текущая сессия. `/account/audit`: cursor,
  `changes[]` с прежним и новым значением.
- Заголовки: `Idempotency-Key`, `X-RateLimit-*`, `Retry-After`, `Link`, `X-Next-Cursor`,
  `X-API-Version`. В dev без SMTP письма пишутся в лог целиком.
- `python -m app.seed --demo [--password]` создаёт 90 дней демо-истории (только APP_ENV=dev).

## 2026-11-03: для фронтенда

- POST /api/auth/register возвращает 202 и {"accepted":true} как для нового,
  так и для существующего email. Токен и cookie больше не выдаются: после
  регистрации требуется подтверждение email и отдельный login.
- Новый POST /api/auth/email/confirm принимает {token,password}. Пароль выбирается
  при подтверждении владения email; до подтверждения login возвращает одинаковую
  ошибку для отсутствующего и неподтверждённого аккаунта. Триал начинается после
  подтверждения. Страница ссылки: account/signup. Старые аккаунты после 0006
  также требуют подтверждения; повторный register отправляет новую ссылку.
- Старые access JWT без iss/aud/jti/ver недействительны. Смена пароля/email,
  reset и выход со всех устройств немедленно отзывают access; нужен новый login.
- Refresh cookie использует SameSite=Strict; недопустимый Origin отклоняется.
- Лишние поля входных схем отклоняются с 422. Заголовки мониторинга и все
  значения config каналов возвращаются как ********, включая email и URL.
- Публичные названия, описания и заметки HTML-экранируются. Для них использовать
  обычный вывод текста; не вставлять произвольную разметку.
- Тело запроса ограничено 1 МиБ, глубина JSON: 16. Ошибки 413, 408 и 429
  содержат request_id. Ответ 500 содержит только request_id; детали не выдаются.
- Подписанные ссылки reset/email живут 30 минут, остальные почтовые ссылки:
  один час; старые ссылки миграция инвалидирует. Поля token принимают до 2048
  символов. Для писем нужны страницы обработки ссылок account/reset и account/email.
- Production возвращает 404 для /docs, /redoc, /openapi.json и dev-платежей.
  Контракт можно экспортировать командой, отключение HTTP docs этому не мешает.
- Списки channels/keys/status-pages/account/sessions принимают cursor и limit
  (по умолчанию 100, максимум 100). Checks принимает cursor и limit до 1000;
  записи получили id. Следующая страница: cursor последнего id.
- GET CSV с чужим site_id и DELETE чужой refresh-сессии возвращают 404.
- Порты 22, 25, 2375, 2376, 5432, 6379, 11211 и 27017 запрещены для целей.
- Повторный успешный webhook после canceled возвращает payment_state_conflict
  (409). Полный refund проверяется через API провайдера; raw payload не раскрывается.

## 2026-11-03: серверная часть

Добавлены планы Free/Pro/Business в БД, Pro trial на 14 дней, подписки, платежи,
Dev/YooKassa checkout, server-side лимиты и приостановка лишних сайтов.
Новые группы API: plans, billing, channels, telegram, status-pages, public/pages,
incidents/notes, keys, export, account, admin, healthz/readyz/metrics.
Все группы сохраняют префикс /api; health также доступен без префикса.

Site поддерживает HTTP/keyword/TCP/SSL, метод, зашифрованные headers,
expected_codes, таймаут, редиректы, подтверждение сбоя, cooldown, расписание
обслуживания, каналы, теги, группу и паузу. Список остаётся массивом; cursor:
последний id, limit 1-100. Уточнённый тариф и usage: billing/subscription;
старое me.tariff остаётся совместимым free/pro, Business там обозначается pro.

Добавлены очередь доставок, суточные свёртки, MultiFernet и команда ротации,
аудит действий, подписки на статус, еженедельный отчёт, управление сессиями,
API-ключи read/write, изоляция владельцев и общие rate limits в PostgreSQL.
Миграция 0005 добавляет auth_version/currency, очищает старые тела писем и
нормализует payload платежей. Секреты из истории автоматически не удаляются.
