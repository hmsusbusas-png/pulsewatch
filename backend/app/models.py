import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    __abstract__ = True


class UserTariff(enum.StrEnum):
    free = "free"
    pro = "pro"


class SiteStatus(enum.StrEnum):
    up = "up"
    down = "down"
    unknown = "unknown"


MONITOR_TYPES = ("http", "keyword", "tcp", "ssl-only", "heartbeat", "dns")
CHANNEL_TYPES = ("telegram", "email", "webhook", "slack", "discord", "teams", "ntfy")
LATENCY_BUCKETS: tuple[int, ...] = (
    *range(0, 101, 10),
    *range(120, 201, 20),
    *range(225, 301, 25),
    *range(350, 501, 50),
    *range(600, 1001, 100),
    *range(1250, 2001, 250),
    *range(2500, 4001, 500),
    5000,
    6000,
    8000,
    10000,
    15000,
    20000,
    30000,
)


def enum_column(enum_type: type[enum.Enum], name: str) -> Enum:
    return Enum(
        enum_type, name=name, values_callable=lambda members: [item.value for item in members]
    )


def quoted(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    auth_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tariff: Mapped[UserTariff] = mapped_column(
        enum_column(UserTariff, "user_tariff"),
        nullable=False,
        default=UserTariff.free,
        server_default=UserTariff.free.value,
    )
    timezone: Mapped[str] = mapped_column(
        String(64), default="Europe/Moscow", server_default="Europe/Moscow"
    )
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, unique=True)
    telegram_link_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    telegram_link_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    telegram_name: Mapped[str | None] = mapped_column(String(100))
    telegram_mute_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trial_used: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sql_text("false")
    )
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sql_text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )

    sites: Mapped[list["Site"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Site(Base):
    __tablename__ = "sites"
    __table_args__ = (
        CheckConstraint("interval_seconds >= 30", name="ck_sites_interval_min"),
        CheckConstraint(f"monitor_type IN ({quoted(MONITOR_TYPES)})", name="ck_sites_monitor_type"),
        CheckConstraint("method IN ('GET','HEAD','POST')", name="ck_sites_method"),
        CheckConstraint("timeout_seconds BETWEEN 1 AND 30", name="ck_sites_timeout"),
        CheckConstraint("failure_threshold BETWEEN 1 AND 5", name="ck_sites_threshold"),
        Index("ix_sites_last_checked_at", "last_checked_at"),
        UniqueConstraint("public_slug", name="uq_sites_public_slug"),
        UniqueConstraint("heartbeat_token_hash", name="uq_sites_heartbeat_token"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    paused: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sql_text("false"))
    pause_reason: Mapped[str | None] = mapped_column(String(200))
    next_check_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sql_text("now()"), index=True
    )
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    monitor_type: Mapped[str] = mapped_column(String(16), default="http", server_default="http")
    method: Mapped[str] = mapped_column(String(8), default="GET", server_default="GET")
    headers_encrypted: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    expected_codes: Mapped[list[int]] = mapped_column(
        JSON, default=lambda: list(range(200, 400)), server_default="[200]"
    )
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=10, server_default="10")
    follow_redirects: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sql_text("true")
    )
    keyword: Mapped[str | None] = mapped_column(String(1000))
    keyword_present: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sql_text("true")
    )
    failure_threshold: Mapped[int] = mapped_column(Integer, default=2, server_default="2")
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=3600, server_default="3600")
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    maintenance: Mapped[list[dict[str, str]]] = mapped_column(
        JSON, default=list, server_default="[]"
    )
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    group: Mapped[str | None] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    interval_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=300, server_default=sql_text("300")
    )
    is_public: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sql_text("false")
    )
    show_url_publicly: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sql_text("false")
    )
    public_slug: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, default=uuid.uuid4
    )
    status: Mapped[SiteStatus] = mapped_column(
        enum_column(SiteStatus, "site_status"),
        nullable=False,
        default=SiteStatus.unknown,
        server_default=SiteStatus.unknown.value,
    )
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sql_text("0")
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ssl_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ssl_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ssl_details: Mapped[dict[str, object] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    heartbeat_token_hash: Mapped[str | None] = mapped_column(String(64))
    heartbeat_prefix: Mapped[str | None] = mapped_column(String(16))
    heartbeat_period_seconds: Mapped[int | None] = mapped_column(Integer)
    heartbeat_grace_seconds: Mapped[int | None] = mapped_column(Integer)
    last_ping_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dns_record_type: Mapped[str | None] = mapped_column(String(8))
    dns_expected: Mapped[str | None] = mapped_column(String(500))
    dns_values: Mapped[list[str] | None] = mapped_column(JSON)
    tcp_send: Mapped[str | None] = mapped_column(String(1024))
    tcp_expect: Mapped[str | None] = mapped_column(String(200))
    assertions: Mapped[list[dict[str, str]]] = mapped_column(
        JSON, default=list, server_default="[]"
    )
    expiry_thresholds: Mapped[list[int]] = mapped_column(
        JSON, default=lambda: [30, 14, 7], server_default="[30, 14, 7]"
    )
    expiry_notified: Mapped[dict[str, int]] = mapped_column(JSON, default=dict, server_default="{}")
    check_domain: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sql_text("false")
    )
    domain_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    domain_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    regions: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    slo_target: Mapped[float | None] = mapped_column(Numeric(6, 3, asdecimal=False))
    baseline_ms: Mapped[int | None] = mapped_column(Integer)
    baseline_mad_ms: Mapped[int | None] = mapped_column(Integer)
    baseline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    slow_streak: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    fast_streak: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_response_encrypted: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User] = relationship(back_populates="sites")
    checks: Mapped[list["Check"]] = relationship(
        back_populates="site", cascade="all, delete-orphan"
    )
    incidents: Mapped[list["Incident"]] = relationship(
        back_populates="site", cascade="all, delete-orphan"
    )


class Check(Base):
    __tablename__ = "checks"
    __table_args__ = (
        Index("ix_checks_site_checked_at", "site_id", sql_text("checked_at DESC")),
        Index("ix_checks_checked_at", "checked_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True, primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    dns_ms: Mapped[int | None] = mapped_column(Integer)
    connect_ms: Mapped[int | None] = mapped_column(Integer)
    tls_ms: Mapped[int | None] = mapped_column(Integer)
    ttfb_ms: Mapped[int | None] = mapped_column(Integer)
    transfer_ms: Mapped[int | None] = mapped_column(Integer)
    region: Mapped[str | None] = mapped_column(String(16))

    site: Mapped[Site] = relationship(back_populates="checks")


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        CheckConstraint("kind IN ('down','degraded')", name="ck_incidents_kind"),
        Index("ix_incidents_site_started", "site_id", "started_at"),
        Index(
            "uq_incidents_open_per_site_kind",
            "site_id",
            "kind",
            unique=True,
            postgresql_where=sql_text("ended_at IS NULL"),
        ),
        Index("ix_incidents_open", "started_at", postgresql_where=sql_text("ended_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(
        ForeignKey("sites.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), default="down", server_default="down")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    postmortem: Mapped[str | None] = mapped_column(Text)

    site: Mapped[Site] = relationship(back_populates="incidents")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
        Index("ix_refresh_tokens_user_active", "user_id", "revoked", "id"),
    )

    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sql_text("false")
    )
    family: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sql_text("now()")
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    ip_mask: Mapped[str | None] = mapped_column(String(64))

    user: Mapped[User] = relationship(back_populates="refresh_tokens")


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    price_kop: Mapped[int] = mapped_column(Integer)
    period_days: Mapped[int] = mapped_column(Integer)
    max_sites: Mapped[int] = mapped_column(Integer)
    min_interval_sec: Mapped[int] = mapped_column(Integer)
    retention_days: Mapped[int] = mapped_column(Integer)
    max_channels: Mapped[int | None] = mapped_column(Integer)
    max_regions: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    features: Mapped[dict[str, bool]] = mapped_column(JSON)


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('trialing','active','past_due','canceled','expired')",
            name="ck_subscription_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id", ondelete="RESTRICT"))
    scheduled_plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("plans.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(16), index=True)
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sql_text("false")
    )
    trial_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (CheckConstraint("amount_kop >= 0", name="ck_payment_amount"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id", ondelete="CASCADE"))
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id", ondelete="RESTRICT"))
    provider: Mapped[str] = mapped_column(String(20))
    provider_payment_id: Mapped[str] = mapped_column(String(100), unique=True)
    amount_kop: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="RUB", server_default="RUB")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sql_text("now()")
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)


class NotificationChannel(Base):
    __tablename__ = "notification_channels"
    __table_args__ = (
        CheckConstraint(f"type IN ({quoted(CHANNEL_TYPES)})", name="ck_channels_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(100))
    config: Mapped[str] = mapped_column(Text)
    pending_config: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sql_text("true"))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sql_text("now()")
    )


class SiteChannel(Base):
    __tablename__ = "site_channels"

    site_id: Mapped[int] = mapped_column(
        ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True
    )
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("notification_channels.id", ondelete="CASCADE"), primary_key=True
    )


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (Index("ix_deliveries_due", "status", "next_attempt_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("notification_channels.id", ondelete="CASCADE"), index=True
    )
    payload: Mapped[dict[str, object]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    response_code: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sql_text("now()")
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sql_text("now()")
    )


class DailyUptime(Base):
    __tablename__ = "daily_uptime"

    site_id: Mapped[int] = mapped_column(
        ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    total: Mapped[int] = mapped_column(Integer)
    successful: Mapped[int] = mapped_column(Integer)
    response_ms_sum: Mapped[int] = mapped_column(BigInteger, default=0)
    histogram: Mapped[list[int]] = mapped_column(
        ARRAY(Integer),
        default=lambda: [0] * len(LATENCY_BUCKETS),
        server_default=sql_text(f"array_fill(0, ARRAY[{len(LATENCY_BUCKETS)}])"),
    )


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(20))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scope: Mapped[str] = mapped_column(String(8))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sql_text("now()")
    )


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_user_created", "user_id", "created_at"),
        Index("ix_audit_user_id_desc", "user_id", sql_text("id DESC")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(80))
    resource_type: Mapped[str | None] = mapped_column(String(30))
    resource_id: Mapped[int | None] = mapped_column(Integer)
    changes: Mapped[list[dict[str, object]] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sql_text("now()")
    )


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AccountToken(Base):
    __tablename__ = "account_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(20))
    value: Mapped[str | None] = mapped_column(String(320))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RateBucket(Base):
    __tablename__ = "rate_buckets"
    __table_args__ = (Index("ix_rate_bucket_started", "started_at"),)

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(Integer)


PAGE_ACCENTS = ("emerald", "sky", "violet", "amber", "rose", "slate")


class StatusPage(Base):
    __tablename__ = "status_pages"
    __table_args__ = (
        CheckConstraint(f"accent IN ({quoted(PAGE_ACCENTS)})", name="ck_status_pages_accent"),
        UniqueConstraint("custom_domain", name="uq_status_pages_domain"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    title: Mapped[str] = mapped_column(String(150))
    description: Mapped[str] = mapped_column(String(2000), default="")
    show_urls: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sql_text("false")
    )
    logo_url: Mapped[str | None] = mapped_column(String(2048))
    accent: Mapped[str] = mapped_column(String(16), default="emerald", server_default="emerald")
    custom_domain: Mapped[str | None] = mapped_column(String(253))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sql_text("now()")
    )


class StatusAnnouncement(Base):
    __tablename__ = "status_announcements"
    __table_args__ = (Index("ix_announcements_page", "page_id", "pinned", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("status_pages.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(150))
    body: Mapped[str] = mapped_column(String(2000))
    pinned: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sql_text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sql_text("now()")
    )


class StatusPageSite(Base):
    __tablename__ = "status_page_sites"

    page_id: Mapped[int] = mapped_column(
        ForeignKey("status_pages.id", ondelete="CASCADE"), primary_key=True
    )
    site_id: Mapped[int] = mapped_column(
        ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True
    )


class StatusSubscriber(Base):
    __tablename__ = "status_subscribers"
    __table_args__ = (UniqueConstraint("page_id", "email", name="uq_status_subscriber"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    page_id: Mapped[int] = mapped_column(
        ForeignKey("status_pages.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(320))
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MailJob(Base):
    __tablename__ = "mail_jobs"
    __table_args__ = (Index("ix_mail_jobs_due", "status", "next_attempt_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    recipient: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sql_text("now()")
    )
    dedupe_key: Mapped[str | None] = mapped_column(String(150), unique=True)


class IncidentNote(Base):
    __tablename__ = "incident_notes"
    __table_args__ = (Index("ix_incident_notes_incident_created", "incident_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sql_text("now()")
    )


class StreamToken(Base):
    __tablename__ = "stream_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Region(Base):
    __tablename__ = "regions"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    is_primary: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sql_text("false")
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sql_text("now()")
    )


class SiteRegionState(Base):
    __tablename__ = "site_region_state"
    __table_args__ = (Index("ix_region_state_due", "region", "next_check_at"),)

    site_id: Mapped[int] = mapped_column(
        ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True
    )
    region: Mapped[str] = mapped_column(String(16), primary_key=True)
    status: Mapped[str] = mapped_column(String(8), default="unknown", server_default="unknown")
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_response_ms: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(String(500))
    next_check_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sql_text("now()")
    )
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CheckDiagnostic(Base):
    __tablename__ = "check_diagnostics"
    __table_args__ = (
        Index("ix_diagnostics_site_created", "site_id", sql_text("created_at DESC")),
        Index("ix_diagnostics_incident", "incident_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sql_text("now()")
    )
    region: Mapped[str | None] = mapped_column(String(16))
    status_code: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(String(500))
    ip: Mapped[str | None] = mapped_column(String(64))
    resolved: Mapped[list[str]] = mapped_column(JSON, default=list)
    headers: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    body_encrypted: Mapped[str | None] = mapped_column(Text)
    timings: Mapped[dict[str, int | None]] = mapped_column(JSON, default=dict)


class EscalationRule(Base):
    __tablename__ = "escalation_rules"
    __table_args__ = (
        CheckConstraint("after_minutes BETWEEN 1 AND 1440", name="ck_escalation_after"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    site_id: Mapped[int | None] = mapped_column(
        ForeignKey("sites.id", ondelete="CASCADE"), index=True
    )
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("notification_channels.id", ondelete="CASCADE")
    )
    after_minutes: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sql_text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sql_text("now()")
    )


class IncidentEscalation(Base):
    __tablename__ = "incident_escalations"

    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), primary_key=True
    )
    rule_id: Mapped[int] = mapped_column(
        ForeignKey("escalation_rules.id", ondelete="CASCADE"), primary_key=True
    )
    notified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sql_text("now()")
    )


TEMPLATE_EVENTS = ("down", "up", "degraded", "expiring")


class NotificationTemplate(Base):
    __tablename__ = "notification_templates"
    __table_args__ = (
        UniqueConstraint("user_id", "event", name="uq_templates_user_event"),
        CheckConstraint(f"event IN ({quoted(TEMPLATE_EVENTS)})", name="ck_templates_event"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    event: Mapped[str] = mapped_column(String(16))
    body: Mapped[str] = mapped_column(String(2000))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sql_text("now()")
    )


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("scope", "key", name="uq_idempotency_scope_key"),
        Index("ix_idempotency_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    scope: Mapped[str] = mapped_column(String(80))
    key: Mapped[str] = mapped_column(String(255))
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(300))
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int | None] = mapped_column(Integer)
    response_encrypted: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sql_text("now()")
    )
