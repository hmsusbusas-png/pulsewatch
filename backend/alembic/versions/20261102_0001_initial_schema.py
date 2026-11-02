"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-02 16:19:19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

user_tariff = postgresql.ENUM("free", "pro", name="user_tariff", create_type=False)
site_status = postgresql.ENUM("up", "down", "unknown", name="site_status", create_type=False)


def upgrade() -> None:
    postgresql.ENUM("free", "pro", name="user_tariff").create(op.get_bind(), checkfirst=True)
    postgresql.ENUM("up", "down", "unknown", name="site_status").create(
        op.get_bind(), checkfirst=True
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("tariff", user_tariff, server_default="free", nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("telegram_link_token", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])

    op.create_table(
        "sites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), server_default="300", nullable=False),
        sa.Column("is_public", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("show_url_publicly", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("public_slug", sa.UUID(), nullable=False),
        sa.Column("status", site_status, server_default="unknown", nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ssl_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ssl_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint("interval_seconds >= 60", name="ck_sites_interval_min"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_slug", name="uq_sites_public_slug"),
    )
    op.create_index("ix_sites_user_id", "sites", ["user_id"])
    op.create_index("ix_sites_last_checked_at", "sites", ["last_checked_at"])

    op.create_table(
        "checks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column(
            "checked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("response_ms", sa.Integer(), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_checks_site_checked_at",
        "checks",
        ["site_id", sa.literal_column("checked_at DESC")],
    )

    op.create_table(
        "incidents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_incidents_site_id", "incidents", ["site_id"])
    op.create_index(
        "uq_incidents_open_per_site",
        "incidents",
        ["site_id"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_incidents_open_per_site", table_name="incidents", postgresql_where=sa.text("ended_at IS NULL")
    )
    op.drop_index("ix_incidents_site_id", table_name="incidents")
    op.drop_table("incidents")
    op.drop_index("ix_checks_site_checked_at", table_name="checks")
    op.drop_table("checks")
    op.drop_index("ix_sites_last_checked_at", table_name="sites")
    op.drop_index("ix_sites_user_id", table_name="sites")
    op.drop_table("sites")
    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_table("users")
    postgresql.ENUM("up", "down", "unknown", name="site_status").drop(
        op.get_bind(), checkfirst=True
    )
    postgresql.ENUM("free", "pro", name="user_tariff").drop(op.get_bind(), checkfirst=True)
