"""status_accounts_mail

Revision ID: 3a0b90b379c2
Revises: 0d88e251fc1c
Create Date: 2026-11-03 08:02:42.643322

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "3a0b90b379c2"
down_revision: str | None = "0d88e251fc1c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rate_buckets",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("key", "started_at"),
    )
    op.create_table(
        "mail_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("recipient", sa.String(length=320), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("dedupe_key", sa.String(length=150), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index("ix_mail_jobs_due", "mail_jobs", ["status", "next_attempt_at"], unique=False)
    op.create_index(op.f("ix_mail_jobs_user_id"), "mail_jobs", ["user_id"], unique=False)
    op.create_table(
        "status_pages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=150), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index(op.f("ix_status_pages_user_id"), "status_pages", ["user_id"], unique=False)
    op.create_table(
        "status_page_sites",
        sa.Column("page_id", sa.Integer(), nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["page_id"], ["status_pages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("page_id", "site_id"),
    )
    op.create_table(
        "status_subscribers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("page_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("confirmed", sa.Boolean(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["page_id"], ["status_pages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("page_id", "email", name="uq_status_subscriber"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        op.f("ix_status_subscribers_page_id"), "status_subscribers", ["page_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_status_subscribers_page_id"), table_name="status_subscribers")
    op.drop_table("status_subscribers")
    op.drop_table("status_page_sites")
    op.drop_index(op.f("ix_status_pages_user_id"), table_name="status_pages")
    op.drop_table("status_pages")
    op.drop_index(op.f("ix_mail_jobs_user_id"), table_name="mail_jobs")
    op.drop_index("ix_mail_jobs_due", table_name="mail_jobs")
    op.drop_table("mail_jobs")
    op.drop_table("rate_buckets")
