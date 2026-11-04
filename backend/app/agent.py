import asyncio
import contextlib
import logging
import signal

import httpx

from app.config import settings
from app.logging_conf import configure_logging
from app.models import Site
from app.routers.agents import Job
from app.services.probe import ProbeResult, probe_monitor

logger = logging.getLogger("pulsewatch.agent")
IDLE_SECONDS = 5


def job_site(job: Job) -> Site:
    return Site(
        id=job.site_id,
        monitor_type=job.monitor_type,
        url=job.url,
        method=job.method,
        body=job.body,
        expected_codes=job.expected_codes,
        timeout_seconds=job.timeout_seconds,
        follow_redirects=job.follow_redirects,
        keyword=job.keyword,
        keyword_present=job.keyword_present,
        assertions=job.assertions,
        tcp_send=job.tcp_send,
        tcp_expect=job.tcp_expect,
        dns_record_type=job.dns_record_type,
        dns_expected=job.dns_expected,
    )


async def execute(job: Job) -> dict[str, object]:
    site = job_site(job)
    try:
        result = await asyncio.wait_for(
            probe_monitor(site, job.headers), timeout=site.timeout_seconds + 2
        )
    except TimeoutError:
        result = ProbeResult(False, None, None, "Истёк таймаут проверки")
    return {
        "site_id": job.site_id,
        "lease": job.lease,
        "ok": result.ok,
        "status_code": result.status_code,
        "response_ms": result.response_ms,
        "error": result.error,
        "timings": result.timings.as_dict() if result.timings else {},
    }


async def cycle(client: httpx.AsyncClient) -> int:
    response = await client.post("/api/agent/claim", json={"limit": 20})
    response.raise_for_status()
    jobs = [Job.model_validate(item) for item in response.json()]
    if not jobs:
        return 0
    results = await asyncio.gather(*(execute(job) for job in jobs))
    delivered = await client.post("/api/agent/results", json={"results": results})
    delivered.raise_for_status()
    return len(jobs)


async def main() -> None:
    configure_logging(settings.log_level)
    if len(settings.agent_token) < 32:
        raise SystemExit("AGENT_TOKEN: нужен токен региона длиной от 32 символов")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    logger.info("agent_started", extra={"extra_fields": {"region": settings.worker_region}})
    async with httpx.AsyncClient(
        base_url=settings.agent_api_url,
        headers={
            "Authorization": f"Agent {settings.agent_token}",
            "User-Agent": "pulsewatch-agent",
        },
        timeout=30,
        trust_env=False,
    ) as client:
        while not stop.is_set():
            handled = 0
            try:
                handled = await cycle(client)
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning(
                    "agent_cycle_failed", extra={"extra_fields": {"kind": type(exc).__name__}}
                )
            if handled:
                continue
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), IDLE_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
