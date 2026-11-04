import argparse
import asyncio
import secrets
import sys
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import session_factory
from app.models import AuditLog, NotificationChannel, Plan, Site, SiteChannel, Subscription, User
from app.security import hash_password

CORE_FEATURES = {"custom_slug": True, "keyword": True, "headers": True, "post": True}
PRO_FEATURES = {
    **CORE_FEATURES,
    "heartbeat": True,
    "dns": True,
    "assertions": True,
    "domain_expiry": True,
    "slo": True,
    "escalations": True,
}
PLANS = [
    {
        "code": "free",
        "name": "Free",
        "price_kop": 0,
        "period_days": 30,
        "max_sites": 3,
        "min_interval_sec": 300,
        "retention_days": 7,
        "max_channels": 1,
        "max_regions": 1,
        "features": {"heartbeat": True},
    },
    {
        "code": "pro",
        "name": "Pro",
        "price_kop": 99000,
        "period_days": 30,
        "max_sites": 20,
        "min_interval_sec": 60,
        "retention_days": 90,
        "max_channels": 5,
        "max_regions": 2,
        "features": PRO_FEATURES,
    },
    {
        "code": "business",
        "name": "Business",
        "price_kop": 299000,
        "period_days": 30,
        "max_sites": 100,
        "min_interval_sec": 30,
        "retention_days": 365,
        "max_channels": None,
        "max_regions": 3,
        "features": {
            **PRO_FEATURES,
            "maintenance": True,
            "api_keys": True,
            "csv": True,
            "regions": True,
            "templates": True,
        },
    },
]


async def seed_plans(session: AsyncSession) -> None:
    for row in PLANS:
        values = {key: value for key, value in row.items() if key != "code"}
        await session.execute(
            insert(Plan).values(**row).on_conflict_do_update(index_elements=["code"], set_=values)
        )


async def seed_regions(session: AsyncSession) -> None:
    from app.models import Region
    from app.security import hash_refresh_token

    for code, entry in settings.region_agents.items():
        token = entry.get("token", "")
        if len(token) < 32:
            continue
        name = entry.get("name") or code
        await session.execute(
            insert(Region)
            .values(code=code, name=name, token_hash=hash_refresh_token(token), is_primary=False)
            .on_conflict_do_update(
                index_elements=["code"],
                set_={"name": name, "token_hash": hash_refresh_token(token)},
            )
        )


async def seed(demo_user: bool = True) -> None:
    async with session_factory() as session:
        await seed_plans(session)
        await seed_regions(session)
        await migrate_accounts(session)
        if demo_user and settings.app_env == "dev":
            user = await session.scalar(select(User).where(User.email == settings.demo_email))
            if user is None:
                from app.services.billing import grant_trial

                password = secrets.token_urlsafe(24)
                user = User(
                    email=settings.demo_email,
                    password_hash=hash_password(password),
                    email_verified_at=datetime.now(UTC),
                )
                session.add(user)
                await session.flush()
                await grant_trial(session, user)
                await session.commit()
                sys.stdout.write(f"Demo login: {settings.demo_email}\nDemo password: {password}\n")
        await session.commit()


async def migrate_accounts(session: AsyncSession) -> None:
    from datetime import UTC, datetime, timedelta

    from app.crypto import encrypt_config
    from app.services.billing import apply_limits, plan_by_code

    users = list(
        await session.scalars(
            select(User)
            .outerjoin(Subscription, Subscription.user_id == User.id)
            .where(Subscription.id.is_(None))
            .with_for_update(of=User)
        )
    )
    for user in users:
        plan = await plan_by_code(session, user.tariff.value)
        now = datetime.now(UTC)
        user.trial_used = True
        session.add(
            Subscription(
                user_id=user.id,
                plan_id=plan.id,
                status="active",
                current_period_start=now,
                current_period_end=now + timedelta(days=plan.period_days),
            )
        )
        await apply_limits(session, user.id, plan)
        session.add(AuditLog(user_id=user.id, action="subscription.migrated"))
    linked = await session.scalars(
        select(User)
        .where(
            User.telegram_chat_id.is_not(None),
            ~select(NotificationChannel.id)
            .where(NotificationChannel.user_id == User.id, NotificationChannel.type == "telegram")
            .exists(),
        )
        .with_for_update()
    )
    for user in linked:
        channel = NotificationChannel(
            user_id=user.id,
            name="Telegram",
            type="telegram",
            config=encrypt_config({"chat_id": str(user.telegram_chat_id)}),
            verified_at=datetime.now(UTC),
        )
        session.add(channel)
        await session.flush()
        for site_id in await session.scalars(select(Site.id).where(Site.user_id == user.id)):
            session.add(SiteChannel(site_id=site_id, channel_id=channel.id))


def main() -> None:
    parser = argparse.ArgumentParser(description="Тарифы, регионы и демо-данные Pulsewatch")
    parser.add_argument("--demo", action="store_true", help="пересоздать демо-аккаунт и историю")
    parser.add_argument("--password", help="пароль демо-аккаунта; без него будет сгенерирован")
    args = parser.parse_args()
    asyncio.run(prepare(args.demo, args.password))


async def prepare(demo: bool, password: str | None) -> None:
    from app.database import engine

    await seed(demo_user=not demo)
    if demo:
        from app.demo import run

        await run(password)
    await engine.dispose()


if __name__ == "__main__":
    main()
