"""PHASE 2 research: append-only factor / score predictive-power studies

Revision ID: 0006
Revises: 0005
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "factor_studies",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("study_group", sa.String(64), nullable=False),  # studies of one multiple-testing family
        sa.Column("factor", sa.String(48), nullable=False),
        sa.Column("horizon_days", sa.Integer, nullable=False),
        sa.Column("period_start", sa.Date, nullable=False),
        sa.Column("period_end", sa.Date, nullable=False),
        sa.Column("config", JSONB, nullable=False),
        sa.Column("result", JSONB, nullable=False),
        sa.Column("data_version_hash", sa.String(64), nullable=False),
        sa.Column("data_manifest", JSONB, nullable=False),
        sa.Column("code_ref", sa.String(80), nullable=False),
        sa.Column("results_hash", sa.String(64), nullable=False),
        sa.Column("disclaimer", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_factor_studies_group", "factor_studies", ["study_group", "factor", "horizon_days"])
    op.execute("CREATE TRIGGER factor_studies_append_only BEFORE UPDATE OR DELETE ON factor_studies "
               "FOR EACH ROW EXECUTE FUNCTION aqt_forbid_change()")
    op.execute("CREATE TRIGGER factor_studies_no_truncate BEFORE TRUNCATE ON factor_studies "
               "FOR EACH STATEMENT EXECUTE FUNCTION aqt_forbid_change()")


def downgrade() -> None:
    op.drop_table("factor_studies")
