import json
from pathlib import Path

CRITICAL = (
    "app/crypto.py",
    "app/http_guard.py",
    "app/logging_conf.py",
    "app/rotate_keys.py",
    "app/seed.py",
    "app/deps.py",
    "app/security.py",
    "app/ssrf.py",
    "app/scheduler.py",
    "app/worker.py",
    "app/services/billing.py",
    "app/services/payments.py",
    "app/services/monitoring.py",
    "app/services/monitors.py",
    "app/services/notifications.py",
    "app/services/probe.py",
    "app/services/rate_limit.py",
    "app/services/telegram.py",
    "app/idempotency.py",
    "app/pagination.py",
    "app/agent.py",
    "app/demo.py",
    "app/routers/agents.py",
    "app/routers/heartbeat.py",
    "app/routers/stream.py",
    "app/routers/sites.py",
    "app/routers/incidents.py",
    "app/routers/insights.py",
    "app/routers/channels.py",
    "app/routers/public.py",
    "app/routers/transfer.py",
    "app/services/assertions.py",
    "app/services/certificates.py",
    "app/services/channel_formats.py",
    "app/services/devices.py",
    "app/services/dns_probe.py",
    "app/services/domains.py",
    "app/services/events.py",
    "app/services/redaction.py",
    "app/services/stats.py",
    "app/services/templates.py",
)


def main() -> None:
    report = json.loads(Path("coverage.json").read_text(encoding="utf-8"))
    files = {path.replace("\\", "/"): data for path, data in report["files"].items()}
    failures = [name for name in CRITICAL if files[name]["summary"]["percent_covered"] < 80]
    if failures:
        raise SystemExit("Покрытие ниже 80%: " + ", ".join(failures))


if __name__ == "__main__":
    main()
