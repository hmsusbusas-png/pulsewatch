"""saas_core

Revision ID: 0d88e251fc1c
Revises: 0001_initial
Create Date: 2026-11-03 08:02:19.150679

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0d88e251fc1c"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("price_kop", sa.Integer(), nullable=False),
        sa.Column("period_days", sa.Integer(), nullable=False),
        sa.Column("max_sites", sa.Integer(), nullable=False),
        sa.Column("min_interval_sec", sa.Integer(), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=False),
        sa.Column("max_channels", sa.Integer(), nullable=True),
        sa.Column("features", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "worker_heartbeats",
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_table(
        "account_tokens",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(length=20), nullable=False),
        sa.Column("value", sa.String(length=320), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_index(op.f("ix_account_tokens_user_id"), "account_tokens", ["user_id"], unique=False)
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("prefix", sa.String(length=20), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=8), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(op.f("ix_api_keys_user_id"), "api_keys", ["user_id"], unique=False)
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_log_user_id"), "audit_log", ["user_id"], unique=False)
    op.create_table(
        "notification_channels",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("config", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_notification_channels_user_id"), "notification_channels", ["user_id"], unique=False
    )
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "cancel_at_period_end", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("trial_end", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('trialing','active','past_due','canceled','expired')",
            name="ck_subscription_status",
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(
        op.f("ix_subscriptions_current_period_end"),
        "subscriptions",
        ["current_period_end"],
        unique=False,
    )
    op.create_index(op.f("ix_subscriptions_status"), "subscriptions", ["status"], unique=False)
    op.create_table(
        "daily_uptime",
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("successful", sa.Integer(), nullable=False),
        sa.Column("response_ms_sum", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("site_id", "day"),
    )
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("response_code", sa.Integer(), nullable=True),
        sa.Column("error", sa.String(length=300), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["channel_id"], ["notification_channels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_deliveries_due", "notification_deliveries", ["status", "next_attempt_at"], unique=False
    )
    op.create_index(
        op.f("ix_notification_deliveries_channel_id"),
        "notification_deliveries",
        ["channel_id"],
        unique=False,
    )
    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("subscription_id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("provider_payment_id", sa.String(length=100), nullable=False),
        sa.Column("amount_kop", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.CheckConstraint("amount_kop >= 0", name="ck_payment_amount"),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_payment_id"),
    )
    op.create_index(op.f("ix_payments_status"), "payments", ["status"], unique=False)
    op.create_index(op.f("ix_payments_user_id"), "payments", ["user_id"], unique=False)
    op.create_table(
        "site_channels",
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["channel_id"], ["notification_channels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("site_id", "channel_id"),
    )
    op.create_table(
        "incident_notes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("incident_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("text", sa.String(length=2000), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_incident_notes_incident_id"), "incident_notes", ["incident_id"], unique=False
    )
    op.add_column(
        "sites", sa.Column("paused", sa.Boolean(), server_default=sa.text("false"), nullable=False)
    )
    op.add_column("sites", sa.Column("pause_reason", sa.String(length=200), nullable=True))
    op.add_column(
        "sites",
        sa.Column(
            "next_check_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column("sites", sa.Column("lease_token", sa.String(length=64), nullable=True))
    op.add_column("sites", sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "sites",
        sa.Column("monitor_type", sa.String(length=16), server_default="http", nullable=False),
    )
    op.add_column(
        "sites", sa.Column("method", sa.String(length=8), server_default="GET", nullable=False)
    )
    op.add_column("sites", sa.Column("headers_encrypted", sa.Text(), nullable=True))
    op.add_column("sites", sa.Column("body", sa.Text(), nullable=True))
    op.add_column(
        "sites", sa.Column("expected_codes", sa.JSON(), server_default="[200]", nullable=False)
    )
    op.add_column(
        "sites", sa.Column("timeout_seconds", sa.Integer(), server_default="10", nullable=False)
    )
    op.add_column(
        "sites",
        sa.Column("follow_redirects", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.add_column("sites", sa.Column("keyword", sa.String(length=1000), nullable=True))
    op.add_column(
        "sites",
        sa.Column("keyword_present", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.add_column(
        "sites", sa.Column("failure_threshold", sa.Integer(), server_default="2", nullable=False)
    )
    op.add_column(
        "sites", sa.Column("cooldown_seconds", sa.Integer(), server_default="3600", nullable=False)
    )
    op.add_column("sites", sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sites", sa.Column("maintenance", sa.JSON(), server_default="[]", nullable=False))
    op.add_column("sites", sa.Column("tags", sa.JSON(), server_default="[]", nullable=False))
    op.add_column("sites", sa.Column("group", sa.String(length=100), nullable=True))
    op.create_index(op.f("ix_sites_next_check_at"), "sites", ["next_check_at"], unique=False)
    op.add_column(
        "users", sa.Column("telegram_link_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("users", sa.Column("telegram_name", sa.String(length=100), nullable=True))
    op.add_column(
        "users", sa.Column("telegram_mute_until", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "users",
        sa.Column("trial_used", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "users",
        sa.Column("is_admin", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.execute(
        "UPDATE users u SET telegram_chat_id = NULL WHERE telegram_chat_id IS NOT NULL "
        "AND EXISTS (SELECT 1 FROM users other WHERE other.telegram_chat_id = u.telegram_chat_id AND other.id < u.id)"
    )
    op.create_unique_constraint("users_telegram_chat_id_key", "users", ["telegram_chat_id"])


def downgrade() -> None:
    op.drop_constraint("users_telegram_chat_id_key", "users", type_="unique")
    op.drop_column("users", "is_admin")
    op.drop_column("users", "trial_used")
    op.drop_column("users", "telegram_mute_until")
    op.drop_column("users", "telegram_name")
    op.drop_column("users", "telegram_link_expires_at")
    op.drop_index(op.f("ix_sites_next_check_at"), table_name="sites")
    op.drop_column("sites", "group")
    op.drop_column("sites", "tags")
    op.drop_column("sites", "maintenance")
    op.drop_column("sites", "last_notified_at")
    op.drop_column("sites", "cooldown_seconds")
    op.drop_column("sites", "failure_threshold")
    op.drop_column("sites", "keyword_present")
    op.drop_column("sites", "keyword")
    op.drop_column("sites", "follow_redirects")
    op.drop_column("sites", "timeout_seconds")
    op.drop_column("sites", "expected_codes")
    op.drop_column("sites", "body")
    op.drop_column("sites", "headers_encrypted")
    op.drop_column("sites", "method")
    op.drop_column("sites", "monitor_type")
    op.drop_column("sites", "lease_until")
    op.drop_column("sites", "lease_token")
    op.drop_column("sites", "next_check_at")
    op.drop_column("sites", "pause_reason")
    op.drop_column("sites", "paused")
    op.drop_index(op.f("ix_incident_notes_incident_id"), table_name="incident_notes")
    op.drop_table("incident_notes")
    op.drop_table("site_channels")
    op.drop_index(op.f("ix_payments_user_id"), table_name="payments")
    op.drop_index(op.f("ix_payments_status"), table_name="payments")
    op.drop_table("payments")
    op.drop_index(
        op.f("ix_notification_deliveries_channel_id"), table_name="notification_deliveries"
    )
    op.drop_index("ix_deliveries_due", table_name="notification_deliveries")
    op.drop_table("notification_deliveries")
    op.drop_table("daily_uptime")
    op.drop_index(op.f("ix_subscriptions_status"), table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_current_period_end"), table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_index(op.f("ix_notification_channels_user_id"), table_name="notification_channels")
    op.drop_table("notification_channels")
    op.drop_index(op.f("ix_audit_log_user_id"), table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index(op.f("ix_api_keys_user_id"), table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_index(op.f("ix_account_tokens_user_id"), table_name="account_tokens")
    op.drop_table("account_tokens")
    op.drop_table("worker_heartbeats")
    op.drop_table("plans")
