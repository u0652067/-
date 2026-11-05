"""Schema safety: the application never migrates by itself and never runs against the wrong schema.

Every command that uses the database checks `alembic_version` against the head shipped in this build
and refuses to start on a mismatch. Migrations run only through `init-db --apply-migrations`
(scripts\\migrate.ps1), never as a side effect of starting, restarting or rebuilding a service.

Rollback policy: `alembic downgrade` of 0004/0005 removes audit metadata, so it is allowed only while
no backtest, audit or invalidation record exists (`rollback_check`). After the first such record the
only standard rollback is restoring the pre-migration backup.
"""
import os

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# rows here hold audit metadata a downgrade would destroy: (label, table, optional filter)
AUDIT_RECORDS = (
    ("backtest_runs", "backtest_runs", None),
    ("backtest_trades", "backtest_trades", None),
    ("backtest_run_invalidations", "backtest_run_invalidations", None),
    ("post_hoc_quality_issues", "data_quality_issues", "detection_method <> 'AUTO_RULE'"),
    ("strategy_versions", "strategy_versions", None),
    ("strategy_stage_changes", "strategy_stage_changes", None),
    ("job_reconciliations", "job_reconciliations", None),
)
DOWNGRADE_LOSES = {
    "0005a": ["table job_reconciliations (the evidence and reason for every stuck job)"],
    "0005": ["data_quality_issues.known_at / effective_at / detection_method (when each exclusion took effect)",
             "backtest_runs.benchmark_comparable",
             "table backtest_run_invalidations (every NEEDS_RERUN flag)"],
    "0004": ["backtest_runs: config, params_hash, data_version, data_version_hash, data_manifest, results_hash, "
             "engine_version, started_at, finished_at, parent_run_id, disclaimer, dividend_mode, benchmark_index, "
             "benchmark_kind, usable_for_evaluation, evaluation",
             "backtest_trades: entry_costs, exit_costs, gross_pnl",
             "append-only triggers of backtest_runs and backtest_trades"],
}


class SchemaMismatch(RuntimeError):
    pass


def expected_head() -> str:
    cfg = Config(os.path.join(BACKEND, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND, "alembic"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def current_version(engine: Engine) -> str | None:
    if not inspect(engine).has_table("alembic_version"):
        return None
    with engine.connect() as c:
        return c.execute(text("SELECT version_num FROM alembic_version")).scalar()


def schema_status(engine: Engine) -> dict:
    cur, head = current_version(engine), expected_head()
    return {"database_version": cur, "build_expects": head, "match": cur == head,
            "build_commit": os.environ.get("AQT_BUILD_COMMIT", "unknown")}


def require_schema(engine: Engine) -> dict:
    st = schema_status(engine)
    if not st["match"]:
        raise SchemaMismatch(
            f"database schema is {st['database_version']} but this build (commit {st['build_commit']}) expects "
            f"{st['build_expects']}. Nothing was changed. Migrations never run automatically: use the image that "
            "matches this database, or apply the approved migration with scripts\\migrate.ps1.")
    return st


def rollback_check(engine: Engine) -> dict:
    """May `alembic downgrade` be used? Only while no audit record exists."""
    cur = current_version(engine)
    counts: dict[str, int] = {}
    insp = inspect(engine)
    with engine.connect() as c:
        for label, table, where in AUDIT_RECORDS:
            if not insp.has_table(table):
                continue
            if where and "detection_method" in where and "detection_method" not in {
                    x["name"] for x in insp.get_columns(table)}:
                continue
            q = f"SELECT count(*) FROM {table}" + (f" WHERE {where}" if where else "")
            counts[label] = c.execute(text(q)).scalar()
    written = {k: v for k, v in counts.items() if v}
    return {"database_version": cur, "audit_records": counts, "downgrade_allowed": not written,
            "standard_rollback": "alembic downgrade (no audit record exists)" if not written else
            "restore the pre-migration backup; downgrade would destroy audit metadata",
            "downgrade_would_lose": DOWNGRADE_LOSES}
