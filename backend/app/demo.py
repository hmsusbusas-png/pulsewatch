import asyncio
import json
import random
import secrets
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.crypto import encrypt_config, encrypt_text
from app.database import engine, session_factory
from app.demo_model import LOCAL_ZONE, SHAPES, latency, phases
from app.models import (
    LATENCY_BUCKETS,
    ApiKey,
    AuditLog,
    CheckDiagnostic,
    EscalationRule,
    Incident,
    IncidentEscalation,
    IncidentNote,
    NotificationChannel,
    NotificationDelivery,
    NotificationTemplate,
    Payment,
    Plan,
    Site,
    SiteChannel,
    SiteRegionState,
    SiteStatus,
    StatusAnnouncement,
    StatusPage,
    StatusPageSite,
    StatusSubscriber,
    Subscription,
    User,
    UserTariff,
)
from app.security import hash_password, hash_refresh_token
from app.services.monitors import in_maintenance
from app.services.redaction import redact_body

DEMO_EMAIL = "demo@example.com"
TEAM_PATTERN = "demo-team-%@example.com"
HISTORY_DAYS = 90
REGION_DAYS = 7
CHECK_COLUMNS = (
    "site_id",
    "checked_at",
    "status_code",
    "response_ms",
    "ok",
    "error",
    "dns_ms",
    "connect_ms",
    "tls_ms",
    "ttfb_ms",
    "transfer_ms",
    "region",
)
BUCKET_LIST = ",".join(str(value) for value in LATENCY_BUCKETS)
ROLLUP_SQL = text(
    f"""
    INSERT INTO daily_uptime (site_id, day, total, successful, response_ms_sum, histogram)
    SELECT k.site_id, k.day, k.total, k.successful, k.latency,
           coalesce(h.histogram, array_fill(0, ARRAY[{len(LATENCY_BUCKETS)}]))
    FROM (
        SELECT site_id, (checked_at AT TIME ZONE 'UTC')::date AS day, count(*) AS total,
               count(*) FILTER (WHERE ok) AS successful,
               coalesce(sum(response_ms) FILTER (WHERE ok), 0) AS latency
        FROM checks WHERE site_id = ANY(:ids) GROUP BY 1, 2
    ) k
    LEFT JOIN (
        SELECT d.site_id, d.day, array_agg(coalesce(b.n, 0) ORDER BY g.i)::int[] AS histogram
        FROM (
            SELECT DISTINCT site_id, (checked_at AT TIME ZONE 'UTC')::date AS day
            FROM checks WHERE site_id = ANY(:ids)
        ) d
        CROSS JOIN generate_series(1, {len(LATENCY_BUCKETS)}) AS g(i)
        LEFT JOIN (
            SELECT site_id, (checked_at AT TIME ZONE 'UTC')::date AS day,
                   width_bucket(response_ms, ARRAY[{BUCKET_LIST}]::int[]) AS bucket, count(*) AS n
            FROM checks WHERE site_id = ANY(:ids) AND ok AND response_ms IS NOT NULL
            GROUP BY 1, 2, 3
        ) b ON b.site_id = d.site_id AND b.day = d.day AND b.bucket = g.i
        GROUP BY d.site_id, d.day
    ) h ON h.site_id = k.site_id AND h.day = k.day
    ON CONFLICT (site_id, day) DO UPDATE SET total = EXCLUDED.total,
        successful = EXCLUDED.successful, response_ms_sum = EXCLUDED.response_ms_sum,
        histogram = EXCLUDED.histogram
    """
)


@dataclass
class Outage:
    start: datetime
    end: datetime
    error: str
    status_code: int | None


@dataclass
class Monitor:
    key: str
    site: Site
    interval: int
    shape_key: str | None
    outages: list[Outage] = field(default_factory=list)
    blips: list[datetime] = field(default_factory=list)
    slow: tuple[datetime, datetime] | None = None
    regions: tuple[str, ...] = ()


@dataclass
class Generated:
    rows: list[tuple[object, ...]]
    failures: list[tuple[datetime, str, int | None]]
    last: tuple[datetime, bool, int | None] | None


def local(day_offset: int, hour: int, minute: int, now: datetime) -> datetime:
    day = (now.astimezone(LOCAL_ZONE) - timedelta(days=day_offset)).date()
    return datetime.combine(day, time(hour, minute), LOCAL_ZONE).astimezone(UTC)


def moments(
    start: datetime, end: datetime, interval: int, rng: random.Random
) -> Iterator[datetime]:
    current = start + timedelta(seconds=rng.randint(0, interval))
    while current < end:
        yield current
        current += timedelta(seconds=interval + rng.randint(-2, 2))


def generate(
    monitor: Monitor, start: datetime, now: datetime, region: str, rng: random.Random
) -> Generated:
    site = monitor.site
    rows: list[tuple[object, ...]] = []
    failures: list[tuple[datetime, str, int | None]] = []
    last = None
    shape = SHAPES.get(monitor.shape_key or "")
    for moment in moments(start, now, monitor.interval, rng):
        if in_maintenance(site, moment):
            continue
        outage = next((item for item in monitor.outages if item.start <= moment < item.end), None)
        blip = any(
            abs((moment - item).total_seconds()) < monitor.interval / 2 for item in monitor.blips
        )
        if outage or blip:
            error = outage.error if outage else "read timeout after 10s"
            code = outage.status_code if outage else None
            elapsed = rng.randint(30, 90) if code else None
            rows.append(
                (site.id, moment, code, elapsed, False, error, None, None, None, None, None, region)
            )
            failures.append((moment, error, code))
            last = (moment, False, code)
            continue
        if shape is None:
            rows.append(
                (site.id, moment, None, None, True, None, None, None, None, None, None, region)
            )
            last = (moment, True, None)
            continue
        factor = 3.1 if monitor.slow and monitor.slow[0] <= moment < monitor.slow[1] else 1.0
        if region != settings.worker_region:
            factor *= 1.35 if region == "fra" else 1.5
        total = latency(shape, moment, rng, factor)
        if site.monitor_type == "tcp":
            rows.append(
                (
                    site.id,
                    moment,
                    None,
                    total,
                    True,
                    None,
                    rng.randint(0, 2),
                    total,
                    None,
                    None,
                    None,
                    region,
                )
            )
        else:
            dns, connect, handshake, ttfb, transfer = phases(total, rng, shape.tls)
            rows.append(
                (
                    site.id,
                    moment,
                    200,
                    total,
                    True,
                    None,
                    dns,
                    connect,
                    handshake,
                    ttfb,
                    transfer,
                    region,
                )
            )
        last = (moment, True, 200)
    return Generated(rows, failures, last)


def derive_incidents(
    rows: list[tuple[object, ...]], threshold: int
) -> list[tuple[datetime, datetime, datetime | None, str]]:
    found: list[tuple[datetime, datetime, datetime | None, str]] = []
    streak: list[tuple[datetime, str]] = []
    opened: tuple[datetime, datetime, str] | None = None
    for row in rows:
        moment, ok, error = row[1], row[4], row[5]
        assert isinstance(moment, datetime)
        if not ok:
            streak.append((moment, str(error)))
            if opened is None and len(streak) >= threshold:
                opened = (streak[0][0], moment, streak[0][1])
            continue
        if opened is not None:
            found.append((opened[0], opened[1], moment, opened[2]))
            opened = None
        streak = []
    if opened is not None:
        found.append((opened[0], opened[1], None, opened[2]))
    return found


async def remove_previous(session: AsyncSession) -> None:
    owners = select(User.id).where((User.email == DEMO_EMAIL) | (User.email.like(TEAM_PATTERN)))
    await session.execute(update(Site).where(Site.user_id.in_(owners)).values(paused=True))
    await session.commit()
    for attempt in range(5):
        try:
            await session.execute(
                delete(User).where((User.email == DEMO_EMAIL) | (User.email.like(TEAM_PATTERN)))
            )
            await session.commit()
            return
        except DBAPIError as exc:
            await session.rollback()
            if "deadlock" not in str(exc.orig).lower() or attempt == 4:
                raise
            await asyncio.sleep(0.5 * (attempt + 1))


def build_monitors(user: User, now: datetime, rng: random.Random) -> list[Monitor]:
    target = settings.demo_target_host
    weekly = [
        {"weekdays": "6", "start_time": "03:00", "end_time": "04:00", "timezone": "Europe/Moscow"}
    ]
    api = Site(
        user_id=user.id,
        name="Публичный API",
        url=f"https://{target}:8443/api/health",
        interval_seconds=60,
        monitor_type="http",
        assertions=[{"source": "json", "path": "$.status", "op": "equals", "value": "ok"}],
        slo_target=99.5,
        tags=["prod", "api"],
        group="Ядро",
        regions=[settings.worker_region, "fra", "ams"],
        failure_threshold=2,
        cooldown_seconds=1800,
    )
    shop = Site(
        user_id=user.id,
        name="Витрина магазина",
        url=f"https://{target}:8443/shop",
        interval_seconds=120,
        monitor_type="http",
        slo_target=99.5,
        tags=["prod", "web"],
        group="Витрина",
        maintenance=weekly,
        check_domain=True,
        failure_threshold=2,
    )
    payments = Site(
        user_id=user.id,
        name="Платёжный шлюз",
        url=f"https://{target}:8443/payments/health",
        interval_seconds=60,
        monitor_type="keyword",
        keyword='"accepting":true',
        slo_target=99.95,
        tags=["prod", "billing"],
        group="Ядро",
        failure_threshold=2,
        cooldown_seconds=1800,
    )
    database = Site(
        user_id=user.id,
        name="PostgreSQL (pgbouncer)",
        url=f"http://{target}:6432",
        interval_seconds=300,
        monitor_type="tcp",
        slo_target=99.9,
        tags=["prod", "db"],
        group="Инфраструктура",
        failure_threshold=3,
    )
    certificate = Site(
        user_id=user.id,
        name="Сертификат cdn",
        url=f"https://{target}:9443/",
        interval_seconds=3600,
        monitor_type="ssl-only",
        tags=["tls"],
        group="Инфраструктура",
        expiry_thresholds=[30, 14, 7],
        failure_threshold=1,
    )
    backup = Site(
        user_id=user.id,
        name="Ночной бэкап",
        url="",
        interval_seconds=86400,
        monitor_type="heartbeat",
        heartbeat_period_seconds=86400,
        heartbeat_grace_seconds=3600,
        tags=["cron"],
        group="Инфраструктура",
        failure_threshold=1,
    )
    shop_outage = local(40, 14, 12, now) + timedelta(seconds=rng.randint(0, 50))
    api_outage = local(11, 10, 20, now)
    return [
        Monitor(
            "api",
            api,
            60,
            "api",
            outages=[Outage(api_outage, api_outage + timedelta(hours=2), "HTTP 503", 503)],
            blips=[now - timedelta(days=day, hours=rng.randint(0, 23)) for day in (63, 29, 5)],
            slow=(local(24, 9, 40, now), local(21, 9, 40, now)),
            regions=("fra", "ams"),
        ),
        Monitor(
            "shop",
            shop,
            120,
            "shop",
            outages=[Outage(shop_outage, shop_outage + timedelta(minutes=6), "HTTP 502", 502)],
            blips=[now - timedelta(days=day, hours=rng.randint(0, 23)) for day in (77, 52, 33, 8)],
        ),
        Monitor(
            "payments",
            payments,
            60,
            "payments",
            outages=[
                Outage(
                    now - timedelta(minutes=47, seconds=rng.randint(0, 40)),
                    now + timedelta(days=1),
                    "HTTP 503",
                    503,
                )
            ],
            blips=[now - timedelta(days=day, hours=rng.randint(0, 23)) for day in (70, 44, 15)],
        ),
        Monitor("database", database, 300, "database"),
        Monitor("certificate", certificate, 3600, None),
        Monitor("backup", backup, 86400, None),
    ]


async def copy_checks(rows: list[tuple[object, ...]]) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("ALTER TABLE checks DISABLE TRIGGER checks_rollup"))
        raw = await connection.get_raw_connection()
        driver = raw.driver_connection
        assert driver is not None
        await driver.copy_records_to_table("checks", records=rows, columns=CHECK_COLUMNS)
        await connection.execute(text("ALTER TABLE checks ENABLE TRIGGER checks_rollup"))


def note_times(start: datetime) -> list[tuple[timedelta, str, str]]:
    return [
        (
            timedelta(minutes=6),
            "investigating",
            "Видим 503 на /api/health во всех регионах. Разбираемся.",
        ),
        (
            timedelta(minutes=27),
            "identified",
            "Причина: исчерпан пул соединений к PostgreSQL после релиза 2.14.0. Откатываем релиз.",
        ),
        (
            timedelta(minutes=104),
            "monitoring",
            "Откат завершён, пул соединений увеличен. Ошибки прекратились, наблюдаем.",
        ),
        (timedelta(minutes=121), "resolved", "Инцидент закрыт. Постмортем опубликован."),
    ]


POSTMORTEM = """## Что произошло
Релиз 2.14.0 изменил настройку пула соединений: вместо 40 соединений на под осталось 10.
Под дневной нагрузкой запросы ждали соединение дольше таймаута балансировщика, API отвечал 503.

## Хронология
- 10:20: первые ошибки 503, подтверждение сбоя из трёх регионов через минуту.
- 10:26: дежурный взял инцидент в работу.
- 10:47: найдена причина, начат откат.
- 12:04: откат завершён, ошибки прекратились.
- 12:20: инцидент закрыт.

## Что меняем
1. Проверка параметров пула в CI по профилю нагрузки.
2. Алерт на долю ожидания соединения выше 20 %.
3. Канареечный выпуск на 5 % трафика перед полным релизом.
"""


async def seed_demo(password: str | None) -> tuple[str, str, str]:
    if settings.app_env != "dev":
        raise SystemExit("Демо-данные создаются только при APP_ENV=dev")
    rng = random.Random(20261104)
    now = datetime.now(UTC).replace(microsecond=0)
    start = now - timedelta(days=HISTORY_DAYS)
    secret = password or secrets.token_urlsafe(18)
    async with session_factory() as session:
        await remove_previous(session)
        plans = await demo_plans(session)
        user = User(
            email=DEMO_EMAIL,
            password_hash=hash_password(secret),
            email_verified_at=start - timedelta(days=5),
            tariff=UserTariff.pro,
            trial_used=True,
            is_admin=True,
            timezone="Europe/Moscow",
            created_at=start - timedelta(days=5),
        )
        session.add(user)
        await session.flush()
        await seed_team(session, plans, now, rng)
        heartbeat_token = await fill_account(session, user, plans, now, rng, "polka")
    from app.scheduler import refresh_baselines

    await refresh_baselines()
    ping = f"{settings.api_base_url.rstrip('/')}/api/heartbeat/{heartbeat_token}"
    return DEMO_EMAIL, secret, ping


async def demo_plans(session: AsyncSession) -> dict[str, Plan]:
    plans = {plan.code: plan for plan in await session.scalars(select(Plan))}
    if not {"free", "pro", "business"} <= plans.keys():
        raise SystemExit("Сначала выполните python -m app.seed")
    return plans


async def fill_account(
    session: AsyncSession,
    user: User,
    plans: dict[str, Plan],
    now: datetime,
    rng: random.Random,
    page_slug: str,
) -> str:
    start = now - timedelta(days=HISTORY_DAYS)
    subscription = await session.scalar(select(Subscription).where(Subscription.user_id == user.id))
    if subscription is None:
        subscription = Subscription(user_id=user.id, plan_id=plans["business"].id, status="active")
        session.add(subscription)
    subscription.plan_id = plans["business"].id
    subscription.status = "active"
    subscription.current_period_start = now - timedelta(days=18)
    subscription.current_period_end = now + timedelta(days=12)
    subscription.cancel_at_period_end = False
    subscription.trial_end = None
    user.tariff = UserTariff.pro
    await session.flush()
    for offset in (78, 48, 18):
        paid = now - timedelta(days=offset, hours=rng.randint(1, 6))
        payment_id = f"dev_{secrets.token_hex(12)}"
        session.add(
            Payment(
                user_id=user.id,
                subscription_id=subscription.id,
                plan_id=plans["business"].id,
                provider="dev",
                provider_payment_id=payment_id,
                amount_kop=plans["business"].price_kop,
                status="succeeded",
                created_at=paid - timedelta(minutes=2),
                paid_at=paid,
                raw_payload={"id": payment_id, "status": "succeeded"},
            )
        )
    monitors = build_monitors(user, now, rng)
    for monitor in monitors:
        monitor.site.created_at = start - timedelta(days=2)
        session.add(monitor.site)
    await session.flush()
    heartbeat_token = f"hb_{secrets.token_urlsafe(24)}"
    backup = next(item for item in monitors if item.key == "backup").site
    backup.heartbeat_token_hash = hash_refresh_token(heartbeat_token)
    backup.heartbeat_prefix = heartbeat_token[:10]
    email, slack = await seed_channels(session, user, monitors, start)
    rows: list[tuple[object, ...]] = []
    for monitor in monitors:
        if monitor.key == "backup":
            rows.extend(backup_rows(monitor.site, start, now, rng))
            continue
        generated = generate(monitor, start, now, settings.worker_region, rng)
        rows.extend(generated.rows)
        for region in monitor.regions:
            rows.extend(generate(monitor, now - timedelta(days=REGION_DAYS), now, region, rng).rows)
        settle_site(monitor, generated, now)
    await session.commit()
    await copy_checks(rows)
    ids = [monitor.site.id for monitor in monitors]
    await session.execute(ROLLUP_SQL, {"ids": ids})
    incidents = await seed_incidents(session, monitors, rows, now, email, slack)
    await seed_regions(session, monitors, now)
    await seed_page(session, user, monitors, now, page_slug)
    await seed_audit(session, user, monitors, incidents, now, rng)
    session.add(
        NotificationTemplate(
            user_id=user.id,
            event="down",
            body="🔴 {name} недоступен\nОшибка: {error}\nКод: {code}\n{url}",
        )
    )
    session.add(
        ApiKey(
            user_id=user.id,
            name="CI: деплой",
            prefix="pw_" + secrets.token_urlsafe(6)[:9],
            token_hash=hash_refresh_token(secrets.token_urlsafe(32)),
            scope="read",
            created_at=now - timedelta(days=31),
            last_used_at=now - timedelta(hours=3),
        )
    )
    await session.commit()
    for monitor in monitors:
        if monitor.key != "backup":
            monitor.site.next_check_at = datetime.now(UTC) + timedelta(
                seconds=random.Random(monitor.key).randint(5, 60)
            )
    await session.commit()
    return heartbeat_token


async def fill_current_account(user_id: int) -> tuple[int, str]:
    rng = random.Random(user_id)
    now = datetime.now(UTC).replace(microsecond=0)
    async with session_factory() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise SystemExit("Аккаунт не найден")
        plans = await demo_plans(session)
        token = await fill_account(session, user, plans, now, rng, f"demo-{user_id}")
    from app.scheduler import refresh_baselines

    await refresh_baselines()
    return 6, token


async def seed_team(
    session: AsyncSession, plans: dict[str, Plan], now: datetime, rng: random.Random
) -> None:
    for number in range(1, 24):
        joined = now - timedelta(days=rng.randint(1, HISTORY_DAYS), hours=rng.randint(0, 23))
        code = rng.choices(["free", "pro", "business"], weights=[11, 8, 4])[0]
        member = User(
            email=f"demo-team-{number:02d}@example.com",
            password_hash=hash_password(secrets.token_urlsafe(24)),
            email_verified_at=joined,
            tariff=UserTariff.free if code == "free" else UserTariff.pro,
            trial_used=True,
            created_at=joined,
        )
        session.add(member)
        await session.flush()
        period_start = max(joined, now - timedelta(days=rng.randint(1, 29)))
        subscription = Subscription(
            user_id=member.id,
            plan_id=plans[code].id,
            status="active",
            current_period_start=period_start,
            current_period_end=period_start + timedelta(days=30),
        )
        session.add(subscription)
        await session.flush()
        if code != "free":
            payment_id = f"dev_{secrets.token_hex(12)}"
            session.add(
                Payment(
                    user_id=member.id,
                    subscription_id=subscription.id,
                    plan_id=plans[code].id,
                    provider="dev",
                    provider_payment_id=payment_id,
                    amount_kop=plans[code].price_kop,
                    status="succeeded",
                    created_at=period_start,
                    paid_at=period_start,
                    raw_payload={"id": payment_id, "status": "succeeded"},
                )
            )


async def seed_channels(
    session: AsyncSession, user: User, monitors: list[Monitor], start: datetime
) -> tuple[NotificationChannel, NotificationChannel]:
    email = NotificationChannel(
        user_id=user.id,
        type="email",
        name="Почта дежурного",
        config=encrypt_config({"email": user.email}),
        verified_at=start,
        created_at=start,
    )
    slack = NotificationChannel(
        user_id=user.id,
        type="slack",
        name="Slack #ops-alerts",
        config=encrypt_config({"url": settings.demo_slack_url}),
        verified_at=start + timedelta(days=3),
        created_at=start + timedelta(days=3),
    )
    session.add_all([email, slack])
    await session.flush()
    for monitor in monitors:
        session.add(SiteChannel(site_id=monitor.site.id, channel_id=email.id))
        if monitor.key in {"api", "payments", "database"}:
            session.add(SiteChannel(site_id=monitor.site.id, channel_id=slack.id))
    session.add(
        EscalationRule(
            user_id=user.id,
            channel_id=slack.id,
            after_minutes=15,
            created_at=start + timedelta(days=4),
        )
    )
    return email, slack


def backup_rows(
    site: Site, start: datetime, now: datetime, rng: random.Random
) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    for offset in range(HISTORY_DAYS, -1, -1):
        moment = local(offset, 3, 0, now) + timedelta(minutes=rng.randint(4, 11))
        if start <= moment <= now:
            rows.append(
                (
                    site.id,
                    moment,
                    None,
                    rng.randint(240, 560) * 1000,
                    True,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    settings.worker_region,
                )
            )
    site.last_ping_at = max(row[1] for row in rows if isinstance(row[1], datetime))
    site.last_checked_at = site.last_ping_at
    site.status = SiteStatus.up
    site.next_check_at = site.last_ping_at + timedelta(seconds=86400 + 3600)
    return rows


def settle_site(monitor: Monitor, generated: Generated, now: datetime) -> None:
    site = monitor.site
    if generated.last is None:
        return
    moment, ok, _ = generated.last
    site.last_checked_at = moment
    site.next_check_at = now + timedelta(days=1)
    tail = 0
    for row in reversed(generated.rows):
        if row[4]:
            break
        tail += 1
    site.consecutive_failures = tail
    site.status = SiteStatus.down if tail >= site.failure_threshold else SiteStatus.up
    if monitor.key == "certificate":
        site.ssl_expires_at = now + timedelta(days=12, hours=5)
        site.ssl_checked_at = now - timedelta(minutes=20)
        site.ssl_details = {
            "issuer": "Pulsewatch Demo CA",
            "subject": settings.demo_target_host,
            "expires_at": site.ssl_expires_at.isoformat(),
            "tls_version": "TLSv1.3",
            "cipher": "TLS_AES_256_GCM_SHA384",
            "chain_complete": True,
            "trusted": True,
            "problem": None,
        }
    if monitor.key == "shop":
        site.domain_expires_at = now + timedelta(days=47)
        site.domain_checked_at = now - timedelta(hours=2)
    if site.monitor_type in {"http", "keyword"}:
        body = (
            '{"accepting":false,"reason":"upstream provider timeout"}'
            if not ok
            else '{"status":"ok","version":"2.14.3","db":"ok","queue_lag_ms":12}'
            if monitor.key == "api"
            else "<!doctype html><title>Полка</title>"
        )
        site.last_response_encrypted = encrypt_text(
            json.dumps(
                {
                    "status_code": 503 if not ok else 200,
                    "headers": {"content-type": "application/json", "server": "nginx"},
                    "body": body,
                },
                ensure_ascii=False,
            )
        )


async def seed_incidents(
    session: AsyncSession,
    monitors: list[Monitor],
    rows: list[tuple[object, ...]],
    now: datetime,
    email: NotificationChannel,
    slack: NotificationChannel,
) -> dict[str, Incident]:
    created: dict[str, Incident] = {}
    for monitor in monitors:
        site = monitor.site
        own = [row for row in rows if row[0] == site.id and row[11] == settings.worker_region]
        for started, confirmed, ended, reason in derive_incidents(own, site.failure_threshold):
            incident = Incident(
                site_id=site.id,
                kind="down",
                started_at=started,
                confirmed_at=confirmed,
                ended_at=ended,
                reason=reason,
            )
            session.add(incident)
            await session.flush()
            created[f"{monitor.key}:{started.date()}"] = incident
            code = 503 if "503" in reason else 502 if "502" in reason else None
            body = (
                b'{"accepting":false,"reason":"upstream provider timeout","retry_after":30}'
                if monitor.key == "payments"
                else b"<html><head><title>502 Bad Gateway</title></head><body>nginx</body></html>"
                if code == 502
                else b'{"error":"service unavailable","detail":"connection pool exhausted",'
                b'"pool":"primary","session_token":"expired"}'
            )
            for moment in (started, confirmed):
                session.add(
                    CheckDiagnostic(
                        site_id=site.id,
                        incident_id=incident.id,
                        created_at=moment,
                        region=settings.worker_region,
                        status_code=code,
                        error=reason,
                        ip="172.20.0.9",
                        resolved=["172.20.0.9"],
                        headers={
                            "content-type": "application/json" if code != 502 else "text/html",
                            "server": "nginx/1.27.2",
                            "retry-after": "30",
                            "set-cookie": "[redacted]",
                        },
                        body_encrypted=encrypt_text(redact_body(body)),
                        timings={
                            "dns_ms": 1,
                            "connect_ms": 2,
                            "tls_ms": 11,
                            "ttfb_ms": 38,
                            "transfer_ms": 1,
                        },
                    )
                )
            channels = [email, slack] if monitor.key in {"api", "payments"} else [email]
            for channel in channels:
                events: list[tuple[str, datetime | None, str]] = [
                    ("down", confirmed, f"🔴 {site.name} недоступен\nОшибка: {reason}"),
                    ("up", ended, f"🟢 {site.name}\nВосстановлен"),
                ]
                for event, sent_at, message in events:
                    if sent_at is None:
                        continue
                    session.add(
                        NotificationDelivery(
                            channel_id=channel.id,
                            payload={
                                "site_id": site.id,
                                "name": site.name,
                                "text": message,
                                "event": event,
                            },
                            status="sent",
                            attempts=1,
                            response_code=250 if channel.type == "email" else 200,
                            created_at=sent_at,
                            delivered_at=sent_at + timedelta(seconds=3),
                            next_attempt_at=sent_at,
                        )
                    )
        if monitor.slow:
            begin = monitor.slow[0] + timedelta(minutes=5)
            finish = monitor.slow[1] + timedelta(minutes=5)
            session.add(
                Incident(
                    site_id=site.id,
                    kind="degraded",
                    started_at=begin,
                    confirmed_at=begin,
                    ended_at=finish,
                    reason=f"Отклик {round(SHAPES['api'].base * 3.1)} мс при обычных "
                    f"{round(SHAPES['api'].base)} мс",
                )
            )
    await session.flush()
    long_incident = next(
        (item for key, item in created.items() if key.startswith("api:") and item.ended_at),
        None,
    )
    if long_incident is not None:
        long_incident.acknowledged_at = long_incident.started_at + timedelta(minutes=6)
        long_incident.postmortem = POSTMORTEM
        for offset, status, note in note_times(long_incident.started_at):
            session.add(
                IncidentNote(
                    incident_id=long_incident.id,
                    status=status,
                    text=note,
                    created_at=long_incident.started_at + offset,
                )
            )
        rule = await session.scalar(
            select(EscalationRule.id).where(EscalationRule.channel_id == slack.id)
        )
        if rule is not None:
            session.add(
                IncidentEscalation(
                    incident_id=long_incident.id,
                    rule_id=rule,
                    notified_at=long_incident.started_at + timedelta(minutes=15),
                )
            )
    current = next((item for item in created.values() if item.ended_at is None), None)
    if current is not None:
        session.add(
            IncidentNote(
                incident_id=current.id,
                status="investigating",
                text="Платёжный провайдер не отвечает. Связались с поддержкой провайдера.",
                created_at=current.started_at + timedelta(minutes=9),
            )
        )
        rule = await session.scalar(
            select(EscalationRule.id).where(EscalationRule.channel_id == slack.id)
        )
        if rule is not None and now - current.started_at > timedelta(minutes=15):
            session.add(
                IncidentEscalation(
                    incident_id=current.id,
                    rule_id=rule,
                    notified_at=current.started_at + timedelta(minutes=15),
                )
            )
    payments = next(item.site for item in monitors if item.key == "payments")
    if current is not None:
        payments.last_notified_at = current.confirmed_at
    return created


async def seed_regions(session: AsyncSession, monitors: list[Monitor], now: datetime) -> None:
    api = next(item for item in monitors if item.key == "api")
    for code in api.site.regions:
        latest = now - timedelta(seconds=random.Random(code).randint(5, 55))
        session.add(
            SiteRegionState(
                site_id=api.site.id,
                region=code,
                status="up",
                consecutive_failures=0,
                last_checked_at=latest,
                last_response_ms=round(
                    SHAPES["api"].base * (1.0 if code == settings.worker_region else 1.4)
                ),
                next_check_at=now + timedelta(seconds=30),
            )
        )


async def seed_page(
    session: AsyncSession, user: User, monitors: list[Monitor], now: datetime, slug: str
) -> None:
    page = StatusPage(
        user_id=user.id,
        slug=slug,
        title="Статус сервисов «Полки»",
        description="Доступность витрины, API и оплаты интернет-магазина «Полка».",
        accent="emerald",
        created_at=now - timedelta(days=85),
    )
    session.add(page)
    await session.flush()
    for monitor in monitors:
        if monitor.key in {"api", "shop", "payments"}:
            session.add(StatusPageSite(page_id=page.id, site_id=monitor.site.id))
    session.add(
        StatusAnnouncement(
            page_id=page.id,
            title="Плановые работы по воскресеньям",
            body="Каждое воскресенье с 03:00 до 04:00 МСК обновляем витрину. Возможны кратковременные "
            "задержки загрузки страниц.",
            pinned=True,
            created_at=now - timedelta(days=60),
        )
    )
    session.add(StatusSubscriber(page_id=page.id, email="subscriber@example.com", confirmed=True))


async def seed_audit(
    session: AsyncSession,
    user: User,
    monitors: list[Monitor],
    incidents: dict[str, Incident],
    now: datetime,
    rng: random.Random,
) -> None:
    start = now - timedelta(days=HISTORY_DAYS + 2)
    entries: list[tuple[datetime, str, str | None, int | None, list[dict[str, object]] | None]] = []
    for index, monitor in enumerate(monitors):
        entries.append(
            (start + timedelta(minutes=7 * index), "site.create", "site", monitor.site.id, None)
        )
    api = monitors[0].site
    entries += [
        (
            start + timedelta(days=1),
            "site.update",
            "site",
            api.id,
            [{"field": "interval_seconds", "old": 300, "new": 60}],
        ),
        (
            start + timedelta(days=3),
            "channel.create",
            "channel",
            None,
            [{"field": "type", "old": None, "new": "slack"}],
        ),
        (
            start + timedelta(days=4),
            "escalation.create",
            "escalation",
            None,
            [{"field": "after_minutes", "old": None, "new": 15}],
        ),
        (start + timedelta(days=7), "page.create", "status_page", None, None),
        (
            start + timedelta(days=12),
            "subscription.paid",
            "billing",
            None,
            [{"field": "plan", "old": "pro", "new": "business"}],
        ),
        (
            start + timedelta(days=30),
            "site.update",
            "site",
            api.id,
            [
                {"field": "slo_target", "old": None, "new": 99.9},
                {
                    "field": "assertions",
                    "old": [],
                    "new": [{"source": "json", "path": "$.status", "op": "equals", "value": "ok"}],
                },
            ],
        ),
        (
            start + timedelta(days=58),
            "site.update",
            "site",
            api.id,
            [{"field": "regions", "old": [], "new": api.regions}],
        ),
        (
            now - timedelta(days=9),
            "site.update",
            "site",
            monitors[1].site.id,
            [{"field": "maintenance", "old": [], "new": monitors[1].site.maintenance}],
        ),
    ]
    for key, incident in incidents.items():
        if key.startswith("api:") and incident.acknowledged_at:
            entries.append(
                (incident.acknowledged_at, "incident.acknowledge", "incident", incident.id, None)
            )
            entries.append(
                (
                    incident.started_at + timedelta(minutes=27),
                    "incident.note",
                    "incident",
                    incident.id,
                    [{"field": "status", "old": None, "new": "identified"}],
                )
            )
    for day in range(HISTORY_DAYS, 0, -3):
        entries.append(
            (
                now - timedelta(days=day, hours=rng.randint(7, 11)),
                "auth.login",
                "session",
                None,
                None,
            )
        )
    for moment, action, resource_type, resource_id, changes in sorted(
        entries, key=lambda item: item[0]
    ):
        session.add(
            AuditLog(
                user_id=user.id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                changes=changes,
                created_at=moment,
            )
        )


async def run(password: str | None) -> None:
    email, secret, ping = await seed_demo(password)
    sys.stdout.write(f"Demo login: {email}\nDemo password: {secret}\nDemo heartbeat URL: {ping}\n")
    await engine.dispose()
