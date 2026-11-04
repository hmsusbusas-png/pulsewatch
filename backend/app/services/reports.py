from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto import encrypt_text
from app.models import DailyUptime, Incident, MailJob, Site, User
from app.services.monitoring import calculate_uptime


async def weekly_reports(session: AsyncSession) -> None:
    now = datetime.now(UTC)
    if now.weekday() != 0:
        return
    since = now - timedelta(days=7)
    for user in await session.scalars(select(User)):
        sites = list(await session.scalars(select(Site).where(Site.user_id == user.id)))
        if not sites:
            continue
        ids = [site.id for site in sites]
        incidents = await session.scalar(
            select(func.count(Incident.id)).where(
                Incident.site_id.in_(ids), Incident.started_at >= since
            )
        )
        slowest = await session.scalar(
            select(Site.name)
            .join(DailyUptime)
            .where(Site.id.in_(ids), DailyUptime.day >= since.date())
            .group_by(Site.id)
            .order_by(
                (
                    func.sum(DailyUptime.response_ms_sum)
                    / func.nullif(func.sum(DailyUptime.successful), 0)
                )
                .desc()
                .nulls_last()
            )
            .limit(1)
        )
        lines = [
            f"{site.name}: {await calculate_uptime(session, site.id, since)}%" for site in sites
        ]
        body = "\n".join(
            [
                "Отчёт за неделю",
                *lines,
                f"Инцидентов: {incidents}",
                f"Самый медленный сайт: {slowest or 'нет данных'}",
            ]
        )
        await session.execute(
            insert(MailJob)
            .values(
                user_id=user.id,
                recipient=user.email,
                subject="Pulsewatch: отчёт за неделю",
                body=encrypt_text(body),
                status="pending",
                attempts=0,
                dedupe_key=f"weekly:{user.id}:{now.date().isoformat()}",
            )
            .on_conflict_do_nothing(index_elements=["dedupe_key"])
        )
    await session.commit()
