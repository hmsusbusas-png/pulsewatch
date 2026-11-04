import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.errors import ApiError

logger = logging.getLogger("pulsewatch.events")
CHANNEL = "pw_events"
WAKEUP_CHANNEL = "pw_wakeup"
EVENT_KINDS = (
    "check_completed",
    "incident_opened",
    "incident_resolved",
    "site_updated",
    "subscription_changed",
)
HEARTBEAT_SECONDS = 20
QUEUE_SIZE = 200


@dataclass(frozen=True)
class Event:
    id: int
    kind: str
    data: dict[str, Any]


CLOSE = Event(0, "close", {})
RESYNC = "resync"


def encode(value: object) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value)


async def publish(session: AsyncSession, user_id: int, kind: str, data: dict[str, Any]) -> None:
    payload = json.dumps(
        {"u": user_id, "k": kind, "d": data},
        default=encode,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    await session.execute(
        text("SELECT pg_notify(:channel, :payload)"), {"channel": CHANNEL, "payload": payload}
    )


async def wake_worker(session: AsyncSession, site_id: int) -> None:
    await session.execute(
        text("SELECT pg_notify(:channel, :payload)"),
        {"channel": WAKEUP_CHANNEL, "payload": str(site_id)},
    )


def raw_dsn() -> str:
    return (
        make_url(settings.database_url)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )


class EventHub:
    def __init__(self) -> None:
        self._queues: dict[int, set[asyncio.Queue[Event]]] = {}
        self._connection: asyncpg.Connection | None = None
        self._lock = asyncio.Lock()
        self._sequence = 0

    def connections(self, user_id: int) -> int:
        return len(self._queues.get(user_id, ()))

    async def subscribe(self, user_id: int) -> asyncio.Queue[Event]:
        if self.connections(user_id) >= settings.stream_max_per_user:
            raise ApiError(
                429,
                "stream_limit",
                f"Не более {settings.stream_max_per_user} потоков событий на пользователя",
            )
        await self._listen()
        queue: asyncio.Queue[Event] = asyncio.Queue(QUEUE_SIZE)
        self._queues.setdefault(user_id, set()).add(queue)
        return queue

    def unsubscribe(self, user_id: int, queue: asyncio.Queue[Event]) -> None:
        queues = self._queues.get(user_id)
        if queues is None:
            return
        queues.discard(queue)
        if not queues:
            self._queues.pop(user_id, None)

    async def _listen(self) -> None:
        async with self._lock:
            if self._connection is not None and not self._connection.is_closed():
                return
            connection = await asyncpg.connect(raw_dsn())
            await connection.add_listener(CHANNEL, self._receive)
            connection.add_termination_listener(self._terminated)
            self._connection = connection

    def _receive(self, _: object, __: int, ___: str, payload: str) -> None:
        try:
            message = json.loads(payload)
            user_id, kind, data = int(message["u"]), str(message["k"]), dict(message["d"])
        except (ValueError, KeyError, TypeError):
            logger.warning("event_payload_invalid")
            return
        self.dispatch(user_id, kind, data)

    def dispatch(self, user_id: int, kind: str, data: dict[str, Any]) -> None:
        self._sequence += 1
        event = Event(self._sequence, kind, data)
        for queue in list(self._queues.get(user_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(Event(self._sequence, RESYNC, {}))

    def _terminated(self, _: object) -> None:
        self._connection = None
        logger.warning("event_listener_terminated")
        for queues in self._queues.values():
            for queue in queues:
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(CLOSE)

    async def close(self) -> None:
        for queues in list(self._queues.values()):
            for queue in queues:
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(CLOSE)
        connection, self._connection = self._connection, None
        if connection is not None and not connection.is_closed():
            await connection.close()


hub = EventHub()


def frame(event: Event) -> str:
    body = json.dumps(event.data, default=str, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.id}\nevent: {event.kind}\ndata: {body}\n\n"


async def event_stream(user_id: int, queue: asyncio.Queue[Event]) -> AsyncIterator[str]:
    try:
        yield f'retry: 5000\nevent: ready\ndata: {{"heartbeat":{HEARTBEAT_SECONDS}}}\n\n'
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), HEARTBEAT_SECONDS)
            except TimeoutError:
                yield ": heartbeat\n\n"
                continue
            if event is CLOSE:
                yield "event: close\ndata: {}\n\n"
                return
            yield frame(event)
    finally:
        hub.unsubscribe(user_id, queue)
