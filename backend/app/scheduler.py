import asyncio
import logging
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.config import settings
from app.database import session_factory
from app.models import Site
from app.services.monitoring import process_probe_result, purge_old_checks
from app.services.probe import fetch_ssl_expiry, probe_site

logger = logging.getLogger("pulsewatch.scheduler")

scheduler = AsyncIOScheduler(timezone=UTC)
_semaphore = asyncio.Semaphore(settings.check_concurrency)


async def _refresh_ssl(site_id: int, url: str) -> None:
    expires = await fetch_ssl_expiry(url)
    if expires is None:
        return
    async with session_factory() as session:
        site = await session.get(Site, site_id)
        if site is None:
            return
        site.ssl_expires_at = expires
        site.ssl_checked_at = datetime.now(UTC)
        await session.commit()


async def _run_single_check(site_id: int, url: str, ssl_due: bool) -> None:
    async with _semaphore:
        result = await probe_site(url, settings.allow_private_targets)
        await process_probe_result(site_id, result)
        if ssl_due:
            try:
                await _refresh_ssl(site_id, url)
            except Exception:
                logger.exception("ssl_refresh_failed", extra={"extra_fields": {"site_id": site_id}})


async def run_due_checks() -> None:
    now = datetime.now(UTC)
    ssl_threshold = now - timedelta(hours=settings.ssl_refresh_hours)
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(
                    Site.id,
                    Site.url,
                    Site.ssl_checked_at,
                    Site.last_checked_at,
                    Site.interval_seconds,
                )
            )
        ).all()

    due: list[tuple[int, str, bool]] = []
    for site_id, url, ssl_checked_at, last_checked_at, interval_seconds in rows:
        next_run = (
            last_checked_at + timedelta(seconds=interval_seconds)
            if last_checked_at is not None
            else None
        )
        if next_run is not None and next_run > now:
            continue
        ssl_due = url.startswith("https://") and (
            ssl_checked_at is None or ssl_checked_at < ssl_threshold
        )
        due.append((site_id, url, ssl_due))

    if not due:
        return
    logger.info("scheduler_tick", extra={"extra_fields": {"due_sites": len(due)}})
    await asyncio.gather(*(_run_single_check(*item) for item in due))


async def purge_checks_job() -> None:
    async with session_factory() as session:
        deleted = await purge_old_checks(session, settings.checks_retention_days)
    if deleted:
        logger.info("checks_purged", extra={"extra_fields": {"deleted": deleted}})


def start_scheduler() -> None:
    scheduler.add_job(
        run_due_checks,
        trigger="interval",
        seconds=settings.scheduler_tick_seconds,
        id="run_due_checks",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(UTC) + timedelta(seconds=3),
    )
    scheduler.add_job(
        purge_checks_job,
        trigger="cron",
        hour=3,
        minute=30,
        id="purge_checks",
        max_instances=1,
    )
    scheduler.start()
    logger.info("scheduler_started")


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("scheduler_stopped")
