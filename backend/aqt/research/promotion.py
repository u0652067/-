"""Strategy promotion rules: a process gate, never a verdict.

Stages say which review a version went through, not that it works:
    CANDIDATE -> BACKTESTED -> OOS_REVIEWED -> WF_REVIEWED
There is no automatic path beyond WF_REVIEWED: PAPER / ACTIVE belong to phases that have not started and
are refused here. No stage means effective, profitable or tradable.

A run is evidence only if `evidence_problems()` is empty. It is refused when
  * it holds frozen / untradable positions (usable_for_evaluation is not true);
  * its benchmark is not comparable with the strategy's market scope;
  * it is flagged NEEDS_RERUN, or the data it read has changed since (fingerprint mismatch);
  * it used fundamentals, has no data manifest / dividend mode, or belongs to another version.
A walk-forward summary also needs every child run to qualify.

Thresholds are the pre-declared RULES below; a gate not met is recorded, not hidden.
"""
from datetime import date

from sqlalchemy.orm import Session

from aqt.models import BacktestRun, StrategyStageChange, StrategyVersion
from aqt.models.base import utcnow
from aqt.research.fingerprint import data_fingerprint
from aqt.research.records import run_validity

ORDER = ("CANDIDATE", "BACKTESTED", "OOS_REVIEWED", "WF_REVIEWED")
FORBIDDEN_STAGES = ("PAPER", "ACTIVE")
RULES_VERSION = "2026-11-05.1"
RULES = {
    "BACKTESTED": {"kind": "IN_SAMPLE", "min_trades": 100, "min_years": 3.0, "min_sharpe": 0.0,
                   "min_excess_return": None},
    "OOS_REVIEWED": {"kind": "OUT_OF_SAMPLE", "role": "test", "min_trades": 30, "min_years": 0.5, "min_sharpe": 0.0,
                     "min_excess_return": 0.0},
    "WF_REVIEWED": {"kind": "WALK_FORWARD", "min_trades": 100, "min_years": 2.0, "min_sharpe": 0.0,
                    "min_excess_return": 0.0, "min_windows": 4, "min_positive_window_share": 0.5},
}


def evidence_problems(session: Session, run: BacktestRun, version_id: int, check_children: bool = True) -> list[str]:
    p: list[str] = []
    if run.strategy_version_id != version_id:
        p.append("run belongs to another strategy version")
    if run.status != "DONE":
        p.append(f"run status {run.status}")
    if run.usable_for_evaluation is not True:
        reasons = (run.evaluation or {}).get("unusable_reasons") or ["not marked usable"]
        p.append("frozen or untradable holdings: " + "; ".join(reasons))
    if run.benchmark_comparable is not True:
        p.append("benchmark market scope is not comparable with the strategy")
    if not run.dividend_mode:
        p.append("dividend mode not recorded")
    if (run.config or {}).get("allow_fundamentals"):
        p.append("run used fundamentals")
    if not run.data_version_hash or not (run.data_manifest or {}).get("manifest"):
        p.append("no data manifest")
    if run_validity(session, run.id)["status"] != "VALID":
        p.append("flagged NEEDS_RERUN")
    if run.data_manifest and run.data_version_hash:
        cur = data_fingerprint(session, run.period_start, run.period_end)
        if cur["sha256"] != run.data_version_hash:
            changed = [t for t, m in cur["manifest"].items() if m != run.data_manifest.get("manifest", {}).get(t)]
            p.append(f"data changed since the run: {', '.join(changed)}")
    if check_children and run.kind == "WALK_FORWARD":
        kids = (run.config or {}).get("window_run_ids") or []
        if not kids:
            p.append("walk-forward summary lists no window runs")
        for rid in kids:
            child = session.get(BacktestRun, rid)
            if child is None:
                p.append(f"window run {rid} missing")
                continue
            # window runs carry capital forward: each is reviewed like the summary, without its own children
            for x in evidence_problems(session, child, version_id, check_children=False):
                p.append(f"window run {rid}: {x}")
    return p


def _years(run: BacktestRun) -> float:
    return ((run.period_end - run.period_start).days + 1) / 365.25


def gate_failures(run: BacktestRun, stage: str) -> list[str]:
    r, m, out = RULES[stage], run.metrics or {}, []
    if run.kind != r["kind"]:
        out.append(f"needs a {r['kind']} run, got {run.kind}")
    if r.get("role") and (run.config or {}).get("role") != r["role"]:
        out.append(f"needs role {r['role']}")
    if (m.get("trades") or 0) < r["min_trades"]:
        out.append(f"trades {m.get('trades')} < {r['min_trades']}")
    if _years(run) < r["min_years"]:
        out.append(f"period {_years(run):.2f}y < {r['min_years']}y")
    if m.get("sharpe") is None or m["sharpe"] <= r["min_sharpe"]:
        out.append(f"net Sharpe {m.get('sharpe')} not above {r['min_sharpe']}")
    if r["min_excess_return"] is not None:
        ex = m.get("excess_return_vs_benchmark")
        if ex is None or ex <= r["min_excess_return"]:
            out.append(f"excess return vs comparable benchmark {ex} not above {r['min_excess_return']}")
    if stage == "WF_REVIEWED":
        wins = (run.config or {}).get("windows") or []
        if len(wins) < r["min_windows"]:
            out.append(f"{len(wins)} windows < {r['min_windows']}")
        pos = sum(1 for w in wins if (w["end_equity"] > w["start_equity"]))
        if wins and pos / len(wins) < r["min_positive_window_share"]:
            out.append(f"positive windows {pos}/{len(wins)} below {r['min_positive_window_share']}")
    return out


def review(session: Session, version_id: int, to_stage: str, run_id: int) -> dict:
    """Apply one gate. The outcome, passed or not, is written to the append-only stage log."""
    if to_stage in FORBIDDEN_STAGES or to_stage not in ORDER[1:]:
        raise ValueError(f"stage {to_stage} is not available in PHASE 2")
    v = session.get(StrategyVersion, version_id)
    if v.stage not in ORDER or ORDER.index(to_stage) != ORDER.index(v.stage) + 1:
        raise ValueError(f"{v.stage} -> {to_stage} skips a review")
    run = session.get(BacktestRun, run_id)
    evidence = evidence_problems(session, run, version_id)
    gate = gate_failures(run, to_stage) if not evidence else []
    passed = not evidence and not gate
    note = {"rules_version": RULES_VERSION, "rule": RULES[to_stage], "run_id": run_id,
            "evidence_problems": evidence, "gate_failures": gate, "metrics": {
                k: (run.metrics or {}).get(k) for k in ("sharpe", "total_return", "excess_return_vs_benchmark",
                                                        "max_drawdown", "trades")},
            "statement": "process gate only; not a claim that the strategy is effective, profitable or tradable",
            "reviewed_on": str(date.today())}
    session.add(StrategyStageChange(strategy_version_id=version_id, from_stage=v.stage,
                                    to_stage=to_stage if passed else v.stage,
                                    reason="gate met" if passed else "gate NOT met: stays at current stage",
                                    evidence=note))
    if passed:
        v.stage, v.promoted_at = to_stage, utcnow()
    session.flush()
    return {"passed": passed, "stage": v.stage, **note}
