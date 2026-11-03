"""In-sample / out-of-sample and walk-forward orchestration with append-only records.

Parameter selection uses the training window only (highest Sharpe; ties -> first in the grid). The
chosen parameters are then run once on the unseen window. Results are research measurements only.
"""
from dataclasses import replace
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from aqt.research.engine import BacktestConfig, BacktestResult, run_backtest
from aqt.research.metrics import compute_metrics
from aqt.research.records import save_run
from aqt.research.splits import chronological_split, trading_days, walk_forward_windows
from aqt.research.strategies import make


def _score(r: BacktestResult) -> float:
    s = r.metrics.get("sharpe")
    return s if s is not None else float("-inf")


PERSIST = True  # set False only for dry runs against a database without the PHASE 2 tables (no record kept)


def run_and_save(session: Session, code: str, params: dict, cfg: BacktestConfig, kind: str,
                 extra: dict | None = None) -> tuple[int | None, BacktestResult]:
    started = datetime.now(timezone.utc)
    strat = make(code, **params)
    res = run_backtest(session, strat, cfg)
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
    for name, kind in (("validation", "OUT_OF_SAMPLE"), ("test", "OUT_OF_SAMPLE")):
        if name in split:
            rid, res = run_and_save(session, code, best,
                                    replace(base, start=split[name][0], end=split[name][1]), kind, {"role": name})
            out[name] = {"run_id": rid, "metrics": res.metrics}
    return out


def walk_forward(session: Session, code: str, grid: list[dict], base: BacktestConfig, train_days: int,
                 test_days: int, step: int | None = None, anchored: bool = False) -> dict:
    days = trading_days(session, base.start, base.end)
    windows = walk_forward_windows(days, train_days, test_days, step, anchored)
    if not windows:
        raise ValueError("period too short for one walk-forward window")
    started = datetime.now(timezone.utc)
    stitched, trades, fills, per_window, child_ids = [], [], [], [], []
    equity = base.initial_cash
    for w in windows:
        best, tried = _select(session, code, grid, replace(base, start=w.train_start, end=w.train_end))
        rid, res = run_and_save(session, code, best, replace(base, start=w.test_start, end=w.test_end),
                                "OUT_OF_SAMPLE", {"role": "walk_forward_test", "window": w.to_dict()})
        child_ids += [t["run_id"] for t in tried] + [rid]
        scale = equity / base.initial_cash  # chain the windows: each starts with the previous end equity
        for d, e, c, x in res.equity_curve:
            stitched.append([d, e * scale, c * scale, x])
        trades += [{**t, "net_pnl": t["net_pnl"] * scale, "gross_pnl": t["gross_pnl"] * scale} for t in res.trades]
        fills += res.fills
        equity = stitched[-1][1] if stitched else equity
        per_window.append({"window": w.to_dict(), "selected_params": best, "test_run_id": rid,
                           "test_metrics": {k: res.metrics.get(k) for k in
                                            ("total_return", "sharpe", "max_drawdown", "trades", "win_rate")}})
    metrics = compute_metrics(stitched, trades, fills, base.initial_cash)
    summary = BacktestResult(config={**base.to_dict(), "start": str(windows[0].test_start),
                                     "end": str(windows[-1].test_end)},
                             strategy={"code": code, "grid": grid}, equity_curve=stitched, trades=trades,
                             fills=[], rejections=[], metrics=metrics, benchmark={})
    strat = make(code, **grid[0])
    sid = None if not PERSIST else save_run(session, strat, summary, "WALK_FORWARD", started,
                   {"role": "walk_forward_summary", "grid": grid, "windows": per_window, "child_run_ids": child_ids,
                    "train_days": train_days, "test_days": test_days, "anchored": anchored})
    return {"summary_run_id": sid, "windows": per_window, "oos_metrics": metrics}
