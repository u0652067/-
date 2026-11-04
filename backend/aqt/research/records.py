"""Append-only persistence of backtests: strategy version, parameters, data version, code fingerprint,
run time and full results (equity curve, every closed trade) — written once, never updated (DB triggers)."""
import hashlib
import json
import os
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aqt.models import (
    BacktestRun, BacktestRunInvalidation, BacktestTrade, DailyPrice, FinancialStatement, RawPayload,
    Strategy as StrategyRow, StrategyVersion,
)
from aqt.research import DISCLAIMER, ENGINE_VERSION
from aqt.research.engine import BacktestResult
from aqt.research.fingerprint import data_fingerprint
from aqt.research.strategies import Strategy

_CODE_FILES = ("research/engine.py", "research/costs.py", "research/dataview.py", "research/strategies.py",
               "research/metrics.py", "research/splits.py", "research/fingerprint.py", "research/evaluation.py", "data/universe.py", "data/tradability.py",
               "data/tw_rules.py", "data/fundamentals.py")


def code_fingerprint() -> str:
    """sha256 over the engine/rule source files (works inside containers without git)."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    h = hashlib.sha256()
    for rel in _CODE_FILES:
        with open(os.path.join(base, rel), "rb") as fh:
            h.update(rel.encode() + b"\0" + fh.read().replace(b"\r\n", b"\n"))
    return h.hexdigest()


def data_version(session: Session, start, end) -> tuple[dict, dict]:
    """(summary, manifest): the summary for quick reading, the manifest is the content fingerprint."""
    fp = data_fingerprint(session, start, end)
    q = lambda stmt: session.scalar(stmt)  # noqa: E731
    summary = {
        "fingerprint_sha256": fp["sha256"], "fingerprint_period": fp["period"],
        "max_raw_payload_id": q(select(func.max(RawPayload.id))),
        "daily_prices_max_date": str(q(select(func.max(DailyPrice.trade_date)))),
        "financials_pit_reliable": q(select(func.count()).select_from(FinancialStatement)
                                     .where(FinancialStatement.pit_reliable.is_(True))),
    }
    return summary, fp


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def strategy_version_id(session: Session, strategy: Strategy) -> int:
    """Get-or-create the immutable version row. Same code+version with different params is refused:
    parameters can never change under an existing version label."""
    srow = session.scalar(select(StrategyRow).where(StrategyRow.code == strategy.code))
    if srow is None:
        srow = StrategyRow(code=strategy.code, name=strategy.code, horizon="SWING",
                           description=strategy.description)
        session.add(srow)
        session.flush()
    label = f"{strategy.version}-{strategy.params_hash()[:8]}"
    v = session.scalar(select(StrategyVersion).where(StrategyVersion.strategy_id == srow.id,
                                                     StrategyVersion.version == label))
    if v is None:
        v = StrategyVersion(strategy_id=srow.id, version=label, params=strategy.params, code_ref=code_fingerprint()[:40],
                            change_reason="PHASE 2 research run", stage="CANDIDATE")
        session.add(v)
        session.flush()
    elif v.params != strategy.params:
        raise ValueError(f"{strategy.code} {label}: stored params differ — create a new version")
    return v.id


def save_run(session: Session, strategy: Strategy, result: BacktestResult, kind: str, started_at: datetime,
             extra_config: dict | None = None) -> int:
    dv, fp = data_version(session, date.fromisoformat(result.config["start"]), date.fromisoformat(result.config["end"]))
    cfg = {**result.config, **(extra_config or {}), "rejections_by_reason": _count(result.rejections)}
    run = BacktestRun(
        strategy_version_id=strategy_version_id(session, strategy), kind=kind,
        period_start=date.fromisoformat(result.config["start"]), period_end=date.fromisoformat(result.config["end"]),
        universe_rule="build_universe+tradability.blocked_securities",
        cost_model=result.config["cost_model"], metrics=result.metrics, regime_metrics={},
        equity_curve=result.equity_curve, data_snapshot_at=datetime.now(timezone.utc),
        code_ref=code_fingerprint()[:40], status="DONE", config=cfg, params_hash=strategy.params_hash(),
        data_version=dv, data_version_hash=fp["sha256"], data_manifest=fp, results_hash=result.results_hash(),
        engine_version=ENGINE_VERSION, started_at=started_at, finished_at=datetime.now(timezone.utc),
        disclaimer=DISCLAIMER, dividend_mode=result.config.get("dividend_mode"),
        benchmark_index=result.config.get("benchmark_index"), benchmark_kind=result.config.get("benchmark_kind"),
        usable_for_evaluation=result.evaluation.get("usable_for_evaluation"), evaluation=result.evaluation,
        benchmark_comparable=result.config.get("benchmark_comparable"))
    session.add(run)
    session.flush()
    for t in result.trades:
        session.add(BacktestTrade(
            backtest_run_id=run.id, security_id=t["sid"], entry_date=date.fromisoformat(t["entry_date"]),
            exit_date=date.fromisoformat(t["exit_date"]),
            entry_price=t["entry_price"] or 0.0, exit_price=t["exit_price"], quantity=int(round(t["quantity"])),
            costs=t["costs"], net_pnl=t["net_pnl"], exit_reason=t["exit_reason"], entry_costs=t["entry_costs"],
            exit_costs=t["exit_costs"], gross_pnl=t["gross_pnl"]))
    session.flush()
    return run.id


def run_validity(session: Session, run_id: int) -> dict:
    """A stored run stays as it was written; later findings about its data are listed here."""
    flags = session.execute(select(BacktestRunInvalidation.status, BacktestRunInvalidation.reason,
                                   BacktestRunInvalidation.data_quality_issue_id, BacktestRunInvalidation.created_at)
                            .where(BacktestRunInvalidation.backtest_run_id == run_id)
                            .order_by(BacktestRunInvalidation.id)).all()
    return {"run_id": run_id, "status": "NEEDS_RERUN" if flags else "VALID",
            "flags": [dict(status=a, reason=b, issue_id=c, created_at=str(d)) for a, b, c, d in flags]}


def _count(rows: list[dict]) -> dict:
    out: dict[str, int] = {}
    for r in rows:
        out[r["reason"]] = out.get(r["reason"], 0) + 1
    return out
