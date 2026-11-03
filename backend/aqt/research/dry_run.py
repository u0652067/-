"""Real-data validation of the PHASE 2 machinery without writing records (`python -m aqt.research.dry_run`).

Used before the PHASE 2 migration is applied to the production database. Output: JSON report with
metrics, rejection reasons and determinism check. Research measurement only — see DISCLAIMER.
"""
import json
import os
import sys
import time
from datetime import date, datetime

from aqt.config import get_settings
from aqt.data.pit import TPE
from aqt.db import get_engine, session_scope
from aqt.research import DISCLAIMER
from aqt.research import evaluation
from aqt.research.engine import BacktestConfig, run_backtest
from aqt.research.strategies import make


def main(start: date, end: date, wf_start: date) -> dict:
    evaluation.PERSIST = False
    engine = get_engine()
    out: dict = {"disclaimer": DISCLAIMER, "runs": {}}
    with session_scope(engine) as s:
        for code, params in (("cash_baseline", {}), ("ew_liquid_baseline", {"top_n": 30}),
                             ("ma_trend_momentum", {"top_n": 20, "lookback": 20})):
            t0 = time.monotonic()
            r = run_backtest(s, make(code, **params), BacktestConfig(start, end))
            r2 = run_backtest(s, make(code, **params), BacktestConfig(start, end)) if code != "cash_baseline" else r
            reasons: dict[str, int] = {}
            for x in r.rejections:
                reasons[x["reason"]] = reasons.get(x["reason"], 0) + 1
            out["runs"][code] = {"params": params, "seconds": round(time.monotonic() - t0, 1),
                                 "deterministic": r.results_hash() == r2.results_hash(),
                                 "results_hash": r.results_hash(), "metrics": r.metrics,
                                 "rejections_by_reason": reasons,
                                 "fills": sum(1 for f in r.fills if f["type"] in ("BUY", "SELL")),
                                 "corporate_action_adjustments": sum(1 for f in r.fills
                                                                     if f["type"] == "CORPORATE_ACTION")}
        t0 = time.monotonic()
        wf = evaluation.walk_forward(s, "ma_trend_momentum", [{"top_n": 10, "lookback": 20},
                                                              {"top_n": 20, "lookback": 60}],
                                     BacktestConfig(wf_start, end), train_days=120, test_days=60)
        out["walk_forward"] = {"seconds": round(time.monotonic() - t0, 1), "windows": wf["windows"],
                               "oos_metrics": wf["oos_metrics"]}
    rd = get_settings().report_dir
    if rd:
        os.makedirs(os.path.join(rd, "backtests"), exist_ok=True)
        path = os.path.join(rd, "backtests", f"phase2_dry_run_{datetime.now(TPE):%Y%m%dT%H%M%S}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1, default=str)
        out["saved_to"] = path
    return out


if __name__ == "__main__":
    a = sys.argv[1:] or ["2024-01-01", "2024-12-31", "2023-07-01"]
    res = main(date.fromisoformat(a[0]), date.fromisoformat(a[1]), date.fromisoformat(a[2]))
    print(json.dumps({k: v for k, v in res.items() if k != "walk_forward"}, ensure_ascii=False, indent=1,
                     default=str)[:6000])
