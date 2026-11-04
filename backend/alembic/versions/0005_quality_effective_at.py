"""Point-in-time audit of data-quality exclusions; flags for backtests affected by later discoveries

Existing issues all come from the daily rule-based checks, whose result is a function of the day's
published data: they become known and effective at that day's check time (17:45 Asia/Taipei). `created_at`
keeps the moment the row was really written.

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("data_quality_issues", sa.Column("known_at", sa.DateTime(timezone=True)))
    op.add_column("data_quality_issues", sa.Column("effective_at", sa.DateTime(timezone=True)))
    op.add_column("data_quality_issues", sa.Column("detection_method", sa.String(32), nullable=False,
                                                   server_default="AUTO_RULE"))
    op.execute("""
        UPDATE data_quality_issues
        SET known_at = COALESCE((trade_date + time '17:45') AT TIME ZONE 'Asia/Taipei', created_at),
            effective_at = COALESCE((trade_date + time '17:45') AT TIME ZONE 'Asia/Taipei', created_at)
    """)
    op.alter_column("data_quality_issues", "known_at", nullable=False)
    op.alter_column("data_quality_issues", "effective_at", nullable=False)
    op.add_column("backtest_runs", sa.Column("benchmark_comparable", sa.Boolean))
    op.create_table(
        "backtest_run_invalidations",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("backtest_run_id", sa.BigInteger, sa.ForeignKey("backtest_runs.id"), nullable=False),
        sa.Column("data_quality_issue_id", sa.BigInteger, sa.ForeignKey("data_quality_issues.id")),
        sa.Column("status", sa.String(16), nullable=False, server_default="NEEDS_RERUN"),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_backtest_invalidations_run", "backtest_run_invalidations", ["backtest_run_id"])
    op.execute("CREATE TRIGGER backtest_run_invalidations_append_only BEFORE UPDATE OR DELETE "
               "ON backtest_run_invalidations FOR EACH ROW EXECUTE FUNCTION aqt_forbid_change()")
    op.execute("CREATE TRIGGER backtest_run_invalidations_no_truncate BEFORE TRUNCATE "
               "ON backtest_run_invalidations FOR EACH STATEMENT EXECUTE FUNCTION aqt_forbid_change()")


def downgrade() -> None:
    op.drop_table("backtest_run_invalidations")
    op.drop_column("backtest_runs", "benchmark_comparable")
    for c in ("detection_method", "effective_at", "known_at"):
        op.drop_column("data_quality_issues", c)
