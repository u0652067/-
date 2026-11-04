"""PHASE 2: reproducible, append-only backtest records

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

APPEND_ONLY = ["backtest_runs", "backtest_trades"]


def upgrade() -> None:
    for col in (sa.Column("config", JSONB, nullable=False, server_default="{}"),
                sa.Column("params_hash", sa.String(64)),
                sa.Column("data_version", JSONB, nullable=False, server_default="{}"),
                sa.Column("data_version_hash", sa.String(64)),
                sa.Column("results_hash", sa.String(64)),
                sa.Column("engine_version", sa.String(16)),
                sa.Column("started_at", sa.DateTime(timezone=True)),
                sa.Column("finished_at", sa.DateTime(timezone=True)),
                sa.Column("parent_run_id", sa.BigInteger, sa.ForeignKey("backtest_runs.id")),
                sa.Column("disclaimer", sa.Text),
                sa.Column("dividend_mode", sa.String(32)),
                sa.Column("benchmark_index", sa.String(64)),
                sa.Column("benchmark_kind", sa.String(24)),
                sa.Column("usable_for_evaluation", sa.Boolean),
                sa.Column("evaluation", JSONB, nullable=False, server_default="{}"),
                sa.Column("data_manifest", JSONB, nullable=False, server_default="{}")):
        op.add_column("backtest_runs", col)
    op.add_column("backtest_trades", sa.Column("entry_costs", sa.Float))
    op.add_column("backtest_trades", sa.Column("exit_costs", sa.Float))
    op.add_column("backtest_trades", sa.Column("gross_pnl", sa.Float))
    # results are written once, completely, and never edited or deleted (same guard as raw_payloads)
    for t in APPEND_ONLY:
        op.execute(f"CREATE TRIGGER {t}_append_only BEFORE UPDATE OR DELETE ON {t} "
                   "FOR EACH ROW EXECUTE FUNCTION aqt_forbid_change()")
        op.execute(f"CREATE TRIGGER {t}_no_truncate BEFORE TRUNCATE ON {t} "
                   "FOR EACH STATEMENT EXECUTE FUNCTION aqt_forbid_change()")


def downgrade() -> None:
    for t in APPEND_ONLY:
        op.execute(f"DROP TRIGGER IF EXISTS {t}_append_only ON {t}")
        op.execute(f"DROP TRIGGER IF EXISTS {t}_no_truncate ON {t}")
    for c in ("gross_pnl", "exit_costs", "entry_costs"):
        op.drop_column("backtest_trades", c)
    for c in ("data_manifest", "evaluation", "usable_for_evaluation", "benchmark_kind", "benchmark_index",
              "dividend_mode", "disclaimer", "parent_run_id", "finished_at", "started_at", "engine_version", "results_hash",
              "data_version_hash", "data_version", "params_hash", "config"):
        op.drop_column("backtest_runs", c)
