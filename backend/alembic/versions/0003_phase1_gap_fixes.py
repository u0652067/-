"""PHASE 1 gap fixes: trading restrictions, financial-statement publication time, quality review

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CAPITAL_REDUCTION / REVERSE_SPLIT do not fit in 16 characters
    op.alter_column("corporate_actions", "action_type", type_=sa.String(24), existing_nullable=False)
    op.create_table(
        "trading_restrictions",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("security_id", sa.Integer, sa.ForeignKey("securities.id"), nullable=False),
        sa.Column("restriction_type", sa.String(24), nullable=False),
        sa.Column("start_date", sa.Date, nullable=False),
        sa.Column("end_date", sa.Date),
        sa.Column("announced_date", sa.Date),
        sa.Column("detail", JSONB, nullable=False, server_default="{}"),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("raw_payload_id", sa.BigInteger, sa.ForeignKey("raw_payloads.id")),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("security_id", "restriction_type", "start_date", "source", name="uq_trading_restriction"),
    )
    op.create_index("ix_trading_restrictions_dates", "trading_restrictions", ["start_date", "end_date"])

    op.add_column("financial_statements", sa.Column("published_at", sa.DateTime(timezone=True)))
    op.add_column("financial_statements", sa.Column("published_basis", sa.String(24), nullable=False,
                                                    server_default="UNKNOWN"))
    op.add_column("financial_statements", sa.Column("pit_reliable", sa.Boolean, nullable=False,
                                                    server_default=sa.false()))
    # OpenAPI rows: 出表日期 is the file generation date -> the values were public by then (upper bound)
    op.execute("UPDATE financial_statements SET published_basis='OPENAPI_FILE_DATE', pit_reliable=true "
               "WHERE published_date IS NOT NULL")

    op.add_column("data_quality_issues", sa.Column("classification", sa.String(32)))
    op.add_column("data_quality_issues", sa.Column("blocks_backtest", sa.Boolean, nullable=False,
                                                   server_default=sa.false()))
    op.add_column("data_quality_issues", sa.Column("reviewed_at", sa.DateTime(timezone=True)))
    op.create_index("ix_dq_issues_blocking", "data_quality_issues", ["security_id", "blocks_backtest"])


def downgrade() -> None:
    op.drop_index("ix_dq_issues_blocking", "data_quality_issues")
    for c in ("reviewed_at", "blocks_backtest", "classification"):
        op.drop_column("data_quality_issues", c)
    for c in ("pit_reliable", "published_basis", "published_at"):
        op.drop_column("financial_statements", c)
    op.drop_index("ix_trading_restrictions_dates", "trading_restrictions")
    op.drop_table("trading_restrictions")
    op.alter_column("corporate_actions", "action_type", type_=sa.String(16), existing_nullable=False)
