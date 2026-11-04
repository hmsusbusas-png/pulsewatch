# Backend

Python 3.12, PostgreSQL 16. Публичные данные: выбранные сайты и заметки
статус-страниц. Остальные ресурсы требуют JWT или ограниченного API-ключа.

## Запуск

Из backend установить зависимости:

```sh
python -m pip install --require-hashes -r requirements.lock
python -m pip install --no-deps -e .
```

Скопировать корневой .env.example в .env. Пустые переменные игнорируются.
Для локального режима задать APP_ENV=dev, COOKIE_SECURE=false,
PAYMENT_PROVIDER=dev, адреса API_BASE_URL/FRONTEND_BASE_URL и явный JSON-список
CORS_ORIGINS. Для production нужны HTTPS, Secure cookie и YooKassa.

JWT_SECRET и пароль POSTGRES_PASSWORD должны быть разными случайными
значениями не короче 32 символов. Команда для одного значения:

```sh
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Для FERNET_KEY сгенерируйте случайный ключ шифрования:

```sh
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Секреты хранятся только в локальном .env или secret storage окружения.
Дефолтных секретов нет. Production отказывается стартовать со слабыми
JWT/Fernet/DB ключами, wildcard CORS, HTTP-адресами или dev-платежами.

Из корня:

```sh
docker compose up --build postgres backend worker
docker compose --profile telegram up -d bot
```

Порт БД наружу не публикуется. При запуске без Docker задать DATABASE_URL
с драйвером postgresql+asyncpg, выполнить alembic upgrade head и
python -m app.seed, затем отдельно запустить uvicorn app.main:app,
python -m app.worker и python -m app.bot. В dev seed создаёт demo-аккаунт
для DEMO_EMAIL (по умолчанию user@example.com), генерирует пароль и выводит
его только при первом создании. Для существующего аккаунта пароль не меняется.

### Демо-данные

```sh
docker compose --profile demo up -d --build --wait
docker compose exec backend python -m app.seed --demo [--password ...]
```

Команда работает только при APP_ENV=dev. Она пересоздаёт аккаунт
demo@example.com на тарифе Business и заполняет 90 дней истории: шесть
мониторов разных типов с суточной и недельной сезонностью отклика, три
инцидента (двухчасовой сбой с четырьмя заметками и постмортемом, трёхдневная
деградация, текущий сбой платёжного шлюза), свёртки, доставки, журнал
действий и статус-страницу с подписчиком. Пароль без --password генерируется
и печатается один раз. Профиль demo поднимает demo-target (HTTPS на 8443 и
9443 с собственным CA, TCP 6432, приёмник вебхуков) и агентов регионов
Франкфурт и Амстердам; их токены: AGENT_TOKEN_FRA и AGENT_TOKEN_AMS.
В интерфейсе dev-окружения та же операция доступна для текущего аккаунта
кнопкой «Показать демо-данные» (POST /api/dev/demo-data).

## Переменные окружения

Пустая переменная означает значение по умолчанию. Шаблон находится в корневом .env.example,
`python ../scripts/make_env.py` создаёт .env со случайными секретами для
локального запуска.

| Переменная | По умолчанию | Назначение |
| --- | --- | --- |
| APP_ENV | production | dev включает /docs, dev-оплату, демо-данные и письма в лог |
| DATABASE_URL |; | postgresql+asyncpg://…; в compose собирается из POSTGRES_* |
| POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB | pulsewatch, не задано, pulsewatch | учётные данные БД для compose |
| JWT_SECRET |; | подпись access JWT, не короче 32 символов |
| JWT_ISSUER, JWT_AUDIENCE | pulsewatch, pulsewatch-api | iss/aud токенов |
| FERNET_KEY |; | ключ шифрования данных в БД |
| FERNET_PREVIOUS_KEYS | [] | прежние ключи на время ротации, JSON-массив |
| COOKIE_SECURE | true | Secure для refresh-cookie |
| CORS_ORIGINS | ["http://localhost:5173"] | JSON-список разрешённых origin |
| FRONTEND_BASE_URL | http://localhost:5173 | адрес интерфейса для ссылок в письмах |
| API_BASE_URL | http://localhost:8000 | публичный адрес API (heartbeat, вебхуки оплаты) |
| PAYMENT_PROVIDER | yookassa | dev: тестовая оплата без внешнего провайдера |
| YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY |; | тестовый магазин ЮKassa |
| BOT_TOKEN, PUBLIC_URL, TELEGRAM_WEBHOOK_SECRET |; | Telegram-бот; PUBLIC_URL включает webhook |
| SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM, SMTP_STARTTLS | не задано, 587, не задано, не задано, не задано, true | почта; без SMTP_HOST в dev письма пишутся в лог |
| METRICS_TOKEN |; | доступ к /metrics |
| LOG_LEVEL | INFO | уровень логов |
| CHECK_CONCURRENCY | 20 | сколько проверок worker берёт за такт |
| SCHEDULER_TICK_SECONDS | 15 | такт планировщика |
| ALLOW_PRIVATE_TARGETS | false | разрешить частные адреса (только для локальной демо-цели) |
| DNS_RESOLVERS | [] | резолверы для DNS-мониторов; пусто: системные |
| WORKER_REGION, WORKER_REGION_NAME | msk, Москва | регион основного worker |
| REGION_AGENTS | {} | агенты регионов: {"fra": {"name": "…", "token": "…"}} |
| AGENT_TOKEN_FRA, AGENT_TOKEN_AMS |; | токены демо-агентов, compose подставляет их в REGION_AGENTS |
| AGENT_API_URL, AGENT_TOKEN | http://backend:8000,; | настройки процесса агента (python -m app.agent) |
| TRUSTED_PROXIES | [] | прокси, которым можно верить в X-Forwarded-For; в compose указан nginx |
| EXTRA_CA_FILE |; | дополнительный корневой сертификат (CA демо-цели) |
| STREAM_MAX_PER_USER | 5 | одновременных SSE-потоков на пользователя |
| API_RATE_LIMIT_PER_MINUTE | 600 | лимит на ключ или пользователя; запросы с API-ключами также ограничены по IP |
| CHECK_NOW_PER_USER_PER_MINUTE | 10 | «Проверить сейчас» на пользователя в минуту |
| CHECK_NOW_SITE_COOLDOWN_SECONDS | 20 | пауза между ручными проверками одного монитора |
| STATUS_CNAME_TARGET | status.pulsewatch.dev | куда должен указывать CNAME своего домена статус-страницы |
| DEMO_EMAIL | user@example.com | аккаунт, который создаёт `python -m app.seed` в dev |
| DEMO_TARGET_HOST, DEMO_SLACK_URL | demo-target, https://demo-target:8443/hooks/slack | адреса демо-цели для `seed --demo` |
| TEST_DATABASE_URL |; | отдельная БД для pytest, имя должно оканчиваться на _test |
| BACKEND_PORT, FRONTEND_PORT | 8000, 8080 | опубликованные порты compose |

## Настройки и данные

BOT_TOKEN включает Telegram; PUBLIC_URL
переключает polling на webhook с TELEGRAM_WEBHOOK_SECRET. Telegram работает
в plain-text режиме без интерпретации HTML/Markdown. SMTP_HOST/PORT/FROM и
SMTP_USERNAME/PASSWORD задают почту; обязателен проверяемый STARTTLS.
METRICS_TOKEN защищает /metrics. /readyz проверяет БД и heartbeat worker.
Production отключает /docs, /redoc, /openapi.json и dev payment routes.

Пароли: bcrypt cost 12, 8-72 байта UTF-8. Access JWT: до 15 минут,
фиксированный HS256, iss/aud/iat/exp/jti. Refresh-cookie: HttpOnly,
SameSite=Strict, Secure в production. Смена пароля/email, reset, выход со всех
устройств и повторное использование refresh аннулируют access и refresh.

До первого входа требуется подтверждение email через auth/email/confirm.
Пароль задаётся вместе с токеном подтверждения; регистрация сама по себе не
даёт доступ. Это закрывает перечисление email через регистрацию и последующий
пробный login. SMTP должен быть настроен до публичной регистрации.

Тарифы загружаются из plans; usage считается запросами. Worker использует
SKIP LOCKED, аренду и fencing результата. Инцидент и очередь сохраняются
атомарно. Доставка at-least-once, пять попыток с паузой. Rate limit хранится
в PostgreSQL: атомарный UPSERT счётчика окна, ключ хешируется, старые окна
удаляются. Он общий для API/worker/bot, без зависимости от памяти процесса.

Проверки и webhook-каналы соединяются с проверенным IP. Все редиректы
проверяются; служебные порты запрещены. Ответ ограничен 1 МиБ, запрос:
1 МиБ и 16 уровнями JSON. HTTP просит identity encoding; сжатый ответ может
не подойти для keyword. Заголовки и config каналов выдаются замаскированными.
POST body хранится как данные проверки: секреты передавать через заголовки.

Письма очереди, заголовки и настройки каналов шифруются Fernet. Для ротации:
задать новый FERNET_KEY, прежние ключи в FERNET_PREVIOUS_KEYS как JSON-массив,
сделать резервную копию БД, выполнить python -m app.rotate_keys. После
успешного переноса и проверки удалить прежние ключи из окружения.
Подход соответствует [MultiFernet](https://cryptography.io/en/latest/fernet/#cryptography.fernet.MultiFernet).
Копии БД и ключи хранить отдельно от репозитория.

Checkout покупает период, автоматического списания нет. YooKassa работает
с тестовым магазином и перепроверяет платежи/возвраты через API. Полный
возврат завершающего период платежа переводит аккаунт на Free; частичный
возврат отклоняется и требует отдельной учётной обработки. Конечные статусы
не переактивируются повторным webhook. Raw payload содержит только
идентификатор, состояние, сумму и валюту.

## Проверки

Создать отдельную БД с именем, оканчивающимся на _test, передать её адрес
через TEST_DATABASE_URL. Фикстуры выполняют миграции и TRUNCATE только в ней.
В CI используется отдельный одноразовый PostgreSQL с trust; это не настройка
обычного compose. Из backend:

```sh
ruff check app scripts tests alembic ../scripts
mypy app scripts ../scripts
alembic upgrade head && alembic downgrade base && alembic upgrade head && alembic check
pytest --cov=app --cov-report=json --cov-report=term-missing
python -m scripts.check_coverage
pip-audit
APP_ENV=dev python -m scripts.export_openapi
```

Миграции проверяются на той же _test БД (DATABASE_URL=TEST_DATABASE_URL).
После изменения API нужно выгрузить openapi.json и выполнить во frontend
`npm run gen:api`: CI сверяет оба файла и типы.

uv.lock фиксирует зависимости, requirements.lock и requirements-runtime.lock
содержат версии и хеши. CI запускает проверки и Gitleaks. Для локального
хука из корня выполнить pre-commit install. Дополнительная проверка:
python scripts/check_secrets.py. Она проверяет также состав файлов публикации.

Smoke: python -m scripts.smoke_saas с APP_ENV=dev, PAYMENT_PROVIDER=dev,
ALLOW_PRIVATE_TARGETS=true, COOKIE_SECURE=false и отдельной _test БД.
Сценарий использует реальный HTTP-сервер и подменяет Telegram API.
