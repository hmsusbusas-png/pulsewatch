import asyncio

import pytest

from app.config import settings


async def test_worker_retries_tick_and_stops_without_signal_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import worker

    callbacks = []
    loop = asyncio.get_running_loop()

    def unsupported(*args: object) -> None:
        raise NotImplementedError

    monkeypatch.setattr(loop, "add_signal_handler", unsupported)
    monkeypatch.setattr(worker.signal, "signal", lambda sig, callback: callbacks.append(callback))
    monkeypatch.setattr(worker, "configure_logging", lambda level: None)
    monkeypatch.setattr(settings, "scheduler_tick_seconds", 0)
    ticks = 0
    checks = []

    async def alive() -> None:
        nonlocal ticks
        ticks += 1
        if ticks == 1:
            raise ConnectionError("temporary database outage")
        if ticks >= 3:
            callbacks[0]()

    async def check() -> None:
        checks.append("checked")

    monkeypatch.setattr(worker, "heartbeat", alive)
    monkeypatch.setattr(worker, "run_due_checks", check)
    await asyncio.wait_for(worker.main(), 5)
    assert ticks >= 3 and checks
