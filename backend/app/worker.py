import asyncio
import contextlib
import logging
import signal
import time

import asyncpg

from app.config import settings
from app.database import engine, session_factory
from app.logging_conf import configure_logging
from app.scheduler import (
    cleanup,
    escalate_incidents,
    heartbeat,
    refresh_baselines,
    run_due_checks,
)
from app.services.billing import billing_maintenance
from app.services.events import WAKEUP_CHANNEL, raw_dsn
from app.services.notifications import deliver_mail, deliver_pending
from app.services.reports import weekly_reports

logger = logging.getLogger("pulsewatch.worker")


async def pulse(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=15)
        except TimeoutError:
            try:
                await heartbeat()
            except Exception:
                logger.exception("heartbeat_failed")


class Doorbell:
    def __init__(self) -> None:
        self.rung = asyncio.Event()
        self.connection: asyncpg.Connection | None = None

    async def listen(self) -> None:
        if self.connection is not None and not self.connection.is_closed():
            return
        try:
            self.connection = await asyncpg.connect(raw_dsn())
            await self.connection.add_listener(WAKEUP_CHANNEL, self._ring)
        except (OSError, asyncpg.PostgresError):
            self.connection = None
            logger.warning("wakeup_listener_unavailable")

    def _ring(self, *_: object) -> None:
        self.rung.set()

    async def wait(self, stop: asyncio.Event, seconds: float) -> None:
        waiters = {asyncio.ensure_future(stop.wait()), asyncio.ensure_future(self.rung.wait())}
        try:
            await asyncio.wait(waiters, timeout=seconds, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiter in waiters:
                waiter.cancel()
            self.rung.clear()

    async def close(self) -> None:
        if self.connection is not None and not self.connection.is_closed():
            with contextlib.suppress(OSError, asyncpg.PostgresError):
                await self.connection.close()


async def main() -> None:
    from app.config import validate_runtime

    validate_runtime()
    configure_logging(settings.log_level)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
    hourly = 0.0
    running: asyncio.Task[None] | None = None
    doorbell = Doorbell()
    pulse_task = asyncio.create_task(pulse(stop))
    try:
        while not stop.is_set():
            try:
                await doorbell.listen()
                await heartbeat()
                if running is None or running.done():
                    if running:
                        running.result()
                    running = asyncio.create_task(run_due_checks())
                async with session_factory() as session:
                    await deliver_pending(session)
                async with session_factory() as session:
                    await deliver_mail(session)
                await escalate_incidents()
                if time.monotonic() >= hourly:
                    async with session_factory() as session:
                        await billing_maintenance(session)
                        await weekly_reports(session)
                    await refresh_baselines()
                    await cleanup()
                    hourly = time.monotonic() + 3600
            except Exception:
                logger.exception("worker_tick_failed")
            await doorbell.wait(stop, settings.scheduler_tick_seconds)
    finally:
        stop.set()
        await pulse_task
        if running:
            await running
        await doorbell.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
