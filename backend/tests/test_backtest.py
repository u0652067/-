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
    return BacktestConfig(DAYS[0], DAYS[-1], initial_cash=1_000_000, cost_model=kw.pop("cm", NO_SLIP), **kw)


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


def test_volume_participation_cap_and_board_lots(engine):
    ids = _make(engine, {"3333": {66: (100.0, 100.0, 20_000)}})  # 5% of 20,000 = 1,000 shares
    res = _run(engine, ids, {65: {"3333": 0.5}})
    buy = next(f for f in res.fills if f["type"] == "BUY")
    assert buy["shares"] == 1000 and buy["partial_of"] == 5000
    assert all(f["shares"] % 1000 == 0 for f in res.fills if f["type"] == "BUY")


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


def test_delisted_holding_is_liquidated_at_last_close(engine):
    ids = _make(engine, {"7777": {"_last": 70, 70: (95.0, 95.0, 1_000_000)}, "8888": {}}, {"7777": "DELISTED"})
    res = _run(engine, ids, {65: {"7777": 0.4}})
    t = next(t for t in res.trades if t["sid"] == ids["7777"])
    assert t["exit_reason"] == "delisted" and t["exit_price"] == 95.0 and t["exit_date"] == str(DAYS[71])


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


def test_engine_refuses_when_integrity_says_technical_not_ready(engine):
    from aqt.jobs.runtime import set_state
    from aqt.research.engine import DataNotReady
    _make(engine, {"1515": {}})
    set_state(engine, "data_readiness", {"technical_backtest_ready": False, "fundamental_backtest_ready": False,
                                         "quality_status": "UNREVIEWED"})
    with session_scope(engine) as s:
        with pytest.raises(DataNotReady):
            Backtester(s, CashBaseline(), _cfg())
    set_state(engine, "data_readiness", {"technical_backtest_ready": True, "fundamental_backtest_ready": False})
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
        assert row.data_version["daily_prices"]["rows"] > 0 and len(row.data_version_hash) == 64
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


# ------------------------------------------------------------------ splits / walk-forward / metrics
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
