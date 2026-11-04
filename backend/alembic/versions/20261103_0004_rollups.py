"""Daily rollups and monitor constraints."""

from alembic import op

revision = "0004_rollups"
down_revision = "3a0b90b379c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_sites_interval_min", "sites", type_="check")
    op.create_check_constraint("ck_sites_interval_min", "sites", "interval_seconds >= 30")
    op.create_check_constraint(
        "ck_sites_monitor_type", "sites", "monitor_type IN ('http','keyword','tcp','ssl-only')"
    )
    op.create_check_constraint("ck_sites_method", "sites", "method IN ('GET','HEAD','POST')")
    op.create_check_constraint("ck_sites_timeout", "sites", "timeout_seconds BETWEEN 1 AND 30")
    op.create_check_constraint("ck_sites_threshold", "sites", "failure_threshold BETWEEN 1 AND 5")
    op.create_check_constraint(
        "ck_channels_type", "notification_channels", "type IN ('telegram','email','webhook')"
    )
    op.execute("UPDATE users SET telegram_link_token = NULL, telegram_link_expires_at = NULL")
    op.execute(
        "UPDATE sites SET expected_codes = (SELECT json_agg(n) FROM generate_series(200,399) n)"
    )
    op.execute("""
        INSERT INTO daily_uptime (site_id, day, total, successful, response_ms_sum)
        SELECT site_id, (checked_at AT TIME ZONE 'UTC')::date, count(*),
               count(*) FILTER (WHERE ok), coalesce(sum(response_ms) FILTER (WHERE ok), 0)
        FROM checks GROUP BY site_id, (checked_at AT TIME ZONE 'UTC')::date
    """)
    op.execute("""
        CREATE FUNCTION pulsewatch_rollup() RETURNS trigger LANGUAGE plpgsql AS $$
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
    """)
    op.execute(
        "CREATE TRIGGER checks_rollup AFTER INSERT ON checks FOR EACH ROW EXECUTE FUNCTION pulsewatch_rollup()"
    )
    op.create_index("ix_incidents_site_started", "incidents", ["site_id", "started_at"])
    op.create_index("ix_checks_checked_at", "checks", ["checked_at"])
    op.create_index("ix_audit_user_created", "audit_log", ["user_id", "created_at"])
    op.create_index("ix_rate_bucket_started", "rate_buckets", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_rate_bucket_started", table_name="rate_buckets")
    op.drop_index("ix_audit_user_created", table_name="audit_log")
    op.drop_index("ix_checks_checked_at", table_name="checks")
    op.drop_index("ix_incidents_site_started", table_name="incidents")
    op.execute("DROP TRIGGER checks_rollup ON checks")
    op.execute("DROP FUNCTION pulsewatch_rollup()")
    for name in (
        "ck_sites_monitor_type",
        "ck_sites_method",
        "ck_sites_timeout",
        "ck_sites_threshold",
    ):
        op.drop_constraint(name, "sites", type_="check")
    op.drop_constraint("ck_channels_type", "notification_channels", type_="check")
    op.execute("UPDATE sites SET interval_seconds = 60 WHERE interval_seconds < 60")
    op.drop_constraint("ck_sites_interval_min", "sites", type_="check")
    op.create_check_constraint("ck_sites_interval_min", "sites", "interval_seconds >= 60")
