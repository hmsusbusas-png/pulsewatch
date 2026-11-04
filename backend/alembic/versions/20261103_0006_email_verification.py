"""Require email ownership before login."""

import sqlalchemy as sa

from alembic import op

revision = "0006_email_verification"
down_revision = "0005_security"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute("UPDATE refresh_tokens SET revoked=true")


def downgrade() -> None:
    op.drop_column("users", "email_verified_at")
