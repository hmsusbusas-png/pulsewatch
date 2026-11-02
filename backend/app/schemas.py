from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models import SiteStatus, UserTariff


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)

    @field_validator("password")
    @classmethod
    def password_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("password must not be blank")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    tariff: UserTariff
    telegram_chat_id: int | None
    created_at: datetime


class TelegramLinkResponse(BaseModel):
    link_token: str
    bot_username: str | None
    instructions: str


class SiteCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1, max_length=2048)
    interval_seconds: int = Field(default=300, ge=60, le=86400)
    is_public: bool = False
    show_url_publicly: bool = False

    @field_validator("name")
    @classmethod
    def name_is_not_blank(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("name must not be blank")
        return trimmed


class SiteUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    interval_seconds: int | None = Field(default=None, ge=60, le=86400)
    is_public: bool | None = None
    show_url_publicly: bool | None = None

    @field_validator("name")
    @classmethod
    def name_is_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("name must not be blank")
        return trimmed


class SiteResponse(BaseModel):
    id: int
    name: str
    url: str
    interval_seconds: int
    is_public: bool
    show_url_publicly: bool
    public_slug: str
    status: SiteStatus
    consecutive_failures: int
    last_checked_at: datetime | None
    ssl_expires_at: datetime | None
    ssl_checked_at: datetime | None
    created_at: datetime
    uptime_24h: float | None
    uptime_30d: float | None
    latest_response_ms: int | None
    response_series: list["SeriesPoint"]


class SeriesPoint(BaseModel):
    t: datetime
    ms: int | None


class CheckResponse(BaseModel):
    checked_at: datetime
    status_code: int | None
    response_ms: int | None
    ok: bool
    error: str | None


class IncidentResponse(BaseModel):
    id: int
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: int | None
    reason: str
    is_open: bool


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
    timestamp: datetime


def utc_now() -> datetime:
    return datetime.now(UTC)
