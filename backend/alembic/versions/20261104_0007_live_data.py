"""Sessions metadata, audit diffs, latency histograms, incident kinds, stream tokens."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0007_live_data"
down_revision = "0006_email_verification"
branch_labels = None
depends_on = None


ROLLUP_V2 = """
CREATE OR REPLACE FUNCTION pulsewatch_rollup() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    bucket int;
    initial int[] := array_fill(0, ARRAY[44]);
BEGIN
    IF NEW.ok AND NEW.response_ms IS NOT NULL THEN
        bucket := width_bucket(NEW.response_ms, ARRAY[0,10,20,30,40,50,60,70,80,90,100,120,140,160,180,200,225,250,275,300,350,400,450,500,600,700,800,900,1000,1250,1500,1750,2000,2500,3000,3500,4000,5000,6000,8000,10000,15000,20000,30000]::int[]);
        initial[bucket] := 1;
    END IF;
    INSERT INTO daily_uptime (site_id, day, total, successful, response_ms_sum, histogram)
    VALUES (NEW.site_id, (NEW.checked_at AT TIME ZONE 'UTC')::date, 1,
            CASE WHEN NEW.ok THEN 1 ELSE 0 END,
            CASE WHEN NEW.ok THEN coalesce(NEW.response_ms, 0) ELSE 0 END,
            initial)
    ON CONFLICT (site_id, day) DO UPDATE
    SET total = daily_uptime.total + 1,
        successful = daily_uptime.successful + EXCLUDED.successful,
        response_ms_sum = daily_uptime.response_ms_sum + EXCLUDED.response_ms_sum,
        histogram = ARRAY(
            SELECT coalesce(a, 0) + coalesce(b, 0)
            FROM unnest(daily_uptime.histogram, EXCLUDED.histogram) WITH ORDINALITY AS t(a, b, i)
            ORDER BY i
        );
    RETURN NEW;
END $$
"""

ROLLUP_V1 = """
CREATE OR REPLACE FUNCTION pulsewatch_rollup() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO daily_uptime (site_id, day, total, successful, response_ms_sum)
    VALUES (NEW.site_id, (NEW.checked_at AT TIME ZONE 'UTC')::date, 1,
            CASE WHEN NEW.ok THEN 1 ELSE 0 END,
            CASE WHEN NEW.ok THEN coalesce(NEW.response_ms, 0) ELSE 0 END)
    ON CONFLICT (site_id, day) DO UPDATE
    SET total = daily_uptime.total + 1,
        successful = daily_uptime.successful + EXCLUDED.successful,
        response_ms_sum = daily_uptime.response_ms_sum + EXCLUDED.response_ms_sum;
    RETURN NEW;
END $$
"""

HISTOGRAM_BACKFILL = """
UPDATE daily_uptime d SET histogram = h.histogram FROM (
    SELECT k.site_id, k.day, array_agg(coalesce(b.n, 0) ORDER BY g.i)::int[] AS histogram
    FROM (SELECT DISTINCT site_id, (checked_at AT TIME ZONE 'UTC')::date AS day FROM checks) k
    CROSS JOIN generate_series(1, 44) AS g(i)
    LEFT JOIN (
        SELECT site_id, (checked_at AT TIME ZONE 'UTC')::date AS day,
               width_bucket(response_ms, ARRAY[0,10,20,30,40,50,60,70,80,90,100,120,140,160,180,200,225,250,275,300,350,400,450,500,600,700,800,900,1000,1250,1500,1750,2000,2500,3000,3500,4000,5000,6000,8000,10000,15000,20000,30000]::int[]) AS bucket, count(*) AS n
        FROM checks WHERE ok AND response_ms IS NOT NULL GROUP BY 1, 2, 3
    ) b ON b.site_id = k.site_id AND b.day = k.day AND b.bucket = g.i
    GROUP BY k.site_id, k.day
) h WHERE d.site_id = h.site_id AND d.day = h.day
"""


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("timezone", sa.String(64), server_default="Europe/Moscow", nullable=False),
    )
    for name, column in (
        ("family", sa.String(32)),
        ("last_used_at", sa.DateTime(timezone=True)),
        ("user_agent", sa.String(300)),
        ("ip_hash", sa.String(64)),
        ("ip_mask", sa.String(64)),
    ):
        op.add_column("refresh_tokens", sa.Column(name, column, nullable=True))
    op.add_column(
        "refresh_tokens",
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )
    op.execute("UPDATE refresh_tokens SET family = md5(token_hash || id::text)::varchar(32)")
    op.create_index(
        "ix_refresh_tokens_user_active", "refresh_tokens", ["user_id", "revoked", "id"]
    )

    op.add_column("audit_log", sa.Column("resource_type", sa.String(30), nullable=True))
    op.add_column("audit_log", sa.Column("changes", sa.JSON(), nullable=True))
    op.create_index("ix_audit_user_id_desc", "audit_log", ["user_id", sa.text("id DESC")])

    op.add_column(
        "status_pages",
        sa.Column("show_urls", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("status_pages", sa.Column("logo_url", sa.String(2048), nullable=True))
    op.add_column(
        "status_pages",
        sa.Column("accent", sa.String(16), server_default="emerald", nullable=False),
    )
    op.add_column("status_pages", sa.Column("custom_domain", sa.String(253), nullable=True))
    op.add_column(
        "status_pages",
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )
    op.create_check_constraint(
        "ck_status_pages_accent",
        "status_pages",
        "accent IN ('emerald','sky','violet','amber','rose','slate')",
    )
    op.create_unique_constraint("uq_status_pages_domain", "status_pages", ["custom_domain"])
    op.create_table(
        "status_announcements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "page_id",
            sa.Integer(),
            sa.ForeignKey("status_pages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(150), nullable=False),
        sa.Column("body", sa.String(2000), nullable=False),
        sa.Column("pinned", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )
    op.create_index(
        "ix_announcements_page", "status_announcements", ["page_id", "pinned", "created_at"]
    )

    op.add_column(
        "subscriptions",
        sa.Column(
            "scheduled_plan_id",
            sa.Integer(),
            sa.ForeignKey("plans.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    op.create_table(
        "stream_tokens",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_stream_tokens_user_id", "stream_tokens", ["user_id"])
    op.create_index("ix_stream_tokens_expires_at", "stream_tokens", ["expires_at"])

    op.add_column(
        "incidents", sa.Column("kind", sa.String(16), server_default="down", nullable=False)
    )
    op.add_column("incidents", sa.Column("confirmed_at", sa.DateTime(timezone=True)))
    op.add_column("incidents", sa.Column("acknowledged_at", sa.DateTime(timezone=True)))
    op.add_column("incidents", sa.Column("postmortem", sa.Text()))
    op.execute("UPDATE incidents SET confirmed_at = started_at")
    op.create_check_constraint("ck_incidents_kind", "incidents", "kind IN ('down','degraded')")
    op.drop_index("uq_incidents_open_per_site", table_name="incidents")
    op.create_index(
        "uq_incidents_open_per_site_kind",
        "incidents",
        ["site_id", "kind"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.create_index("ix_incident_notes_incident_created", "incident_notes", ["incident_id", "id"])

    for name in ("dns_ms", "connect_ms", "tls_ms", "ttfb_ms", "transfer_ms"):
        op.add_column("checks", sa.Column(name, sa.Integer(), nullable=True))
    op.add_column("checks", sa.Column("region", sa.String(16), nullable=True))

    op.add_column(
        "daily_uptime",
        sa.Column(
            "histogram",
            postgresql.ARRAY(sa.Integer()),
            server_default=sa.text("array_fill(0, ARRAY[44])"),
            nullable=False,
        ),
    )
    op.execute(HISTOGRAM_BACKFILL)
    op.execute(ROLLUP_V2)


def downgrade() -> None:
    op.execute(ROLLUP_V1)
    op.drop_column("daily_uptime", "histogram")
    op.drop_column("checks", "region")
    for name in ("transfer_ms", "ttfb_ms", "tls_ms", "connect_ms", "dns_ms"):
        op.drop_column("checks", name)
    op.drop_index("ix_incident_notes_incident_created", table_name="incident_notes")
    op.drop_index("uq_incidents_open_per_site_kind", table_name="incidents")
    op.execute(
        "UPDATE incidents SET ended_at = coalesce(ended_at, now()) "
        "WHERE kind = 'degraded' AND ended_at IS NULL"
    )
    op.execute("DELETE FROM incidents WHERE kind = 'degraded'")
    op.create_index(
        "uq_incidents_open_per_site",
        "incidents",
        ["site_id"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.drop_constraint("ck_incidents_kind", "incidents", type_="check")
    for name in ("postmortem", "acknowledged_at", "confirmed_at", "kind"):
        op.drop_column("incidents", name)
    op.drop_index("ix_stream_tokens_expires_at", table_name="stream_tokens")
    op.drop_index("ix_stream_tokens_user_id", table_name="stream_tokens")
    op.drop_table("stream_tokens")
    op.drop_column("subscriptions", "scheduled_plan_id")
    op.drop_index("ix_announcements_page", table_name="status_announcements")
    op.drop_table("status_announcements")
    op.drop_constraint("uq_status_pages_domain", "status_pages", type_="unique")
    op.drop_constraint("ck_status_pages_accent", "status_pages", type_="check")
    for name in ("created_at", "custom_domain", "accent", "logo_url", "show_urls"):
        op.drop_column("status_pages", name)
    op.drop_index("ix_audit_user_id_desc", table_name="audit_log")
    op.drop_column("audit_log", "changes")
    op.drop_column("audit_log", "resource_type")
    op.drop_index("ix_refresh_tokens_user_active", table_name="refresh_tokens")
    for name in ("started_at", "ip_mask", "ip_hash", "user_agent", "last_used_at", "family"):
        op.drop_column("refresh_tokens", name)
    op.drop_column("users", "timezone")
