"""Append-only reconciliation records for jobs that never reported an end state

A job killed mid-run stays RUNNING in job_runs. That row is history and is never edited: a reconciliation
records what the evidence shows about the job, why, when and by whom.

Revision ID: 0005a
Revises: 0005
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005a"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_reconciliations",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("job_run_id", sa.BigInteger, sa.ForeignKey("job_runs.id"), nullable=False),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("evidence", JSONB, nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("executed_by", sa.String(128), nullable=False),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("outcome IN ('DATA_COMPLETE', 'DATA_INCOMPLETE')", name="ck_job_reconciliations_outcome"),
    )
    op.create_index("ix_job_reconciliations_job", "job_reconciliations", ["job_run_id"])
    op.execute("CREATE TRIGGER job_reconciliations_append_only BEFORE UPDATE OR DELETE "
               "ON job_reconciliations FOR EACH ROW EXECUTE FUNCTION aqt_forbid_change()")
    op.execute("CREATE TRIGGER job_reconciliations_no_truncate BEFORE TRUNCATE "
               "ON job_reconciliations FOR EACH STATEMENT EXECUTE FUNCTION aqt_forbid_change()")


def downgrade() -> None:
    op.drop_table("job_reconciliations")
