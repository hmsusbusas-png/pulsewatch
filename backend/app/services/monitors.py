import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.crypto import decrypt_config, encrypt_config
from app.errors import ApiError
from app.models import NotificationChannel, Plan, Region, Site, SiteChannel, SiteRegionState
from app.services.assertions import validate_assertions
from app.services.billing import require_feature

FIELDS = {
    "monitor_type",
    "method",
    "body",
    "expected_codes",
    "timeout_seconds",
    "follow_redirects",
    "keyword",
    "keyword_present",
    "failure_threshold",
    "cooldown_seconds",
    "maintenance",
    "tags",
    "group",
    "paused",
    "tcp_send",
    "tcp_expect",
    "assertions",
    "expiry_thresholds",
    "check_domain",
    "slo_target",
    "dns_record_type",
    "dns_expected",
    "heartbeat_period_seconds",
    "heartbeat_grace_seconds",
    "regions",
}
NULLABLE = {
    "body",
    "keyword",
    "group",
    "tcp_send",
    "tcp_expect",
    "slo_target",
    "dns_expected",
}
HOST_MONITORS = {"http", "keyword", "tcp", "ssl-only"}


def validate_monitor(plan: Plan, site: Site) -> None:
    if site.monitor_type != "heartbeat" and site.interval_seconds < plan.min_interval_sec:
        raise ApiError(
            402,
            "plan_limit_interval",
            f"Минимальный интервал тарифа: {plan.min_interval_sec} секунд",
        )
    if site.monitor_type == "keyword":
        require_feature(plan, "keyword")
        if not site.keyword:
            raise ApiError(422, "keyword_required", "Укажите ключевое слово")
    if site.monitor_type == "heartbeat":
        require_feature(plan, "heartbeat")
        period = site.heartbeat_period_seconds or 0
        if not 60 <= period <= 2592000:
            raise ApiError(422, "invalid_heartbeat", "Период сигнала: от 1 минуты до 30 дней")
        if not 0 <= (site.heartbeat_grace_seconds or 0) <= 86400:
            raise ApiError(422, "invalid_heartbeat", "Допуск: от 0 до 24 часов")
    if site.monitor_type == "dns":
        require_feature(plan, "dns")
        from app.services.dns_probe import RECORD_TYPES

        if (site.dns_record_type or "") not in RECORD_TYPES:
            raise ApiError(422, "invalid_dns_type", "Тип записи: A, AAAA, CNAME, MX, TXT или NS")
    if site.headers_encrypted:
        require_feature(plan, "headers")
    if site.method == "POST" or site.body:
        require_feature(plan, "post")
    if site.assertions:
        require_feature(plan, "assertions")
    if site.check_domain:
        require_feature(plan, "domain_expiry")
    if site.slo_target is not None:
        require_feature(plan, "slo")
        if not 90 <= site.slo_target < 100:
            raise ApiError(422, "invalid_slo", "Цель SLO: от 90 до 99,999 %")
    if len(site.regions) > 1:
        require_feature(plan, "regions")
    if len(site.regions) > plan.max_regions:
        raise ApiError(
            402, "plan_limit_regions", f"Тариф {plan.name}: не более {plan.max_regions} регионов"
        )
    thresholds = site.expiry_thresholds
    if not 1 <= len(thresholds) <= 5 or any(not 1 <= day <= 365 for day in thresholds):
        raise ApiError(
            422, "invalid_thresholds", "Пороги оповещения: 1-5 значений от 1 до 365 дней"
        )
    site.expiry_thresholds = sorted(set(thresholds), reverse=True)
    if any(not 100 <= code <= 599 for code in site.expected_codes):
        raise ApiError(422, "invalid_codes", "Коды ответа должны быть в диапазоне 100-599")
    if site.monitor_type == "ssl-only" and not site.url.startswith("https://"):
        raise ApiError(422, "https_required", "Проверка SSL требует HTTPS")
    if site.monitor_type == "tcp" and urlparse(site.url).port is None:
        raise ApiError(422, "tcp_port_required", "Для TCP укажите порт")
    if any(len(tag) > 50 or not tag.strip() for tag in site.tags):
        raise ApiError(422, "invalid_tags", "Теги должны содержать 1-50 символов")
    if site.maintenance:
        require_feature(plan, "maintenance")
        for window in site.maintenance:
            try:
                if "weekdays" in window:
                    _recurring_window(window)
                    continue
                start, end = (
                    datetime.fromisoformat(window["start"]),
                    datetime.fromisoformat(window["end"]),
                )
                if start.tzinfo is None or end.tzinfo is None or end <= start:
                    raise ValueError("invalid window")
            except (KeyError, ValueError) as exc:
                raise ApiError(
                    422,
                    "invalid_maintenance",
                    "Нужны start и end с часовым поясом, end позже start",
                ) from exc


async def _sync_regions(session: AsyncSession, site: Site) -> None:
    codes = list(dict.fromkeys(site.regions))
    if len(codes) > 1 and settings.worker_region not in codes:
        codes.insert(0, settings.worker_region)
    if codes:
        known = set(await session.scalars(select(Region.code).where(Region.code.in_(codes))))
        known.add(settings.worker_region)
        missing = [code for code in codes if code not in known]
        if missing:
            raise ApiError(422, "region_unknown", f"Неизвестные регионы: {', '.join(missing)}")
    site.regions = codes if len(codes) > 1 else []
    await session.flush()
    await session.execute(
        delete(SiteRegionState).where(
            SiteRegionState.site_id == site.id, SiteRegionState.region.not_in(site.regions or [""])
        )
    )
    for code in site.regions:
        await session.execute(
            insert(SiteRegionState)
            .values(site_id=site.id, region=code)
            .on_conflict_do_nothing(index_elements=["site_id", "region"])
        )


async def configure_monitor(
    session: AsyncSession, site: Site, values: dict[str, object], plan: Plan
) -> None:
    for field in FIELDS & values.keys():
        value = values[field]
        if value is not None or field in NULLABLE:
            setattr(site, field, value)
    if "assertions" in values and isinstance(values["assertions"], list):
        site.assertions = validate_assertions(values["assertions"])
    headers = values.get("headers")
    if isinstance(headers, dict):
        if sum(len(str(k)) + len(str(v)) for k, v in headers.items()) > 8192:
            raise ApiError(422, "headers_too_large", "Заголовки не должны превышать 8 КБ")
        current = decrypt_config(site.headers_encrypted) if site.headers_encrypted else {}
        clean: dict[str, str] = {}
        for key, value in headers.items():
            if (
                not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key)
                or key.lower() in {"host", "content-length", "transfer-encoding", "connection"}
                or "\r" in value
                or "\n" in value
                or not value.isascii()
                or any(ord(char) < 32 or ord(char) == 127 for char in value)
            ):
                raise ApiError(422, "invalid_headers", "Недопустимый HTTP-заголовок")
            clean[key] = current.get(key, "") if value == "********" else value
        site.headers_encrypted = encrypt_config(clean) if clean else None
    if site.monitor_type == "heartbeat":
        site.interval_seconds = max(60, site.heartbeat_period_seconds or 60)
        site.failure_threshold = 1
    validate_monitor(plan, site)
    if "regions" in values:
        await _sync_regions(session, site)
    if "paused" in values:
        if not site.paused:
            active = await session.scalar(
                select(func.count(Site.id)).where(
                    Site.user_id == site.user_id, Site.id != site.id, Site.paused.is_(False)
                )
            )
            if (active or 0) >= plan.max_sites:
                raise ApiError(402, "plan_limit_sites", "Превышен лимит активных сайтов")
        site.pause_reason = "user" if site.paused else None
    ids = values.get("channel_ids")
    if isinstance(ids, list):
        owned = set(
            await session.scalars(
                select(NotificationChannel.id).where(
                    NotificationChannel.user_id == site.user_id, NotificationChannel.id.in_(ids)
                )
            )
        )
        if owned != set(ids):
            raise ApiError(404, "channel_not_found", "Один из каналов не найден")
        await session.execute(delete(SiteChannel).where(SiteChannel.site_id == site.id))
        for channel_id in owned:
            session.add(SiteChannel(site_id=site.id, channel_id=channel_id))


def _recurring_window(window: dict[str, str]) -> tuple[set[int], datetime, datetime, ZoneInfo]:
    days = {int(day) for day in window["weekdays"].split(",")}
    if not days or not days <= set(range(7)):
        raise ValueError("weekdays")
    start = datetime.strptime(window["start_time"], "%H:%M").replace(tzinfo=UTC)
    end = datetime.strptime(window["end_time"], "%H:%M").replace(tzinfo=UTC)
    try:
        zone = ZoneInfo(window.get("timezone", "UTC"))
    except ZoneInfoNotFoundError as exc:
        raise ValueError("timezone") from exc
    if start == end:
        raise ValueError("empty window")
    return days, start, end, zone


def in_maintenance(site: Site, now: datetime) -> bool:
    for window in site.maintenance:
        if "weekdays" not in window:
            if (
                datetime.fromisoformat(window["start"]).astimezone(UTC)
                <= now
                < datetime.fromisoformat(window["end"]).astimezone(UTC)
            ):
                return True
            continue
        days, start, end, zone = _recurring_window(window)
        local = now.astimezone(zone)
        current = local.time().replace(tzinfo=None)
        if start < end:
            if local.weekday() in days and start.time() <= current < end.time():
                return True
        elif (local.weekday() in days and current >= start.time()) or (
            (local.weekday() - 1) % 7 in days and current < end.time()
        ):
            return True
    return False


def maintenance_spans(
    windows: list[dict[str, str]], start: datetime, end: datetime
) -> list[tuple[datetime, datetime]]:
    spans: list[tuple[datetime, datetime]] = []
    for window in windows:
        if "weekdays" not in window:
            first = datetime.fromisoformat(window["start"]).astimezone(UTC)
            last = datetime.fromisoformat(window["end"]).astimezone(UTC)
            if first < end and last > start:
                spans.append((max(first, start), min(last, end)))
            continue
        days, opens, closes, zone = _recurring_window(window)
        day = (start - timedelta(days=1)).astimezone(zone).date()
        final = end.astimezone(zone).date()
        while day <= final:
            if day.weekday() in days:
                begin = datetime.combine(day, opens.time(), zone)
                finish = datetime.combine(
                    day + timedelta(days=1 if closes <= opens else 0), closes.time(), zone
                )
                begin_utc, finish_utc = begin.astimezone(UTC), finish.astimezone(UTC)
                if begin_utc < end and finish_utc > start:
                    spans.append((max(begin_utc, start), min(finish_utc, end)))
            day += timedelta(days=1)
    return merge_spans(spans)


def merge_spans(spans: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    merged: list[tuple[datetime, datetime]] = []
    for begin, finish in sorted(spans):
        if merged and begin <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], finish))
        else:
            merged.append((begin, finish))
    return merged


def overlap_seconds(
    spans: list[tuple[datetime, datetime]], others: list[tuple[datetime, datetime]]
) -> float:
    total = 0.0
    for begin, finish in spans:
        for other_begin, other_finish in others:
            low, high = max(begin, other_begin), min(finish, other_finish)
            if high > low:
                total += (high - low).total_seconds()
    return total
