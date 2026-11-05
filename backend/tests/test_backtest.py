"""PHASE 2 backtest engine: known-answer fills and costs, execution constraints, point-in-time safety,
universe/restriction enforcement, records immutability, splits and walk-forward.

Market data here is SYNTHETIC (hand-built rows / the seeded random-walk factory)."""
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal as D

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from aqt.data import pipeline
from aqt.data.pit import TPE
from aqt.db import session_scope
from aqt.models import (
    BacktestRun, BacktestTrade, CorporateAction, DailyPrice, Security, TradingRestriction,
)
from aqt.research.costs import CostModel
from aqt.research.dataview import DataView, FundamentalsNotAllowed
from aqt.research.engine import BacktestConfig, Backtester, run_backtest
from aqt.research.evaluation import out_of_sample, walk_forward
from aqt.research.metrics import compute_metrics, max_drawdown
from aqt.research.records import save_run
from aqt.research.splits import chronological_split, walk_forward_windows
from aqt.research.strategies import CashBaseline, EqualWeightLiquid, MaTrendMomentum, Strategy

NO_SLIP = CostModel(slippage_tiers=((0, 0),))
START = date(2024, 1, 1)
READY = {"technical_backtest_ready": True, "fundamental_backtest_ready": False, "coverage_complete": True,
         "quality_status": "REVIEWED_WITH_EXCLUSIONS", "window": "2019-01-01..2099-12-31"}


@pytest.fixture(autouse=True)
def ready(engine):
    """The engine fails closed without a readiness record; tests start from a 'ready' database."""
    from aqt.jobs.runtime import set_state
    set_state(engine, "data_readiness", READY)


def _days(n):
    out, d = [], START
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


DAYS = _days(80)


def _make(engine, specs: dict[str, dict], status=None):
    """specs: code -> {day_index: (open, close, volume)}; days missing from a spec have no row.
    SYNTHETIC rows: constant price 100 unless overridden, high turnover so the universe admits them."""
    ids = {}
    with session_scope(engine) as s:
        for code, over in specs.items():
            sec = Security(code=code, market="TWSE", name=f"SYN{code}", security_type="STOCK",
                           status=(status or {}).get(code, "ACTIVE"))
            s.add(sec)
            s.flush()
            ids[code] = sec.id
            prev = None
            for i, d in enumerate(DAYS):
                if over.get("_last") is not None and i > over["_last"]:
                    break
                o, c, v = over.get(i, (100.0, 100.0, 1_000_000))
                lu = ld = None
                if prev is not None:
                    from aqt.data.tw_rules import price_limits
                    lu, ld = price_limits(D(str(prev)))
                s.add(DailyPrice(security_id=sec.id, trade_date=d, open=D(str(o)), high=D(str(max(o, c))),
                                 low=D(str(min(o, c))), close=D(str(c)), volume=v, turnover=int(v * c),
                                 prev_close=D(str(prev)) if prev else None, limit_up=lu, limit_down=ld,
                                 adj_factor=1, source="SYNTHETIC",
                                 available_at=datetime.combine(d, time(14, 30), tzinfo=TPE)))
                prev = c
    return ids


@dataclass
class Fixed(Strategy):
    """Test strategy: target weights on given decision days (by DAYS index)."""
    code: str = "test_fixed"
    version: str = "T1"
    schedule: dict = field(default_factory=dict)

    def is_decision_day(self, d, prev):
        return DAYS.index(d) in self.schedule

    def target_weights(self, view, holdings):
        sid_of = {s: i for s, i in self.params.get("ids", {}).items()}
        return {sid_of[c]: w for c, w in self.schedule[DAYS.index(view.as_of)].items()}


def _cfg(**kw):
    return BacktestConfig(DAYS[0], DAYS[-1], initial_cash=kw.pop("cash", 1_000_000), cost_model=kw.pop("cm", NO_SLIP),
                          **kw)


def _run(engine, ids, schedule, **kw):
    with session_scope(engine) as s:
        strat = Fixed(params={"ids": ids}, schedule=schedule)
        return run_backtest(s, strat, _cfg(**kw))


# ------------------------------------------------------------------ known answer
def test_known_answer_round_trip_with_taiwan_costs(engine):
    ids = _make(engine, {"1111": {67: (110.0, 110.0, 1_000_000), 68: (110.0, 110.0, 1_000_000),
                                  **{i: (110.0, 110.0, 1_000_000) for i in range(69, 80)}}})
    res = _run(engine, ids, {65: {"1111": 0.5}, 67: {}})
    buy, sell = [f for f in res.fills if f["type"] in ("BUY", "SELL")]
    assert (buy["date"], buy["shares"], buy["price"], buy["commission"]) == (str(DAYS[66]), 5000, "100.0", "712")
    assert (sell["date"], sell["price"], sell["commission"], sell["tax"]) == (str(DAYS[68]), "110.0", "783", "1650")
    t = res.trades[0]
    assert t["gross_pnl"] == 50_000 and t["costs"] == 712 + 783 + 1650 and t["net_pnl"] == 50_000 - 3145
    assert res.equity_curve[-1][1] == 1_000_000 + 50_000 - 3145
    assert res.metrics["trades"] == 1 and res.metrics["win_rate"] == 1.0 and res.metrics["net_of_costs"]
    assert res.metrics["costs"]["tax"] == 1650


def test_slippage_tiers_move_price_against_us_and_respect_ticks():
    cm = CostModel()
    assert cm.fill_price("BUY", D("100"), None, D("110"), D("90")) == D("101.0")  # 80 bps -> next 0.5 tick
    assert cm.fill_price("SELL", D("100"), 2e9, D("110"), D("90")) == D("99.9")  # 5 bps -> 99.95, floored to the 0.1 tick below 100
    assert cm.fill_price("BUY", D("109.8"), None, D("110"), D("90")) == D("110")  # never beyond the limit
    assert cm.commission(D("1000")) == 20 and cm.round_lots(1999) == 1000


def test_limit_up_at_open_blocks_buy_and_limit_down_blocks_sell(engine):
    ids = _make(engine, {"2222": {66: (110.0, 110.0, 1_000_000), 69: (99.0, 99.0, 1_000_000),
                                  70: (89.1, 89.1, 1_000_000)}})
    res = _run(engine, ids, {65: {"2222": 0.5}})
    assert [r["reason"] for r in res.rejections] == ["limit_up_at_open"] and not res.trades
    res = _run(engine, {**ids}, {62: {"2222": 0.5}, 69: {}})
    reasons = [r["reason"] for r in res.rejections]
    assert "limit_down_at_open" in reasons  # day 70 opens at the -10% limit: the exit waits
    sells = [f for f in res.fills if f["type"] == "SELL"]
    assert sells[0]["date"] == str(DAYS[71])  # retried and filled the next day


def test_volume_participation_cap_uses_history_known_at_decision(engine):
    # 200,000 shares/day on the 20 days up to the decision day: 5% = 10,000 shares (the order is 50,000)
    ids = _make(engine, {"3333": {i: (100.0, 100.0, 200_000) for i in range(46, 66)}})
    res = _run(engine, ids, {65: {"3333": 0.5}}, cash=10_000_000)
    buy = next(f for f in res.fills if f["type"] == "BUY")
    assert buy["shares"] == 10_000 and buy["partial_of"] == 50_000 and buy["date"] == str(DAYS[66])
    assert all(f["shares"] % 1000 == 0 for f in res.fills if f["type"] == "BUY")


def test_fill_day_volume_never_changes_the_open_fill(engine):
    """The fill day's full-day volume is only known after the close: it must not size or admit an order."""
    ids = _make(engine, {"3434": {66: (100.0, 100.0, 1_000_000)}})
    base = _run(engine, ids, {65: {"3434": 0.5}})
    with session_scope(engine) as s:
        for v in (0, 1, 10_000_000):  # T+1 full-day volume: none, tiny, huge
            s.execute(update(DailyPrice).where(DailyPrice.security_id == ids["3434"],
                                               DailyPrice.trade_date == DAYS[66]).values(volume=v, turnover=v * 100))
            s.flush()
            r = run_backtest(s, Fixed(params={"ids": ids}, schedule={65: {"3434": 0.5}}), _cfg())
            assert [f for f in r.fills if f["type"] == "BUY"] == [f for f in base.fills if f["type"] == "BUY"]


# ------------------------------------------------------------------ universe / restrictions / delisting
def test_restricted_or_ineligible_stocks_are_never_bought(engine):
    ids = _make(engine, {"4444": {}, "5555": {}, "6666": {}})
    with session_scope(engine) as s:
        # disposition known on decision day 65 -> excluded from the universe
        s.add(TradingRestriction(security_id=ids["4444"], restriction_type="DISPOSITION", start_date=DAYS[60],
                                 end_date=DAYS[70], announced_date=DAYS[59], source="SYNTHETIC",
                                 available_at=datetime.combine(DAYS[59], time(18), tzinfo=TPE)))
        # daily full-delivery list for the fill day only (published that morning)
        s.add(TradingRestriction(security_id=ids["5555"], restriction_type="ALTERED_TRADING", start_date=DAYS[66],
                                 end_date=DAYS[66], announced_date=DAYS[66], source="SYNTHETIC",
                                 available_at=datetime.combine(DAYS[66], time(8), tzinfo=TPE)))
    res = _run(engine, ids, {65: {"4444": 0.3, "5555": 0.3, "6666": 0.3}})
    by = {(r["sid"], r["reason"]) for r in res.rejections}
    assert (ids["4444"], "not_in_universe") in by
    assert (ids["5555"], "restricted:altered_trading_full_delivery") in by
    assert {f["sid"] for f in res.fills if f["type"] == "BUY"} == {ids["6666"]}


def test_untradable_holding_is_never_exited_from_hindsight_and_run_is_unusable(engine):
    """No exit from 'no later quotes' / Security.status: the holding freezes and the run is flagged."""
    ids = _make(engine, {"7777": {"_last": 70, 70: (95.0, 95.0, 1_000_000)}, "8888": {}}, {"7777": "DELISTED"})
    res = _run(engine, ids, {65: {"7777": 0.4}, 72: {}})  # tries to sell after the quotes stop
    sid = ids["7777"]
    assert not any(f["type"] == "SELL" and f["sid"] == sid for f in res.fills)
    assert any(r["sid"] == sid and r["reason"] == "no_open_on_fill_day" for r in res.rejections)
    t = next(t for t in res.trades if t["sid"] == sid)
    assert t["exit_reason"] == "end_of_test_mark"  # valued, not traded
    ev = res.evaluation
    assert ev["usable_for_evaluation"] is False and res.metrics["usable_for_evaluation"] is False
    h = next(x for x in ev["untradable_holdings"] if x["sid"] == sid)
    assert h["from"] == str(DAYS[71]) and h["resolved"] is False
    # the same run without the stale holding is usable
    assert _run(engine, ids, {65: {"8888": 0.4}}).evaluation["usable_for_evaluation"] is True


def test_cash_dividend_preserves_value_through_ex_date(engine):
    ids = _make(engine, {"9999": {70: (95.0, 95.0, 1_000_000), **{i: (95.0, 95.0, 1_000_000) for i in range(71, 80)}}})
    with session_scope(engine) as s:
        s.add(CorporateAction(security_id=ids["9999"], ex_date=DAYS[70], action_type="DIV", prev_close=D("100"),
                              ref_price=D("95"), cash_dividend=D("5"), source="SYNTHETIC",
                              available_at=datetime.combine(DAYS[70], time(8), tzinfo=TPE)))
    res = _run(engine, ids, {65: {"9999": 0.5}})
    eq = {d: e for d, e, _, _ in res.equity_curve}
    assert eq[str(DAYS[70])] == pytest.approx(eq[str(DAYS[69])], abs=1)  # no artificial 5% loss
    assert any(f["type"] == "CORPORATE_ACTION" for f in res.fills)


def test_corporate_action_published_after_the_period_cannot_change_results(engine):
    ids = _make(engine, {"9191": {**{i: (95.0, 95.0, 1_000_000) for i in range(70, 80)}}})
    strat = Fixed(params={"ids": ids}, schedule={65: {"9191": 0.5}})
    cfg = BacktestConfig(DAYS[0], DAYS[75], initial_cash=1_000_000, cost_model=NO_SLIP)
    with session_scope(engine) as s:
        before = run_backtest(s, strat, cfg)
    with session_scope(engine) as s:  # ex-date inside the period, but only published after it ended
        s.add(CorporateAction(security_id=ids["9191"], ex_date=DAYS[70], action_type="DIV", prev_close=D("100"),
                              ref_price=D("95"), source="SYNTHETIC",
                              available_at=datetime.combine(DAYS[77], time(8), tzinfo=TPE)))
    with session_scope(engine) as s:
        after = run_backtest(s, strat, cfg)
        assert after.results_hash() == before.results_hash()
        h = DataView(s, DAYS[75]).history([ids["9191"]], 30)
        assert h[h.d < DAYS[70]].adj_close.iloc[-1] == pytest.approx(100.0)  # no adjustment from the future
    with session_scope(engine) as s:  # once public, the adjustment applies (and is visible to the view)
        later = run_backtest(s, strat, BacktestConfig(DAYS[0], DAYS[79], initial_cash=1_000_000, cost_model=NO_SLIP))
        assert any(f["type"] == "CORPORATE_ACTION" and f["date"] == str(DAYS[77]) for f in later.fills)
        h = DataView(s, DAYS[78]).history([ids["9191"]], 30)
        assert h[h.d < DAYS[70]].adj_close.iloc[-1] == pytest.approx(95.0)


# ------------------------------------------------------------------ what was known when
def test_blocked_securities_needs_an_explicit_aware_known_at(engine):
    from aqt.data.tradability import blocked_securities
    with session_scope(engine) as s:
        with pytest.raises(TypeError):
            blocked_securities(s, DAYS[10])
        with pytest.raises(ValueError):
            blocked_securities(s, DAYS[10], datetime(2024, 1, 15, 9, 0))


def test_restriction_announced_after_the_open_cannot_block_that_open(engine):
    """T+1 18:00 disposition: unknown at T+1 09:00 — the fill stands; the old 23:59 cut-off blocked it."""
    from aqt.data.tradability import blocked_securities
    ids = _make(engine, {"2626": {}})
    t1 = DAYS[66]
    with session_scope(engine) as s:
        s.add(TradingRestriction(security_id=ids["2626"], restriction_type="DISPOSITION", start_date=t1,
                                 end_date=DAYS[70], announced_date=t1, source="SYNTHETIC",
                                 available_at=datetime.combine(t1, time(18), tzinfo=TPE)))
    res = _run(engine, ids, {65: {"2626": 0.5}})
    assert [f["date"] for f in res.fills if f["type"] == "BUY"] == [str(t1)] and not res.rejections
    with session_scope(engine) as s:
        at_open = datetime.combine(t1, time(9), tzinfo=TPE)
        evening = datetime.combine(t1, time(18), tzinfo=TPE)
        assert ids["2626"] not in blocked_securities(s, t1, at_open, include_upcoming=False)
        assert blocked_securities(s, t1, evening, include_upcoming=False)[ids["2626"]] == "disposition"


@pytest.mark.parametrize("kind,reason", [("ALTERED_TRADING", "altered_trading_full_delivery"),
                                         ("STOP_TRADING", "stop_trading")])
def test_restriction_known_at_0800_blocks_the_open_fill(engine, kind, reason):
    ids = _make(engine, {"2727": {}})
    t1 = DAYS[66]
    with session_scope(engine) as s:
        s.add(TradingRestriction(security_id=ids["2727"], restriction_type=kind, start_date=t1, end_date=t1,
                                 announced_date=t1, source="SYNTHETIC",
                                 available_at=datetime.combine(t1, time(8), tzinfo=TPE)))
    res = _run(engine, ids, {65: {"2727": 0.5}})
    assert not [f for f in res.fills if f["type"] == "BUY"]
    assert [r["reason"] for r in res.rejections] == [f"restricted:{reason}"]


def test_anomaly_found_after_as_of_cannot_change_that_backtest_but_flags_stored_runs(engine):
    from aqt.data import quality_review as qr
    from aqt.data.universe import build_universe
    from aqt.models import DataQualityIssue
    from aqt.research.fingerprint import data_fingerprint
    from aqt.research.records import run_validity
    ids = _make(engine, {"2828": {}})
    sid = ids["2828"]
    strat = Fixed(params={"ids": ids}, schedule={65: {"2828": 0.5}})
    discovered = datetime.combine(DAYS[79] + timedelta(days=30), time(10), tzinfo=TPE)
    with session_scope(engine) as s:
        before = run_backtest(s, strat, _cfg())
        rid = save_run(s, strat, before, "IN_SAMPLE", datetime.now(TPE))
        fp0 = data_fingerprint(s, DAYS[0], DAYS[-1])
        assert run_validity(s, rid)["status"] == "VALID"
    out = qr.record_post_hoc_issue(engine, sid, DAYS[64], "vendor correction found by manual review",
                                   discovered_at=discovered)
    assert out["runs_flagged"] == [rid]
    with session_scope(engine) as s:
        issue = s.get(DataQualityIssue, out["issue_id"])
        assert issue.effective_at == discovered == issue.known_at and issue.detection_method == "POST_HOC_MANUAL"
        assert issue.blocks_backtest and issue.created_at is not None
        # the historical universe and the replayed backtest did not change...
        assert sid in build_universe(s, DAYS[65]).eligible
        assert run_backtest(s, strat, _cfg()).results_hash() == before.results_hash()
        # ...the run is flagged, the data fingerprint moved, and from the discovery on it is excluded
        v = run_validity(s, rid)
        assert v["status"] == "NEEDS_RERUN" and v["flags"][0]["issue_id"] == out["issue_id"]
        assert data_fingerprint(s, DAYS[0], DAYS[-1])["sha256"] != fp0["sha256"]
        assert s.get(BacktestRun, rid).results_hash == before.results_hash()  # the record itself is untouched
        assert sid in {k for k, r in build_universe(s, DAYS[79], known_at=discovered).excluded.items()
                       if r == "unexplained_price_anomaly"}
    with pytest.raises(DBAPIError):
        with engine.begin() as c:
            c.execute(text("UPDATE backtest_run_invalidations SET status='CLEARED'"))


def test_rule_issue_is_effective_from_its_check_time_only(engine):
    from aqt.data.quality import Issue, save_issues
    from aqt.data.tradability import blocked_securities
    from aqt.models import DataQualityIssue
    ids = _make(engine, {"2929": {}})
    d = DAYS[64]
    with session_scope(engine) as s:
        save_issues(s, [Issue("move_exceeds_limit", "WARN", "daily_prices", ids["2929"], d, {})])
    with session_scope(engine) as s:
        issue = s.scalar(select(DataQualityIssue))
        assert issue.detection_method == "AUTO_RULE"
        issue.blocks_backtest = True
    with session_scope(engine) as s:
        assert issue.effective_at == datetime.combine(d, time(17, 45), tzinfo=TPE)
        assert ids["2929"] not in blocked_securities(s, d, datetime.combine(d, time(9), tzinfo=TPE))
        assert ids["2929"] in blocked_securities(s, d, datetime.combine(d, time(18), tzinfo=TPE))


# ------------------------------------------------------------------ point in time
def test_dataview_never_sees_future_or_unpublished_rows(engine):
    ids = _make(engine, {"1212": {}})
    sid = ids["1212"]
    as_of = DAYS[50]
    with session_scope(engine) as s:
        # a later corporate action changed the stored backward factors of every earlier row
        s.execute(update(DailyPrice).where(DailyPrice.security_id == sid, DailyPrice.trade_date <= DAYS[60])
                  .values(adj_factor=D("0.5")))
        # a row of as_of that was published only after the decision time
        late = s.get(DailyPrice, (sid, as_of))
        late.available_at = datetime.combine(as_of, time(23), tzinfo=TPE)
    with session_scope(engine) as s:
        h = DataView(s, as_of).history([sid], 30)
        assert h.d.max() == DAYS[49]  # as_of row not yet available at 18:00, nothing later either
        assert h.adj_close.iloc[-1] == pytest.approx(h.close.iloc[-1])  # re-based to the latest visible row


def test_future_data_cannot_change_past_results(engine, fetcher, synthetic_market):
    m = synthetic_market
    pipeline.backfill(engine, m.dates[0], m.dates[-1], fetcher)
    cut = m.dates[85]
    strat = MaTrendMomentum(params={"top_n": 3, "lookback": 10, "rebalance": "week"})
    cfg = BacktestConfig(m.dates[61], cut, initial_cash=5_000_000)
    with session_scope(engine) as s:
        res = run_backtest(s, strat, cfg)
        before = res.results_hash()
    assert any(f["type"] == "BUY" for f in res.fills)  # the comparison is about real decisions
    with session_scope(engine) as s:  # scramble everything after the cut-off
        s.execute(update(DailyPrice).where(DailyPrice.trade_date > cut)
                  .values(close=DailyPrice.close * 3, open=DailyPrice.open * 3, volume=1))
    with session_scope(engine) as s:
        after = run_backtest(s, strat, cfg).results_hash()
    assert before == after


def test_fundamentals_are_refused_in_technical_backtests(engine):
    ids = _make(engine, {"1313": {}})
    with session_scope(engine) as s:
        with pytest.raises(FundamentalsNotAllowed):
            DataView(s, DAYS[50]).fundamentals([ids["1313"]])
        with pytest.raises(FundamentalsNotAllowed):  # fundamental_backtest_ready is not true
            Backtester(s, CashBaseline(), _cfg(allow_fundamentals=True))


def test_engine_fails_closed_without_explicit_readiness(engine):
    from aqt.jobs.runtime import set_state
    from aqt.research.engine import DataNotReady
    _make(engine, {"1515": {}})
    with engine.begin() as c:
        c.execute(text("DELETE FROM system_state WHERE key='data_readiness'"))
    with session_scope(engine) as s:
        with pytest.raises(DataNotReady, match="missing"):
            Backtester(s, CashBaseline(), _cfg())
    for bad in ({**READY, "technical_backtest_ready": False}, {**READY, "technical_backtest_ready": "true"},
                {**READY, "technical_backtest_ready": None}, {**READY, "quality_status": "UNREVIEWED"},
                {**READY, "coverage_complete": False}, {k: v for k, v in READY.items() if k != "quality_status"}):
        set_state(engine, "data_readiness", bad)
        with session_scope(engine) as s:
            with pytest.raises(DataNotReady):
                Backtester(s, CashBaseline(), _cfg())
    set_state(engine, "data_readiness", READY)
    with session_scope(engine) as s:
        res = run_backtest(s, CashBaseline(), _cfg())
        assert res.config["data_readiness_at_run"]["fundamental_backtest_ready"] is False
        with pytest.raises(FundamentalsNotAllowed):
            Backtester(s, CashBaseline(), _cfg(allow_fundamentals=True))


# ------------------------------------------------------------------ records
def test_records_are_append_only_reproducible_and_versioned(engine):
    ids = _make(engine, {"1414": {**{i: (100.0 + i - 60, 100.0 + i - 60, 1_000_000) for i in range(61, 80)}}})
    strat = Fixed(params={"ids": ids}, schedule={62: {"1414": 0.5}, 70: {}})
    with session_scope(engine) as s:
        r1 = run_backtest(s, strat, _cfg())
        r2 = run_backtest(s, strat, _cfg())
        assert r1.results_hash() == r2.results_hash()  # deterministic
        rid = save_run(s, strat, r1, "IN_SAMPLE", datetime.now(TPE))
        row = s.get(BacktestRun, rid)
        assert row.results_hash == r1.results_hash() and row.params_hash == strat.params_hash()
        assert row.data_version["fingerprint_sha256"] == row.data_version_hash and len(row.data_version_hash) == 64
        assert row.data_manifest["manifest"]["daily_prices"]
        assert "不構成" in row.disclaimer and row.engine_version
        assert s.scalar(select(BacktestTrade.net_pnl).where(BacktestTrade.backtest_run_id == rid)) is not None
    for stmt in (f"UPDATE backtest_runs SET metrics='{{}}' WHERE id={rid}",
                 f"DELETE FROM backtest_trades WHERE backtest_run_id={rid}",
                 f"DELETE FROM backtest_runs WHERE id={rid}"):
        with pytest.raises(DBAPIError):
            with engine.begin() as c:
                c.execute(text(stmt))
    changed = Fixed(code=strat.code, version=strat.version, params={"ids": ids}, schedule=strat.schedule)
    with session_scope(engine) as s:
        save_run(s, changed, r1, "IN_SAMPLE", datetime.now(TPE))  # same params -> same version row
    with session_scope(engine) as s:
        n = s.scalar(text("SELECT count(*) FROM strategy_versions"))
    assert n == 1


def test_data_fingerprint_changes_when_one_historical_value_changes(engine):
    from aqt.research.fingerprint import data_fingerprint
    ids = _make(engine, {"1616": {}, "1717": {}})
    with session_scope(engine) as s:
        s.add(TradingRestriction(security_id=ids["1717"], restriction_type="DISPOSITION", start_date=DAYS[30],
                                 end_date=DAYS[35], announced_date=DAYS[29], source="SYNTHETIC",
                                 available_at=datetime.combine(DAYS[29], time(18), tzinfo=TPE)))
    fp = lambda: data_fingerprint(session, DAYS[50], DAYS[79])  # noqa: E731
    with session_scope(engine) as session:
        f0 = fp()
        assert f0 == fp() and f0["manifest"]["daily_prices"]  # deterministic, per-month manifest
    with session_scope(engine) as session:  # one historical close, one cent
        session.execute(update(DailyPrice).where(DailyPrice.security_id == ids["1616"],
                                                 DailyPrice.trade_date == DAYS[10]).values(close=D("100.01")))
    with session_scope(engine) as session:
        f1 = fp()
        assert f1["sha256"] != f0["sha256"]
        changed = [m for m, h in f1["manifest"]["daily_prices"].items() if f0["manifest"]["daily_prices"][m] != h]
        assert changed == [DAYS[10].strftime("%Y-%m")]
    with session_scope(engine) as session:  # one restriction end date
        session.execute(update(TradingRestriction).values(end_date=DAYS[36]))
    with session_scope(engine) as session:
        f2 = fp()
        assert f2["sha256"] != f1["sha256"] and f2["manifest"]["daily_prices"] == f1["manifest"]["daily_prices"]


def test_dividend_mode_and_benchmark_are_recorded_and_never_mixed(engine):
    from aqt.models import MarketIndex
    ids = _make(engine, {"1818": {}})
    with session_scope(engine) as s:  # only the PRICE index exists: no excess return may be computed against it
        for i, d in enumerate(DAYS):
            s.add(MarketIndex(index_code="TAIEX", trade_date=d, name="SYN", close=D(str(10000 + i)), source="SYNTHETIC",
                              available_at=datetime.combine(d, time(14, 30), tzinfo=TPE)))
    strat = Fixed(params={"ids": ids}, schedule={65: {"1818": 0.5}})
    with session_scope(engine) as s:
        res = run_backtest(s, strat, _cfg())
        assert res.config["dividend_mode"] == "TOTAL_RETURN_REINVEST" and res.config["benchmark_kind"] == "total_return"
        assert "benchmark_total_return" not in res.metrics and "excess_return_vs_benchmark" not in res.metrics
        assert res.benchmark["price_index_reference"]["comparable"] is False
        rid = save_run(s, strat, res, "IN_SAMPLE", datetime.now(TPE))
        row = s.get(BacktestRun, rid)
        assert row.dividend_mode == "TOTAL_RETURN_REINVEST" and row.benchmark_kind == "total_return"
        assert row.usable_for_evaluation is True and row.data_manifest["sha256"] == row.data_version_hash
    with pytest.raises(ValueError):
        BacktestConfig(DAYS[0], DAYS[-1], dividend_mode="CASH")


# ------------------------------------------------------------------ splits / walk-forward / metrics
@pytest.mark.parametrize("code,params,cut", [
    ("ma_trend_momentum", {"top_n": 3, "lookback": 10, "rebalance": "week"}, "midweek"),
    ("ew_liquid_baseline", {"top_n": 3, "rebalance": "month"}, "midmonth")])
def test_window_boundary_does_not_move_the_rebalance_calendar(engine, fetcher, synthetic_market, code, params, cut):
    """Same strategy over the same period, split or not: same decision days, fills and equity."""
    from dataclasses import replace

    from aqt.research.strategies import make
    m = synthetic_market
    pipeline.backfill(engine, m.dates[0], m.dates[-1], fetcher)
    tail = m.dates[61:]
    if cut == "midweek":  # a Wednesday: the first day of the second window is mid-week
        c = next(i for i, d in enumerate(tail) if d.weekday() == 2 and i > 3)
    else:  # the 15th or so: the first day of the second window is mid-month
        c = next(i for i, d in enumerate(tail) if 12 <= d.day <= 18 and i > 3)
    base = BacktestConfig(tail[0], tail[-1], initial_cash=5_000_000)
    with session_scope(engine) as s:
        whole = run_backtest(s, make(code, **params), base)
        first = run_backtest(s, make(code, **params), replace(base, end=tail[c], terminal="CARRY"))
        assert first.final_state.last_trading_day == tail[c]
        second = run_backtest(s, make(code, **params), replace(base, start=tail[c + 1]), first.final_state)
    assert tail[c + 1].isoformat() not in second.rebalance_days or tail[c + 1].isoformat() in whole.rebalance_days
    assert first.rebalance_days + second.rebalance_days == whole.rebalance_days and whole.rebalance_days
    key = lambda fs: [f for f in fs if f["type"] != "CORPORATE_ACTION"]  # noqa: E731
    assert key(first.fills) + key(second.fills) == key(whole.fills)
    assert (first.equity_curve + second.equity_curve) == whole.equity_curve


def test_benchmark_is_compared_only_inside_its_market_scope(engine):
    from aqt.models import MarketIndex
    ids = _make(engine, {"3131": {}})
    with session_scope(engine) as s:
        s.execute(update(Security).where(Security.id == ids["3131"]).values(market="TWSE"))
        tpex = Security(code="3232", market="TPEX", name="SYN", security_type="STOCK", status="ACTIVE")
        s.add(tpex)
        for i, d in enumerate(DAYS):
            s.add(MarketIndex(index_code="TWSE:發行量加權股價報酬指數", trade_date=d, name="SYN",
                              close=D(str(20000 + i)), source="SYNTHETIC",
                              available_at=datetime.combine(d, time(14, 30), tzinfo=TPE)))
    strat = Fixed(params={"ids": ids}, schedule={65: {"3131": 0.5}})
    with session_scope(engine) as s:
        both = run_backtest(s, strat, _cfg())  # default universe: listed + OTC, index: listed only
        assert both.metrics["benchmark_comparable"] is False and both.benchmark["comparable"] is False
        for k in ("excess_return_vs_benchmark", "benchmark_total_return", "benchmark_max_drawdown"):
            assert k not in both.metrics
        assert both.benchmark["curve"] == [] and "scope" in both.benchmark["not_comparable_reason"]
        rid = save_run(s, strat, both, "IN_SAMPLE", datetime.now(TPE))
        assert s.get(BacktestRun, rid).benchmark_comparable is False
        twse = run_backtest(s, strat, _cfg(markets=("TWSE",)))
        assert twse.metrics["benchmark_comparable"] is True and "excess_return_vs_benchmark" in twse.metrics
        assert twse.config["markets"] == ["TWSE"]


def test_market_scope_filters_the_universe(engine):
    ids = _make(engine, {"3333": {}})
    with session_scope(engine) as s:
        s.execute(update(Security).where(Security.id == ids["3333"]).values(market="TPEX"))
    with session_scope(engine) as s:
        assert ids["3333"] in DataView(s, DAYS[65]).universe().eligible
        u = DataView(s, DAYS[65], markets=("TWSE",)).universe()
        assert ids["3333"] not in u.eligible and u.excluded[ids["3333"]] == "outside_market_scope"


def test_chronological_split_and_walk_forward_windows_never_look_back():
    sp = chronological_split(DAYS, 0.6, 0.2)
    assert sp["train"][1] < sp["validation"][0] <= sp["validation"][1] < sp["test"][0]
    ws = walk_forward_windows(DAYS, 30, 10)
    assert len(ws) == 5
    for a, b in zip(ws, ws[1:]):
        assert a.test_end < b.test_start  # test windows tile without overlap
    assert all(w.train_end < w.test_start for w in ws)
    with pytest.raises(ValueError):
        walk_forward_windows(DAYS, 30, 10, step=5)


def test_walk_forward_and_oos_end_to_end(engine, fetcher, synthetic_market):
    m = synthetic_market
    pipeline.backfill(engine, m.dates[0], m.dates[-1], fetcher)
    grid = [{"top_n": 2, "lookback": 5}, {"top_n": 3, "lookback": 10}]
    base = BacktestConfig(m.dates[60], m.dates[-1], initial_cash=5_000_000)
    with session_scope(engine) as s:
        wf = walk_forward(s, "ma_trend_momentum", grid, base, train_days=15, test_days=5)
        oos = out_of_sample(s, "ma_trend_momentum", grid, base)
    assert wf["windows"] and all(w["selected_params"] in grid for w in wf["windows"])
    ws = wf["windows"]
    assert ws[0]["start_equity"] == 5_000_000
    for a, b in zip(ws, ws[1:]):  # each window starts from the previous actual end, no rescaling
        assert b["start_equity"] == pytest.approx(a["end_equity"], abs=1e-6)
    assert wf["oos_metrics"]["final_equity"] == pytest.approx(ws[-1]["end_equity"], abs=0.01)
    for k in ("commission", "tax", "slippage_vs_open", "end_mark_selling_costs"):
        assert wf["costs_total"][k] == pytest.approx(sum(w["costs"][k] for w in ws), abs=1e-6)
    assert wf["oos_metrics"]["costs"]["commission"] == pytest.approx(wf["costs_total"]["commission"])
    assert sum(1 for w in ws[:-1] if w["costs"]["end_mark_selling_costs"]) == 0  # only the last window marks
    assert wf["rebalance_days"] == [d for w in ws for d in w["rebalance_days"]] and wf["rebalance_days"]
    assert oos["selected_params"] in grid and "test" in oos and "validation" in oos
    with session_scope(engine) as s:
        summary = s.get(BacktestRun, wf["summary_run_id"])
        assert summary.kind == "WALK_FORWARD" and summary.config["child_run_ids"]
        kinds = {k for (k,) in s.execute(select(BacktestRun.kind))}
        assert kinds == {"IN_SAMPLE", "OUT_OF_SAMPLE", "WALK_FORWARD"}


def test_metrics_known_values_and_losers_counted():
    curve = [["2024-01-01", 100.0, 0, 1], ["2024-01-02", 110.0, 0, 1], ["2024-01-03", 99.0, 0, 1],
             ["2024-01-04", 121.0, 0, 1]]
    trades = [{"net_pnl": 30.0, "gross_pnl": 32.0}, {"net_pnl": -10.0, "gross_pnl": -8.0},
              {"net_pnl": 0.0, "gross_pnl": 1.0}]
    m = compute_metrics(curve, trades, [], 100.0)
    assert m["total_return"] == pytest.approx(0.21)
    assert m["max_drawdown"] == pytest.approx(99 / 110 - 1) and m["max_drawdown_period"] == ["2024-01-02", "2024-01-03"]
    assert m["win_rate"] == pytest.approx(1 / 3) and m["avg_loss"] == pytest.approx(-5.0)
    assert m["expectancy_per_trade"] == pytest.approx(20 / 3) and m["profit_factor"] == pytest.approx(3.0)
    assert max_drawdown([1, 2, 1, 3])[0] == pytest.approx(-0.5)


def test_baselines_run_and_cash_baseline_is_flat(engine, fetcher, synthetic_market):
    m = synthetic_market
    pipeline.backfill(engine, m.dates[0], m.dates[-1], fetcher)
    cfg = BacktestConfig(m.dates[61], m.dates[-1], initial_cash=5_000_000)
    with session_scope(engine) as s:
        cash = run_backtest(s, CashBaseline(), cfg)
        ew = run_backtest(s, EqualWeightLiquid(params={"top_n": 3, "rebalance": "month"}), cfg)
    assert {e for _, e, _, _ in cash.equity_curve} == {5_000_000.0} and not cash.fills
    assert ew.metrics["trades"] >= 1 and ew.metrics["costs"]["commission"] > 0
    assert all(f["sid"] in _eligible_any(engine, m) for f in ew.fills if f["type"] == "BUY")


def _eligible_any(engine, m):
    from aqt.data.universe import build_universe
    out = set()
    with session_scope(engine) as s:
        for d in m.dates[61:]:
            out |= set(build_universe(s, d).eligible)
    return out
