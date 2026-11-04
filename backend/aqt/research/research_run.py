"""Registered research plan on real data (`python -m aqt.research.research_run START END`).

Run it on a scratch copy of the database. Order matters: candidates are registered first, then factor
studies, then the evaluations the plans fixed, then the promotion review of whatever came out. Every run
is stored append-only; results that fail a gate stay on record. Nothing here claims effectiveness.
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
from aqt.research import evaluation, factors, promotion
from aqt.research.engine import BacktestConfig, check_readiness
from aqt.research.records import code_fingerprint
from aqt.research.registry import EvaluationPlan, register_candidate, registry_listing

MA_GRID = [{"top_n": 10, "lookback": 20, "rebalance": "week"}, {"top_n": 20, "lookback": 60, "rebalance": "week"}]
TOP_METRICS = ("total_return", "cagr", "sharpe", "max_drawdown", "trades", "win_rate", "expectancy_per_trade",
               "benchmark_total_return", "excess_return_vs_benchmark", "benchmark_comparable",
               "usable_for_evaluation")


def _pick(m: dict) -> dict:
    return {k: m.get(k) for k in TOP_METRICS}


def _save(out: dict) -> str | None:
    rd = get_settings().report_dir
    if not rd:
        return None
    os.makedirs(os.path.join(rd, "research"), exist_ok=True)
    path = os.path.join(rd, "research", f"research_run_{datetime.now(TPE):%Y%m%d}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1, default=str)
    return path


def main(start: date, end: date) -> dict:
    engine = get_engine()
    out: dict = {"disclaimer": DISCLAIMER, "window": [str(start), str(end)], "code_ref": code_fingerprint()[:40]}
    with session_scope(engine) as s:
        check_readiness(s)
        plan = EvaluationPlan(start=str(start), end=str(end), markets=("TWSE",), train_days=500, test_days=120,
                              grid=(tuple(sorted(MA_GRID[0].items())),))
        cand = register_candidate(
            s, "ma_trend_momentum", MA_GRID[0],
            "Listed stocks in a price up-trend that lead on recent return keep outperforming over the next weeks.",
            "No registered technical candidate yet. Parts of 2023-2024 were already seen while building the engine, "
            "so that period is not untouched for this hypothesis.", plan)
        out["candidate"] = cand
        vid = cand["strategy_version_id"]
    universe_cache: dict = {}

    t0 = time.monotonic()
    out["factor_studies"] = {}
    for horizon, markets in ((20, ("TWSE", "TPEX")), (5, ("TWSE", "TPEX")), (20, ("TWSE",))):
        cfg = factors.StudyConfig(start, end, horizon=horizon, markets=markets, study_group="technical_v1",
                                  universe_cache=universe_cache)
        with session_scope(engine) as s:
            res = factors.run_study(s, cfg)
            ids = factors.save_studies(s, cfg, res, code_fingerprint()[:40])
        mt = factors.multiple_testing(res)
        key = f"h{horizon}_{'+'.join(markets)}"
        out["factor_studies"][key] = {
            "study_ids": ids, "family_size": mt["family_size"], "factors": {
                f: {"evidence_label": factors.evidence_label(r, mt["factors"].get(f, {}).get("holm_p")),
                    "holm_p": mt["factors"].get(f, {}).get("holm_p"), "overall": r["overall"],
                    "earlier_sample": r.get("earlier_sample"), "later_sample": r.get("later_sample"),
                    "by_regime": r.get("by_regime"), "by_year": {
                        y: {k: v.get(k) for k in ("mean_ic", "mean_top_minus_bottom", "dates")}
                        for y, v in (r.get("by_year") or {}).items()}, "sample_counts": r["sample_counts"]}
                for f, r in res.items()}}
        _save(out)
    out["factor_seconds"] = round(time.monotonic() - t0)

    base = BacktestConfig(start, end, markets=plan.markets, universe_cache=universe_cache)
    with session_scope(engine) as s:
        t0 = time.monotonic()
        rid, res = evaluation.run_and_save(s, "ma_trend_momentum", MA_GRID[0], base, "IN_SAMPLE",
                                           {"role": "full_period_reference"})
        out["full_period"] = {"run_id": rid, "seconds": round(time.monotonic() - t0), "metrics": _pick(res.metrics),
                              "regime_metrics": res.regime_metrics, "evaluation": res.evaluation,
                              "rejections": len(res.rejections)}
        _save(out)
    with session_scope(engine) as s:
        t0 = time.monotonic()
        oos = evaluation.out_of_sample(s, "ma_trend_momentum", [MA_GRID[0]], base)  # fixed, registered parameters
        out["out_of_sample"] = {
            "seconds": round(time.monotonic() - t0), "split": oos["split"], "selected_params": oos["selected_params"],
            "parameter_search": oos["parameter_search"],
            **{k: {"run_id": oos[k]["run_id"], "metrics": _pick(oos[k]["metrics"]), "evaluation": oos[k]["evaluation"]}
               for k in ("validation", "test") if k in oos}}
        _save(out)
    with session_scope(engine) as s:
        t0 = time.monotonic()
        wf = evaluation.walk_forward(s, "ma_trend_momentum", [MA_GRID[0]], base, plan.train_days, plan.test_days)
        out["walk_forward"] = {
            "seconds": round(time.monotonic() - t0), "summary_run_id": wf["summary_run_id"],
            "metrics": _pick(wf["oos_metrics"]), "costs_total": wf["costs_total"], "evaluation": wf["evaluation"],
            "windows": [{k: w[k] for k in ("window", "selected_params", "start_equity", "end_equity", "carried_positions",
                                           "test_metrics", "rebalance_days", "test_run_id")} for w in wf["windows"]]}
        _save(out)

    with session_scope(engine) as s:  # sensitivity only: selected parameters are not the registered version
        t0 = time.monotonic()
        wf2 = evaluation.walk_forward(s, "ma_trend_momentum", MA_GRID, base, plan.train_days, plan.test_days)
        out["walk_forward_parameter_selection_sensitivity"] = {
            "seconds": round(time.monotonic() - t0), "metrics": _pick(wf2["oos_metrics"]),
            "not_evidence": "parameters selected per window; not the registered fixed version",
            "selected": [w["selected_params"] for w in wf2["windows"]]}
        _save(out)

    with session_scope(engine) as s:
        reviews = []
        for stage, run_id in (("BACKTESTED", out["full_period"]["run_id"]),
                              ("OOS_REVIEWED", out["out_of_sample"]["test"]["run_id"]),
                              ("WF_REVIEWED", out["walk_forward"]["summary_run_id"])):
            r = promotion.review(s, vid, stage, run_id)
            reviews.append({"to_stage": stage, "run_id": run_id, "passed": r["passed"], "stage_after": r["stage"],
                            "evidence_problems": r["evidence_problems"], "gate_failures": r["gate_failures"]})
            if not r["passed"]:
                break  # later gates need the earlier ones; the failure is on record
        out["promotion_reviews"] = reviews
        out["registry"] = registry_listing(s)
    out["saved_to"] = _save(out)
    return out


if __name__ == "__main__":
    r = main(date.fromisoformat(sys.argv[1]), date.fromisoformat(sys.argv[2]))
    print(json.dumps({"saved_to": r.get("saved_to"), "promotion_reviews": r.get("promotion_reviews")},
                     ensure_ascii=False, indent=1, default=str))
