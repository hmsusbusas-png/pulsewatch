import asyncio

from sqlalchemy import select

from app.crypto import cipher
from app.database import engine, session_factory
from app.models import MailJob, NotificationChannel, Site


async def rotate() -> None:
    keys = cipher()
    async with session_factory() as session, session.begin():
        for channel in await session.scalars(select(NotificationChannel).with_for_update()):
            channel.config = keys.rotate(channel.config.encode()).decode()
        for site in await session.scalars(
            select(Site).where(Site.headers_encrypted.is_not(None)).with_for_update()
        ):
            if site.headers_encrypted:
                site.headers_encrypted = keys.rotate(site.headers_encrypted.encode()).decode()
        for job in await session.scalars(
            select(MailJob).where(MailJob.body != "").with_for_update()
        ):
            job.body = keys.rotate(job.body.encode()).decode()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(rotate())
