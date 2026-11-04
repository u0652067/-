"""PHASE 2 research layer: candidate registry, factor studies, regimes, promotion gates.

Market data is SYNTHETIC. These tests check the process and its point-in-time safety, not any strategy."""
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal as D

import pandas as pd
import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from aqt.data import pipeline, quality_review
from aqt.data.pit import TPE
from aqt.db import session_scope
from aqt.jobs.runtime import set_state
from aqt.models import (
    BacktestRun, CorporateAction, DailyPrice, FactorStudy, MarketIndex, ResearchProposal, Security,
    StrategyStageChange, StrategyVersion,
)
from aqt.research import factors as fm
from aqt.research import promotion as pr
from aqt.research.dataview import DataView
from aqt.research.engine import BENCHMARKS, BacktestConfig, run_backtest
from aqt.research.evaluation import run_and_save, walk_forward
from aqt.research.records import save_run, strategy_version_id
from aqt.research.regimes import load_regimes, regime_labels, regime_metrics
from aqt.research.registry import EvaluationPlan, register_candidate, registry_listing
from aqt.research.strategies import make

READY = {"technical_backtest_ready": True, "fundamental_backtest_ready": False, "coverage_complete": True,
         "quality_status": "REVIEWED_WITH_EXCLUSIONS"}
PLAN = EvaluationPlan(start="2024-01-01", end="2024-12-31", grid=((("top_n", 3),),))
PARAMS = {"top_n": 3, "lookback": 10, "rebalance": "week"}


@pytest.fixture(autouse=True)
def ready(engine):
    set_state(engine, "data_readiness", READY)


@pytest.fixture()
def market(engine, fetcher, synthetic_market):
    m = synthetic_market
    pipeline.backfill(engine, m.dates[0], m.dates[-1], fetcher)
    with session_scope(engine) as s:  # a total-return index so listed-only runs have a comparable benchmark
        for i, d in enumerate(m.dates):
            s.add(MarketIndex(index_code=BENCHMARKS["TOTAL_RETURN_REINVEST"][0], trade_date=d, name="SYN",
                              close=D(str(20000 + i)), source="SYNTHETIC",
                              available_at=datetime.combine(d, time(14, 30), tzinfo=TPE)))
    return m


def _cfg(m, **kw):
    return BacktestConfig(m.dates[61], kw.pop("end", m.dates[-1]), initial_cash=5_000_000, markets=("TWSE",), **kw)


# ------------------------------------------------------------------ registry
def test_candidate_is_registered_before_evaluation_and_cannot_be_rewritten(engine, market):
    with session_scope(engine) as s:
        r = register_candidate(s, "ma_trend_momentum", PARAMS, "trend + momentum persists", "no technical baseline", PLAN)
        assert r["created"] is True
        v = s.get(StrategyVersion, r["strategy_version_id"])
        assert v.stage == "CANDIDATE" and "trend + momentum" in v.change_reason
        assert s.scalar(select(StrategyStageChange.to_stage)) == "CANDIDATE"
        assert s.get(ResearchProposal, r["proposal_id"]).status == "TESTING"
        assert register_candidate(s, "ma_trend_momentum", PARAMS, "trend + momentum persists", "no technical baseline",
                                  PLAN)["created"] is False
        with pytest.raises(ValueError):  # same version, moved goalposts
            register_candidate(s, "ma_trend_momentum", PARAMS, "trend + momentum persists", "no technical baseline",
                               replace(PLAN, end="2025-12-31"))
        listing = registry_listing(s)
        assert listing[0]["stage"] == "CANDIDATE" and listing[0]["plan"]["markets"] == ["TWSE"]


# ------------------------------------------------------------------ factors
def test_factor_values_use_only_data_up_to_the_decision_day(engine, market):
    m = market
    t = m.dates[70]
    with session_scope(engine) as s:
        eligible = DataView(s, t).universe().eligible
        before = fm.factor_table(DataView(s, t), eligible)
    assert not before.empty and set(fm.FACTORS) <= set(before.columns)
    with session_scope(engine) as s:
        s.execute(update(DailyPrice).where(DailyPrice.trade_date > t).values(close=DailyPrice.close * 3, volume=1))
    with session_scope(engine) as s:
        after = fm.factor_table(DataView(s, t), eligible)
    pd.testing.assert_frame_equal(before, after)


def test_forward_return_is_next_open_to_exit_open_total_return(engine, market):
    m = market
    entry, mid, exit_ = m.dates[70], m.dates[72], m.dates[75]
    with session_scope(engine) as s:
        sid = s.scalar(select(Security.id).where(Security.code == "2330"))
        s.execute(update(DailyPrice).where(DailyPrice.security_id == sid, DailyPrice.trade_date == entry)
                  .values(open=D("100"), limit_up=D("110")))
        s.execute(update(DailyPrice).where(DailyPrice.security_id == sid, DailyPrice.trade_date == exit_)
                  .values(open=D("95")))
        s.add(CorporateAction(security_id=sid, ex_date=mid, action_type="DIV", prev_close=D("100"), ref_price=D("95"),
                              source="SYNTHETIC", available_at=datetime.combine(mid, time(8), tzinfo=TPE)))
    with session_scope(engine) as s:
        r = fm.forward_returns(s, [sid], entry, exit_).iloc[0]
        assert r.ret == pytest.approx(0.0, abs=1e-9) and not r.truncated  # 95 -> 100 after the dividend
        s.execute(update(DailyPrice).where(DailyPrice.security_id == sid, DailyPrice.trade_date == entry)
                  .values(open=D("110")))  # limit-up at the open: not buyable
        s.flush()
        r = fm.forward_returns(s, [sid], entry, exit_).iloc[0]
        assert r.unbuyable and pd.isna(r.ret)


def test_stopped_name_stays_in_the_sample_with_its_last_close(engine, market):
    m = market
    entry, exit_ = m.dates[70], m.dates[75]
    with session_scope(engine) as s:
        sid = s.scalar(select(Security.id).where(Security.code == "2317"))
        last = s.get(DailyPrice, (sid, m.dates[72]))
        close, opened = last.close, s.get(DailyPrice, (sid, entry)).open
        with engine.begin() as c:
            c.execute(text("DELETE FROM technical_indicators WHERE security_id=:s AND trade_date>:d"),
                      {"s": sid, "d": m.dates[72]})
        s.query(DailyPrice).filter(DailyPrice.security_id == sid, DailyPrice.trade_date > m.dates[72]).delete()
        s.flush()
        r = fm.forward_returns(s, [sid], entry, exit_).iloc[0]
    assert r.truncated and r.ret == pytest.approx(float(close) / float(opened) - 1)


def test_study_dates_do_not_overlap_and_refuse_overlap(engine, market):
    m = market
    with session_scope(engine) as s:
        dates = fm.study_dates(s, m.dates[61], m.dates[-1], 5, None)
        assert all(a[2] <= b[1] for a, b in zip(dates, dates[1:]))
        with pytest.raises(ValueError):
            fm.study_dates(s, m.dates[61], m.dates[-1], 5, 2)


def test_study_runs_and_is_stored_append_only_with_holm_adjustment(engine, market):
    m = market
    cfg = fm.StudyConfig(m.dates[61], m.dates[-1], horizon=5, quantiles=2, min_names=3, factors=("mom_20", "trend_ma20"))
    with session_scope(engine) as s:
        res = fm.run_study(s, cfg)
        assert res["mom_20"]["overall"]["dates"] >= 3
        ids = fm.save_studies(s, cfg, res, "test")
        row = s.get(FactorStudy, ids[0])
        assert row.data_version_hash == row.data_manifest["sha256"] and "不構成" in row.disclaimer
        assert row.result["family_size"] <= 2 and row.result["evidence_label"] == "INSUFFICIENT_SAMPLE"
    with pytest.raises(DBAPIError):
        with engine.begin() as c:
            c.execute(text("UPDATE factor_studies SET factor='x'"))


def test_multiple_testing_is_holm_and_labels_never_claim_effectiveness():
    r = lambda p, ic=0.05, later=0.05, dates=30: {  # noqa: E731
        "overall": {"ic_p_two_sided": p, "mean_ic": ic, "dates": dates}, "later_sample": {"mean_ic": later}}
    mt = fm.multiple_testing({"a": r(0.01), "b": r(0.03), "c": r(0.5)})
    assert mt["factors"]["a"]["holm_p"] == pytest.approx(0.03)
    assert mt["factors"]["b"]["holm_p"] == pytest.approx(0.06)
    assert mt["factors"]["c"]["holm_p"] == pytest.approx(0.5)
    assert fm.evidence_label(r(0.01), 0.03) == "STATISTICAL_ASSOCIATION_BEFORE_COSTS"
    assert fm.evidence_label(r(0.01), 0.06) == "NO_STATISTICAL_EVIDENCE"
    assert fm.evidence_label(r(0.01, later=-0.02), 0.03) == "UNSTABLE_ACROSS_SAMPLES"
    assert fm.evidence_label(r(0.01, dates=10), 0.03) == "INSUFFICIENT_SAMPLE"


# ------------------------------------------------------------------ regimes
def test_regime_label_only_looks_backwards():
    idx = [date(2020, 1, 1) + timedelta(days=i) for i in range(320)]
    s = pd.Series([100 + i * 0.1 + (i % 7) for i in range(320)], index=idx, dtype=float)
    full = regime_labels(s)
    cut = regime_labels(s.iloc[:290])
    assert all(full[d] == cut[d] for d in idx[:290])
    assert full[idx[50]] == "UNKNOWN" and full[idx[300]].startswith("BULL")
    assert s.iloc[-1] > 0 and regime_labels(s.iloc[:10])[idx[0]] == "UNKNOWN"


def test_regime_metrics_attribute_returns_to_the_previous_close_label():
    curve = [["2024-01-01", 100.0, 0, 0], ["2024-01-02", 110.0, 0, 0], ["2024-01-03", 99.0, 0, 0]]
    regimes = {date(2024, 1, 1): "BULL_LOW_VOL", date(2024, 1, 2): "BEAR_HIGH_VOL"}
    out = regime_metrics(curve, regimes)
    assert out["BULL_LOW_VOL"]["days"] == 1 and out["BULL_LOW_VOL"]["compounded_return"] == pytest.approx(0.10)
    assert out["BEAR_HIGH_VOL"]["compounded_return"] == pytest.approx(-0.10)


def test_regimes_are_recorded_with_the_run(engine, market):
    m = market
    with session_scope(engine) as s:
        rows = [(d, 17000 + i) for i, d in enumerate(m.dates)]
        for d, c in rows:
            ex = s.get(MarketIndex, ("TAIEX", d))
            assert ex is not None and c
        assert load_regimes(s, m.dates[-1])
        rid, _ = run_and_save(s, "ma_trend_momentum", PARAMS, _cfg(m), "IN_SAMPLE")
        assert "UNKNOWN" in s.get(BacktestRun, rid).regime_metrics  # 100 days are too few for a 200-day trend


# ------------------------------------------------------------------ promotion
def _version(s):
    return strategy_version_id(s, make("ma_trend_momentum", **PARAMS))


def _permissive(monkeypatch):
    for stage in pr.RULES:
        monkeypatch.setitem(pr.RULES[stage], "min_trades", 1)
        monkeypatch.setitem(pr.RULES[stage], "min_years", 0.0)
        monkeypatch.setitem(pr.RULES[stage], "min_sharpe", -99.0)
        if pr.RULES[stage]["min_excess_return"] is not None:
            monkeypatch.setitem(pr.RULES[stage], "min_excess_return", -99.0)


def test_clean_run_can_pass_a_gate_and_every_outcome_is_logged(engine, market, monkeypatch):
    m = market
    _permissive(monkeypatch)
    with session_scope(engine) as s:
        vid = _version(s)
        rid, _ = run_and_save(s, "ma_trend_momentum", PARAMS, _cfg(m), "IN_SAMPLE")
        assert pr.evidence_problems(s, s.get(BacktestRun, rid), vid) == []
        out = pr.review(s, vid, "BACKTESTED", rid)
        assert out["passed"] and out["stage"] == "BACKTESTED" and "not a claim" in out["statement"]
        monkeypatch.setitem(pr.RULES["OOS_REVIEWED"], "role", "test")
        fail = pr.review(s, vid, "OOS_REVIEWED", rid)  # wrong kind of run: logged, stage unchanged
        assert not fail["passed"] and fail["stage"] == "BACKTESTED"
        assert s.get(StrategyVersion, vid).stage == "BACKTESTED"
        log = s.scalars(select(StrategyStageChange.reason).order_by(StrategyStageChange.id)).all()
        assert log[-1].startswith("gate NOT met")


def test_paper_and_active_are_unreachable_and_stages_cannot_be_skipped(engine, market):
    with session_scope(engine) as s:
        vid = _version(s)
        for stage in ("PAPER", "ACTIVE"):
            with pytest.raises(ValueError):
                pr.review(s, vid, stage, 1)
        with pytest.raises(ValueError):
            pr.review(s, vid, "WF_REVIEWED", 1)
    assert not [k for k in pr.RULES if k in pr.FORBIDDEN_STAGES]


def test_scope_mismatch_is_not_evidence(engine, market):
    m = market
    with session_scope(engine) as s:
        vid = _version(s)
        rid, _ = run_and_save(s, "ma_trend_momentum", PARAMS, replace(_cfg(m), markets=("TWSE", "TPEX")), "IN_SAMPLE")
        p = pr.evidence_problems(s, s.get(BacktestRun, rid), vid)
        assert any("benchmark market scope" in x for x in p)
        out = pr.review(s, vid, "BACKTESTED", rid)
        assert out["passed"] is False and out["evidence_problems"]


def test_frozen_holding_is_not_evidence(engine, market):
    m = market
    with session_scope(engine) as s:
        vid = _version(s)
        res = run_backtest(s, make("ma_trend_momentum", **PARAMS), _cfg(m))
        res.evaluation = {"usable_for_evaluation": False, "unusable_reasons": ["1 holding(s) untradable at the end"]}
        rid = save_run(s, make("ma_trend_momentum", **PARAMS), res, "IN_SAMPLE", datetime.now(TPE))
        assert any("frozen" in x for x in pr.evidence_problems(s, s.get(BacktestRun, rid), vid))


def test_needs_rerun_and_revised_data_are_not_evidence(engine, market):
    m = market
    with session_scope(engine) as s:
        vid = _version(s)
        rid, _ = run_and_save(s, "ma_trend_momentum", PARAMS, _cfg(m), "IN_SAMPLE")
        sid = s.scalar(select(Security.id).where(Security.code == "2330"))
    quality_review.record_post_hoc_issue(engine, sid, m.dates[70], "manual finding")
    with session_scope(engine) as s:
        p = pr.evidence_problems(s, s.get(BacktestRun, rid), vid)
        assert any("NEEDS_RERUN" in x for x in p) and any("data changed" in x for x in p)
        rid2, _ = run_and_save(s, "ma_trend_momentum", PARAMS, _cfg(m), "IN_SAMPLE")
        assert pr.evidence_problems(s, s.get(BacktestRun, rid2), vid) == []
        s.execute(update(DailyPrice).where(DailyPrice.trade_date == m.dates[70], DailyPrice.security_id == sid)
                  .values(close=DailyPrice.close + 1))
        s.flush()
        assert any("data changed: daily_prices" in x or "daily_prices" in x
                   for x in pr.evidence_problems(s, s.get(BacktestRun, rid2), vid))


def test_walk_forward_summary_needs_clean_window_runs(engine, market):
    m = market
    grid = [{"top_n": 3, "lookback": 10, "rebalance": "week"}]
    with session_scope(engine) as s:
        vid = _version(s)
        wf = walk_forward(s, "ma_trend_momentum", grid, BacktestConfig(m.dates[61], m.dates[-1], initial_cash=5_000_000,
                                                                       markets=("TWSE",)), train_days=10, test_days=5)
        run = s.get(BacktestRun, wf["summary_run_id"])
        assert run.config["window_run_ids"] and run.benchmark_comparable is True
        assert pr.evidence_problems(s, run, vid) == []
        assert "excess_return_vs_benchmark" in run.metrics
        with engine.connect() as c:
            assert c.execute(text("SELECT count(*) FROM backtest_run_invalidations")).scalar() == 0
    quality_review.record_post_hoc_issue(engine, s_sid(engine), m.dates[70], "found later")
    with session_scope(engine) as s:
        p = pr.evidence_problems(s, s.get(BacktestRun, wf["summary_run_id"]), vid)
        assert any("window run" in x for x in p)


def s_sid(engine):
    with session_scope(engine) as s:
        return s.scalar(select(Security.id).where(Security.code == "2330"))
