"""Session revocation and payment currency."""

import sqlalchemy as sa

from alembic import op

revision = "0005_security"
down_revision = "0004_rollups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("auth_version", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        "payments", sa.Column("currency", sa.String(3), server_default="RUB", nullable=False)
    )
    op.execute("DELETE FROM account_tokens")
    op.execute("UPDATE status_subscribers SET token_hash=NULL, expires_at=NULL WHERE NOT confirmed")
    op.execute("UPDATE mail_jobs SET status='canceled', body='' WHERE status='pending'")
    op.execute("UPDATE mail_jobs SET body='' WHERE status <> 'pending'")
    op.execute(
        "UPDATE payments SET raw_payload=json_build_object('id',provider_payment_id,'status',status,'amount_kop',amount_kop,'currency',currency)"
    )


def downgrade() -> None:
    op.drop_column("payments", "currency")
    op.drop_column("users", "auth_version")
