"""Fill the database with demo sites, checks and incidents."""

import asyncio
import random
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from sqlalchemy import delete, select

from app.database import session_factory
from app.models import Check, Incident, RefreshToken, Site, SiteStatus, User, UserTariff
from app.security import hash_password

DEMO_EMAIL = "demo@pulsewatch.dev"
DEMO_PASSWORD = "demo12345"
INTERVAL_SECONDS = 300
HISTORY_DAYS = 7


class SiteBlueprint(NamedTuple):
    name: str
    url: str
    status: SiteStatus
    base_ms: int
    spread_ms: int
    failure_rate: float


SITES = [
    SiteBlueprint("Pulsewatch Web", "https://pulsewatch.dev", SiteStatus.up, 142, 60, 0.002),
    SiteBlueprint("API Gateway", "https://api.pulsewatch.dev", SiteStatus.up, 78, 35, 0.004),
    SiteBlueprint("Checkout", "https://checkout.pulsewatch.dev", SiteStatus.down, 610, 240, 0.12),
    SiteBlueprint("Status Page", "https://status.pulsewatch.dev", SiteStatus.up, 96, 30, 0.001),
    SiteBlueprint("Docs CDN", "https://docs.pulsewatch.dev", SiteStatus.up, 54, 18, 0.003),
    SiteBlueprint(
        "Internal Admin", "https://admin.pulsewatch.dev", SiteStatus.unknown, 320, 120, 0.05
    ),
]

DOWN_REASONS = [
    "connection refused",
    "request timed out",
    "unexpected status code 503",
]


def build_check_times(now: datetime) -> list[datetime]:
    step = timedelta(seconds=INTERVAL_SECONDS)
    total = int(timedelta(days=HISTORY_DAYS).total_seconds() // INTERVAL_SECONDS)
    start = now - step * total
    return [start + step * index for index in range(total + 1)]


async def clear_demo_user() -> None:
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.email == DEMO_EMAIL))
        if user is None:
            return
        site_ids = list(await session.scalars(select(Site.id).where(Site.user_id == user.id)))
        if site_ids:
            await session.execute(delete(Incident).where(Incident.site_id.in_(site_ids)))
            await session.execute(delete(Check).where(Check.site_id.in_(site_ids)))
            await session.execute(delete(Site).where(Site.id.in_(site_ids)))
        await session.execute(delete(RefreshToken).where(RefreshToken.user_id == user.id))
        await session.execute(delete(User).where(User.id == user.id))
        await session.commit()


def build_checks(site_id: int, blueprint: SiteBlueprint, times: list[datetime]) -> list[Check]:
    if blueprint.status is SiteStatus.unknown:
        return []
    checks: list[Check] = []
    tail_start = len(times) - 6
    for index, checked_at in enumerate(times):
        failing = random.random() < blueprint.failure_rate
        if blueprint.status is SiteStatus.down and index >= tail_start:
            failing = True
        if failing:
            checks.append(
                Check(
                    site_id=site_id,
                    checked_at=checked_at,
                    status_code=None,
                    response_ms=None,
                    ok=False,
                    error=random.choice(DOWN_REASONS),
                )
            )
            continue
        checks.append(
            Check(
                site_id=site_id,
                checked_at=checked_at,
                status_code=200,
                response_ms=max(18, int(random.gauss(blueprint.base_ms, blueprint.spread_ms))),
                ok=True,
                error=None,
            )
        )
    return checks


def build_incidents(blueprint: SiteBlueprint, times: list[datetime]) -> list[Incident]:
    if blueprint.status is SiteStatus.unknown:
        return []
    windows: list[tuple[datetime, datetime | None, str]] = []
    if blueprint.status is SiteStatus.down:
        started = times[-1] - timedelta(minutes=random.randint(18, 45))
        windows.append((started, None, random.choice(DOWN_REASONS)))
    for _ in range(random.randint(1, 2)):
        start_index = random.randint(40, len(times) - 40)
        started = times[start_index]
        ended = started + timedelta(seconds=INTERVAL_SECONDS * random.randint(2, 9))
        windows.append((started, ended, random.choice(DOWN_REASONS)))
    return [
        Incident(started_at=opened, ended_at=closed, reason=reason)
        for opened, closed, reason in windows
    ]


async def seed() -> None:
    random.seed(20261)
    await clear_demo_user()

    now = datetime.now(UTC)
    times = build_check_times(now)

    async with session_factory() as session:
        user = User(
            email=DEMO_EMAIL,
            password_hash=hash_password(DEMO_PASSWORD),
            tariff=UserTariff.pro,
        )
        session.add(user)
        await session.flush()

        for blueprint in SITES:
            site = Site(
                user_id=user.id,
                name=blueprint.name,
                url=blueprint.url,
                interval_seconds=INTERVAL_SECONDS,
                is_public=True,
                show_url_publicly=True,
                status=blueprint.status,
                consecutive_failures=3 if blueprint.status is SiteStatus.down else 0,
                last_checked_at=None if blueprint.status is SiteStatus.unknown else times[-1],
                ssl_expires_at=None
                if blueprint.status is SiteStatus.unknown
                else now + timedelta(days=random.randint(12, 180)),
                ssl_checked_at=None
                if blueprint.status is SiteStatus.unknown
                else now - timedelta(hours=random.randint(1, 5)),
            )
            session.add(site)
            await session.flush()

            session.add_all(build_checks(site.id, blueprint, times))
            for incident in build_incidents(blueprint, times):
                incident.site_id = site.id
                session.add(incident)

        await session.commit()


if __name__ == "__main__":
    asyncio.run(seed())
    print(f"seeded demo account {DEMO_EMAIL} with password {DEMO_PASSWORD}")
