from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models import SiteStatus, UserTariff
from app.validation import RequestModel

MonitorType = Literal["http", "keyword", "tcp", "ssl-only", "heartbeat", "dns"]
DnsRecordType = Literal["A", "AAAA", "CNAME", "MX", "TXT", "NS"]
StatsRange = Literal["24h", "7d", "30d", "90d"]


class RegisterRequest(RequestModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)

    @field_validator("password")
    @classmethod
    def password_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("password must not be blank")
        return value


class LoginRequest(RequestModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    is_admin: bool = False
    email: str
    tariff: UserTariff
    timezone: str = "Europe/Moscow"
    telegram_chat_id: int | None
    created_at: datetime


class MeUpdate(RequestModel):
    timezone: str = Field(min_length=1, max_length=64)


class TelegramLinkResponse(BaseModel):
    link_token: str
    bot_username: str | None
    instructions: str


class Assertion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["json", "header", "body"] = "json"
    path: str = Field(default="", max_length=300)
    op: Literal["equals", "not_equals", "contains", "regex", "exists", "lt", "gt"] = "equals"
    value: str = Field(default="", max_length=500)


class MonitorOptions(RequestModel):
    monitor_type: MonitorType = "http"
    method: Literal["GET", "HEAD", "POST"] = "GET"
    headers: dict[str, str] = Field(default_factory=dict, max_length=32)
    body: str | None = Field(default=None, max_length=65536)
    expected_codes: list[int] = Field(
        default_factory=lambda: list(range(200, 400)), min_length=1, max_length=500
    )
    timeout_seconds: int = Field(default=10, ge=1, le=30)
    follow_redirects: bool = True
    keyword: str | None = Field(default=None, min_length=1, max_length=1000)
    keyword_present: bool = True
    failure_threshold: int = Field(default=2, ge=1, le=5)
    cooldown_seconds: int = Field(default=3600, ge=30, le=86400)
    maintenance: list[dict[str, str]] = Field(default_factory=list, max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=20)
    group: str | None = Field(default=None, max_length=100)
    channel_ids: list[int] = Field(default_factory=list, max_length=100)
    paused: bool = False
    tcp_send: str | None = Field(default=None, max_length=1024)
    tcp_expect: str | None = Field(default=None, max_length=200)
    assertions: list[Assertion] = Field(default_factory=list, max_length=10)
    expiry_thresholds: list[int] = Field(default_factory=lambda: [30, 14, 7], max_length=5)
    check_domain: bool = False
    slo_target: float | None = Field(default=None, ge=90, lt=100)
    dns_record_type: DnsRecordType | None = None
    dns_expected: str | None = Field(default=None, max_length=500)
    heartbeat_period_seconds: int | None = Field(default=None, ge=60, le=2592000)
    heartbeat_grace_seconds: int | None = Field(default=None, ge=0, le=86400)
    regions: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("expected_codes", mode="before")
    @classmethod
    def codes(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                first, last = (int(part) for part in value.split("-"))
            except ValueError as exc:
                raise ValueError("Ожидается диапазон 100-599") from exc
            if not 100 <= first <= last <= 599:
                raise ValueError("Ожидается диапазон 100-599")
            return list(range(first, last + 1))
        return value


class SiteCreateRequest(MonitorOptions):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(default="", max_length=2048)
    interval_seconds: int = Field(default=300, ge=30, le=86400)
    is_public: bool = False
    show_url_publicly: bool = False

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "name": "API",
                    "url": "https://api.example.com/health",
                    "interval_seconds": 60,
                    "assertions": [
                        {"source": "json", "path": "$.status", "op": "equals", "value": "ok"}
                    ],
                },
                {
                    "name": "Ночной бэкап",
                    "monitor_type": "heartbeat",
                    "heartbeat_period_seconds": 86400,
                    "heartbeat_grace_seconds": 1800,
                },
            ]
        },
    )

    @field_validator("name")
    @classmethod
    def name_is_not_blank(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("name must not be blank")
        return trimmed


class SiteUpdateRequest(RequestModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    interval_seconds: int | None = Field(default=None, ge=30, le=86400)
    is_public: bool | None = None
    show_url_publicly: bool | None = None
    monitor_type: MonitorType | None = None
    method: Literal["GET", "HEAD", "POST"] | None = None
    headers: dict[str, str] | None = Field(default=None, max_length=32)
    body: str | None = Field(default=None, max_length=65536)
    expected_codes: list[int] | None = Field(default=None, min_length=1, max_length=500)
    timeout_seconds: int | None = Field(default=None, ge=1, le=30)
    follow_redirects: bool | None = None
    keyword: str | None = Field(default=None, min_length=1, max_length=1000)
    keyword_present: bool | None = None
    failure_threshold: int | None = Field(default=None, ge=1, le=5)
    cooldown_seconds: int | None = Field(default=None, ge=60, le=86400)
    maintenance: list[dict[str, str]] | None = Field(default=None, max_length=100)
    tags: list[str] | None = Field(default=None, max_length=20)
    group: str | None = Field(default=None, max_length=100)
    channel_ids: list[int] | None = Field(default=None, max_length=100)
    paused: bool | None = None
    tcp_send: str | None = Field(default=None, max_length=1024)
    tcp_expect: str | None = Field(default=None, max_length=200)
    assertions: list[Assertion] | None = Field(default=None, max_length=10)
    expiry_thresholds: list[int] | None = Field(default=None, max_length=5)
    check_domain: bool | None = None
    slo_target: float | None = Field(default=None, ge=90, lt=100)
    dns_record_type: DnsRecordType | None = None
    dns_expected: str | None = Field(default=None, max_length=500)
    heartbeat_period_seconds: int | None = Field(default=None, ge=60, le=2592000)
    heartbeat_grace_seconds: int | None = Field(default=None, ge=0, le=86400)
    regions: list[str] | None = Field(default=None, max_length=10)

    @field_validator("expected_codes", mode="before")
    @classmethod
    def codes(cls, value: object) -> object:
        return MonitorOptions.codes(value)

    @field_validator("name")
    @classmethod
    def name_is_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("name must not be blank")
        return trimmed


class RegionState(BaseModel):
    region: str
    name: str
    status: str
    consecutive_failures: int
    last_checked_at: datetime | None
    last_response_ms: int | None
    last_error: str | None


class SiteResponse(MonitorOptions):
    model_config = ConfigDict(extra="ignore")
    assertions: list[Assertion] = Field(default_factory=list)
    maintenance_active: bool = False
    pause_reason: str | None = None
    pause_message: str | None = None
    id: int
    name: str
    url: str
    interval_seconds: int
    is_public: bool
    show_url_publicly: bool
    public_slug: str
    status: SiteStatus
    degraded: bool = False
    open_incident_id: int | None = None
    consecutive_failures: int
    last_checked_at: datetime | None
    next_check_at: datetime | None = None
    ssl_expires_at: datetime | None
    ssl_checked_at: datetime | None
    ssl_details: dict[str, object] | None = None
    domain_expires_at: datetime | None = None
    dns_values: list[str] | None = None
    heartbeat_prefix: str | None = None
    heartbeat_token: str | None = Field(
        default=None, description="Полный токен пинга; возвращается только при создании и ротации"
    )
    last_ping_at: datetime | None = None
    baseline_ms: int | None = None
    region_states: list[RegionState] = Field(default_factory=list)
    created_at: datetime
    uptime_24h: float | None
    uptime_30d: float | None
    uptime_7d: float | None = None
    latest_response_ms: int | None
    response_series: list["SeriesPoint"]


class SeriesPoint(BaseModel):
    t: datetime
    ms: int | None


class CheckResponse(BaseModel):
    id: int
    checked_at: datetime
    status_code: int | None
    response_ms: int | None
    ok: bool
    error: str | None
    dns_ms: int | None = None
    connect_ms: int | None = None
    tls_ms: int | None = None
    ttfb_ms: int | None = None
    transfer_ms: int | None = None
    region: str | None = None


class IncidentResponse(BaseModel):
    id: int
    site_id: int | None = None
    kind: str = "down"
    started_at: datetime
    confirmed_at: datetime | None = None
    acknowledged_at: datetime | None = None
    ended_at: datetime | None
    duration_seconds: int | None
    reason: str
    is_open: bool


class IncidentNoteResponse(BaseModel):
    id: int
    status: str
    text: str
    created_at: datetime


class DiagnosticResponse(BaseModel):
    id: int
    created_at: datetime
    region: str | None
    status_code: int | None
    error: str | None
    ip: str | None
    resolved: list[str]
    headers: dict[str, str]
    body: str | None
    timings: dict[str, int | None]


class TimelineEntry(BaseModel):
    at: datetime
    kind: Literal["started", "confirmed", "acknowledged", "note", "escalated", "resolved"]
    status: str | None = None
    text: str | None = None
    duration_seconds: int | None = Field(
        default=None, description="Сколько длился этап до следующего события"
    )


class IncidentDetail(IncidentResponse):
    site_name: str
    site_url: str
    postmortem: str | None
    notes: list[IncidentNoteResponse]
    diagnostics: list[DiagnosticResponse]
    timeline: list[TimelineEntry]


class DashboardSiteSummary(BaseModel):
    id: int
    name: str
    url: str
    status: SiteStatus
    response_ms: int | None
    uptime_24h: float | None
    last_checked_at: datetime | None
    sparkline: list[int | None]


class DashboardSummary(BaseModel):
    total_sites: int
    up_count: int
    down_count: int
    unknown_count: int
    degraded_count: int = 0
    paused_count: int = 0
    average_response_ms: int | None
    open_incidents: int
    sites: list[DashboardSiteSummary]


class PublicIncidentResponse(BaseModel):
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: int | None
    reason: str


class PublicStatusResponse(BaseModel):
    name: str
    url: str | None
    status: SiteStatus
    uptime_30d: float | None
    last_checked_at: datetime | None
    incidents: list[PublicIncidentResponse]
    updated_at: datetime


class HealthResponse(BaseModel):
    status: str
    database: str
    scheduler: str
    environment: str = "production"
    timestamp: datetime


class DayUptime(BaseModel):
    day: date
    uptime: float | None
    checks: int
    incidents: int
    downtime_seconds: int


class SiteDays(BaseModel):
    site_id: int
    days: list[DayUptime]


class StatsPoint(BaseModel):
    t: datetime
    avg_ms: int | None
    p95_ms: int | None
    min_ms: int | None
    max_ms: int | None
    checks: int
    failures: int


class Span(BaseModel):
    start: datetime
    end: datetime | None
    kind: str
    incident_id: int | None = None


class TimingBreakdown(BaseModel):
    dns_ms: int | None = None
    connect_ms: int | None = None
    tls_ms: int | None = None
    ttfb_ms: int | None = None
    transfer_ms: int | None = None
    samples: int = 0


class SiteStats(BaseModel):
    range: StatsRange
    start: datetime
    end: datetime
    resolution_seconds: int
    source: Literal["checks", "daily_rollups"]
    uptime: float | None
    checks: int
    failures: int
    avg_ms: int | None
    p50_ms: int | None
    p95_ms: int | None
    p99_ms: int | None
    incidents: int
    downtime_seconds: int
    baseline_ms: int | None
    timings: TimingBreakdown
    points: list[StatsPoint]
    incident_spans: list[Span]
    maintenance_spans: list[Span]


class SloReport(BaseModel):
    site_id: int
    target: float | None
    window_days: int
    uptime: float | None
    budget_seconds: int
    spent_seconds: int
    remaining_seconds: int
    remaining_percent: float | None
    burn_rate_1h: float | None
    burn_rate_24h: float | None
    exhausted: bool
    exhausted_at: datetime | None = Field(
        default=None, description="Прогноз исчерпания при скорости сжигания за 24 ч"
    )
    maintenance_seconds: int
    allowed_downtime_month_seconds: int


class SlaRow(BaseModel):
    site_id: int
    name: str
    target: float | None
    uptime: float | None
    downtime_seconds: int
    maintenance_seconds: int
    incidents: int
    met: bool | None


class SlaReport(BaseModel):
    start: datetime
    end: datetime
    sites: list[SlaRow]


class SessionResponse(BaseModel):
    id: int
    browser: str | None
    os: str | None
    ip: str | None
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime
    current: bool


class AuditChange(BaseModel):
    field: str
    old: object = None
    new: object = None


class AuditEntry(BaseModel):
    id: int
    action: str
    resource_type: str | None
    resource_id: int | None
    changes: list[AuditChange]
    created_at: datetime
