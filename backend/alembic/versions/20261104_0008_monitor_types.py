"""Heartbeat and DNS monitors, regions, diagnostics, escalations, templates, plan flags."""

import json

import sqlalchemy as sa

from alembic import op

revision = "0008_monitor_types"
down_revision = "0007_live_data"
branch_labels = None
depends_on = None

FEATURES = {
    "free": {"heartbeat": True},
    "pro": {
        "custom_slug": True,
        "keyword": True,
        "headers": True,
        "post": True,
        "heartbeat": True,
        "dns": True,
        "assertions": True,
        "domain_expiry": True,
        "slo": True,
        "escalations": True,
    },
    "business": {
        "custom_slug": True,
        "keyword": True,
        "headers": True,
        "post": True,
        "maintenance": True,
        "api_keys": True,
        "csv": True,
        "heartbeat": True,
        "dns": True,
        "assertions": True,
        "domain_expiry": True,
        "slo": True,
        "escalations": True,
        "regions": True,
        "templates": True,
    },
}
REGIONS = {"free": 1, "pro": 2, "business": 3}
OLD_FEATURES = {
    "free": {},
    "pro": {"custom_slug": True, "keyword": True, "headers": True, "post": True},
    "business": {
        "custom_slug": True,
        "keyword": True,
        "headers": True,
        "post": True,
        "maintenance": True,
        "api_keys": True,
        "csv": True,
    },
}


def upgrade() -> None:
    op.drop_constraint("ck_sites_monitor_type", "sites", type_="check")
    op.create_check_constraint(
        "ck_sites_monitor_type",
        "sites",
        "monitor_type IN ('http','keyword','tcp','ssl-only','heartbeat','dns')",
    )
    for name, column in (
        ("ssl_details", sa.JSON()),
        ("heartbeat_token_hash", sa.String(64)),
        ("heartbeat_prefix", sa.String(16)),
        ("heartbeat_period_seconds", sa.Integer()),
        ("heartbeat_grace_seconds", sa.Integer()),
        ("last_ping_at", sa.DateTime(timezone=True)),
        ("dns_record_type", sa.String(8)),
        ("dns_expected", sa.String(500)),
        ("dns_values", sa.JSON()),
        ("tcp_send", sa.String(1024)),
        ("tcp_expect", sa.String(200)),
        ("domain_expires_at", sa.DateTime(timezone=True)),
        ("domain_checked_at", sa.DateTime(timezone=True)),
        ("slo_target", sa.Numeric(6, 3)),
        ("baseline_ms", sa.Integer()),
        ("baseline_mad_ms", sa.Integer()),
        ("baseline_at", sa.DateTime(timezone=True)),
        ("last_response_encrypted", sa.Text()),
    ):
        op.add_column("sites", sa.Column(name, column, nullable=True))
    for name, default in (
        ("assertions", "[]"),
        ("expiry_thresholds", "[30, 14, 7]"),
        ("expiry_notified", "{}"),
        ("regions", "[]"),
    ):
        op.add_column(
            "sites", sa.Column(name, sa.JSON(), server_default=default, nullable=False)
        )
    op.add_column(
        "sites",
        sa.Column("check_domain", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    for name in ("slow_streak", "fast_streak"):
        op.add_column(
            "sites", sa.Column(name, sa.Integer(), server_default="0", nullable=False)
        )
    op.create_unique_constraint("uq_sites_heartbeat_token", "sites", ["heartbeat_token_hash"])

    op.create_table(
        "regions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(16), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=True, unique=True),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )
    op.create_table(
        "site_region_state",
        sa.Column(
            "site_id",
            sa.Integer(),
            sa.ForeignKey("sites.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("region", sa.String(16), primary_key=True),
        sa.Column("status", sa.String(8), server_default="unknown", nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("last_response_ms", sa.Integer()),
        sa.Column("last_error", sa.String(500)),
        sa.Column(
            "next_check_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("lease_token", sa.String(64)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_region_state_due", "site_region_state", ["region", "next_check_at"])

    op.create_table(
        "check_diagnostics",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "site_id", sa.Integer(), sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "incident_id",
            sa.Integer(),
            sa.ForeignKey("incidents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("region", sa.String(16)),
        sa.Column("status_code", sa.Integer()),
        sa.Column("error", sa.String(500)),
        sa.Column("ip", sa.String(64)),
        sa.Column("resolved", sa.JSON(), nullable=False),
        sa.Column("headers", sa.JSON(), nullable=False),
        sa.Column("body_encrypted", sa.Text()),
        sa.Column("timings", sa.JSON(), nullable=False),
    )
    op.create_index(
        "ix_diagnostics_site_created", "check_diagnostics", ["site_id", sa.text("created_at DESC")]
    )
    op.create_index("ix_diagnostics_incident", "check_diagnostics", ["incident_id"])

    op.create_table(
        "escalation_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "site_id", sa.Integer(), sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column(
            "channel_id",
            sa.Integer(),
            sa.ForeignKey("notification_channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("after_minutes", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint("after_minutes BETWEEN 1 AND 1440", name="ck_escalation_after"),
    )
    op.create_index("ix_escalation_rules_user_id", "escalation_rules", ["user_id"])
    op.create_index("ix_escalation_rules_site_id", "escalation_rules", ["site_id"])
    op.create_table(
        "incident_escalations",
        sa.Column(
            "incident_id",
            sa.Integer(),
            sa.ForeignKey("incidents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "rule_id",
            sa.Integer(),
            sa.ForeignKey("escalation_rules.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "notified_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_incidents_open", "incidents", ["started_at"], postgresql_where=sa.text("ended_at IS NULL")
    )

    op.create_table(
        "notification_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("event", sa.String(16), nullable=False),
        sa.Column("body", sa.String(2000), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.UniqueConstraint("user_id", "event", name="uq_templates_user_event"),
        sa.CheckConstraint(
            "event IN ('down','up','degraded','expiring')", name="ck_templates_event"
        ),
    )

    op.drop_constraint("ck_channels_type", "notification_channels", type_="check")
    op.create_check_constraint(
        "ck_channels_type",
        "notification_channels",
        "type IN ('telegram','email','webhook','slack','discord','teams','ntfy')",
    )
    op.add_column("notification_channels", sa.Column("pending_config", sa.Text(), nullable=True))

    op.add_column(
        "plans", sa.Column("max_regions", sa.Integer(), server_default="1", nullable=False)
    )
    plans = sa.table(
        "plans",
        sa.column("code", sa.String),
        sa.column("features", sa.JSON),
        sa.column("max_regions", sa.Integer),
    )
    for code, features in FEATURES.items():
        op.execute(
            plans.update()
            .where(plans.c.code == code)
            .values(features=json.loads(json.dumps(features)), max_regions=REGIONS[code])
        )


def downgrade() -> None:
    plans = sa.table("plans", sa.column("code", sa.String), sa.column("features", sa.JSON))
    for code, features in OLD_FEATURES.items():
        op.execute(plans.update().where(plans.c.code == code).values(features=features))
    op.drop_column("plans", "max_regions")
    op.execute("DELETE FROM notification_channels WHERE type IN ('slack','discord','teams','ntfy')")
    op.drop_column("notification_channels", "pending_config")
    op.drop_constraint("ck_channels_type", "notification_channels", type_="check")
    op.create_check_constraint(
        "ck_channels_type", "notification_channels", "type IN ('telegram','email','webhook')"
    )
    op.drop_table("notification_templates")
    op.drop_index("ix_incidents_open", table_name="incidents")
    op.drop_table("incident_escalations")
    op.drop_index("ix_escalation_rules_site_id", table_name="escalation_rules")
    op.drop_index("ix_escalation_rules_user_id", table_name="escalation_rules")
    op.drop_table("escalation_rules")
    op.drop_index("ix_diagnostics_incident", table_name="check_diagnostics")
    op.drop_index("ix_diagnostics_site_created", table_name="check_diagnostics")
    op.drop_table("check_diagnostics")
    op.drop_index("ix_region_state_due", table_name="site_region_state")
    op.drop_table("site_region_state")
    op.drop_table("regions")
    op.drop_constraint("uq_sites_heartbeat_token", "sites", type_="unique")
    op.execute("DELETE FROM sites WHERE monitor_type IN ('heartbeat','dns')")
    for name in (
        "fast_streak",
        "slow_streak",
        "check_domain",
        "regions",
        "expiry_notified",
        "expiry_thresholds",
        "assertions",
        "last_response_encrypted",
        "baseline_at",
        "baseline_mad_ms",
        "baseline_ms",
        "slo_target",
        "domain_checked_at",
        "domain_expires_at",
        "tcp_expect",
        "tcp_send",
        "dns_values",
        "dns_expected",
        "dns_record_type",
        "last_ping_at",
        "heartbeat_grace_seconds",
        "heartbeat_period_seconds",
        "heartbeat_prefix",
        "heartbeat_token_hash",
        "ssl_details",
    ):
        op.drop_column("sites", name)
    op.drop_constraint("ck_sites_monitor_type", "sites", type_="check")
    op.create_check_constraint(
        "ck_sites_monitor_type", "sites", "monitor_type IN ('http','keyword','tcp','ssl-only')"
    )
