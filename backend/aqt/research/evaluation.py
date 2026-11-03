"""In-sample / out-of-sample and walk-forward orchestration with append-only records.

Parameter selection uses the training window only (highest Sharpe; ties -> first in the grid). The
chosen parameters are then run once on the unseen window. Results are research measurements only.

Walk-forward capital is continuous: the out-of-sample windows form one portfolio. Each test window
starts from the previous window's actual end state (cash, holdings, pending exits) — nothing is
re-run with a fixed capital and rescaled — so board lots, minimum commissions, the participation cap
and costs always apply to the real portfolio size. Only the final window values the open book
(END_MARK); intermediate windows carry their holdings into the next one.
"""
from dataclasses import replace
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from aqt.research import DISCLAIMER
from aqt.research.engine import BacktestConfig, BacktestResult, PortfolioState, run_backtest
from aqt.research.metrics import compute_metrics
from aqt.research.records import save_run
from aqt.research.splits import chronological_split, trading_days, walk_forward_windows
from aqt.research.strategies import make

PERSIST = True  # False only for dry runs against a database without the PHASE 2 tables (no record kept)


def _score(r: BacktestResult) -> float:
    s = r.metrics.get("sharpe")
    return s if s is not None else float("-inf")


def run_and_save(session: Session, code: str, params: dict, cfg: BacktestConfig, kind: str,
                 extra: dict | None = None, initial_state: PortfolioState | None = None
                 ) -> tuple[int | None, BacktestResult]:
    started = datetime.now(timezone.utc)
    strat = make(code, **params)
    res = run_backtest(session, strat, cfg, initial_state)
    return (save_run(session, strat, res, kind, started, extra) if PERSIST else None), res


def _select(session, code, grid, cfg) -> tuple[dict, list]:
    tried = []
    for params in grid:
        rid, res = run_and_save(session, code, params, cfg, "IN_SAMPLE", {"role": "parameter_search"})
        tried.append({"params": params, "run_id": rid, "sharpe": res.metrics.get("sharpe"),
                      "total_return": res.metrics.get("total_return"), "_score": _score(res)})
    best = max(tried, key=lambda t: t["_score"])  # max() keeps the first of equal scores
    for t in tried:
        t.pop("_score")
    return best["params"], tried


def out_of_sample(session: Session, code: str, grid: list[dict], base: BacktestConfig, train: float = 0.6,
                  validation: float = 0.2) -> dict:
    days = trading_days(session, base.start, base.end)
    split = chronological_split(days, train, validation)
    best, tried = _select(session, code, grid, replace(base, start=split["train"][0], end=split["train"][1]))
    out = {"split": {k: [str(a), str(b)] for k, (a, b) in split.items()}, "selected_params": best,
           "parameter_search": tried}
    for name in ("validation", "test"):
        if name in split:
            rid, res = run_and_save(session, code, best, replace(base, start=split[name][0], end=split[name][1]),
                                    "OUT_OF_SAMPLE", {"role": name})
            out[name] = {"run_id": rid, "metrics": res.metrics, "evaluation": res.evaluation}
    return out


def _costs(fills: list) -> dict:
    out = {"commission": 0.0, "tax": 0.0, "slippage_vs_open": 0.0, "end_mark_selling_costs": 0.0}
    for f in fills:
        if f.get("type") in ("BUY", "SELL"):
            out["commission"] += float(f["commission"])
            out["tax"] += float(f["tax"])
            out["slippage_vs_open"] += float(f["slippage_cost"])
        elif f.get("type") == "END_MARK":
            out["end_mark_selling_costs"] += float(f["commission"]) + float(f["tax"])
    return out


def walk_forward(session: Session, code: str, grid: list[dict], base: BacktestConfig, train_days: int,
                 test_days: int, step: int | None = None, anchored: bool = False) -> dict:
    days = trading_days(session, base.start, base.end)
    windows = walk_forward_windows(days, train_days, test_days, step, anchored)
    if not windows:
        raise ValueError("period too short for one walk-forward window")
    started = datetime.now(timezone.utc)
    curve, trades, fills, rejections, per_window, child_ids = [], [], [], [], [], []
    state: PortfolioState | None = None
    usable, reasons = True, []
    for i, w in enumerate(windows):
        best, tried = _select(session, code, grid, replace(base, start=w.train_start, end=w.train_end))
        last = i == len(windows) - 1
        cfg = replace(base, start=w.test_start, end=w.test_end, terminal="MARK" if last else "CARRY")
        rid, res = run_and_save(session, code, best, cfg, "OUT_OF_SAMPLE",
                                {"role": "walk_forward_test", "window": w.to_dict()}, initial_state=state)
        child_ids += [t["run_id"] for t in tried] + [rid]
        start_eq = res.config["start_equity"]
        end_eq = res.equity_curve[-1][1] if res.equity_curve else start_eq
        curve += res.equity_curve
        trades += res.trades
        fills += res.fills
        rejections += res.rejections
        state = res.final_state
        if not res.evaluation.get("usable_for_evaluation", True):
            usable = False
            reasons += [f"window {w.test_start}..{w.test_end}: {r}" for r in res.evaluation["unusable_reasons"]]
        per_window.append({"window": w.to_dict(), "selected_params": best, "test_run_id": rid,
                           "start_equity": start_eq, "end_equity": end_eq,
                           "carried_positions": len(state.positions) if state else 0,
                           "costs": _costs(res.fills), "results_hash": res.results_hash(),
                           "test_metrics": {k: res.metrics.get(k) for k in
                                            ("total_return", "sharpe", "max_drawdown", "trades", "win_rate")}})
    metrics = compute_metrics(curve, trades, fills, base.initial_cash)
    metrics["dividend_mode"] = base.dividend_mode
    metrics["usable_for_evaluation"] = usable
    evaluation = {"usable_for_evaluation": usable, "unusable_reasons": reasons,
                  "capital": "continuous: each window starts from the previous actual end state"}
    summary = BacktestResult(config={**base.to_dict(), "start": str(windows[0].test_start),
                                     "end": str(windows[-1].test_end), "start_equity": base.initial_cash},
                             strategy={"code": code, "grid": grid}, equity_curve=curve, trades=trades,
                             fills=fills, rejections=rejections, metrics=metrics, benchmark={}, evaluation=evaluation)
    strat = make(code, **grid[0])
    sid = None if not PERSIST else save_run(
        session, strat, summary, "WALK_FORWARD", started,
        {"role": "walk_forward_summary", "grid": grid, "windows": per_window, "child_run_ids": child_ids,
         "train_days": train_days, "test_days": test_days, "anchored": anchored})
    return {"summary_run_id": sid, "windows": per_window, "oos_metrics": metrics, "evaluation": evaluation,
            "costs_total": _costs(fills), "results_hash": summary.results_hash(), "disclaimer": DISCLAIMER}
