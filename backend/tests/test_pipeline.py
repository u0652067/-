"""End-to-end PHASE 1 pipeline against an offline mock of the exchange endpoints."""
import gzip
import hashlib
from datetime import date, timedelta
from decimal import Decimal as D

import pytest
from sqlalchemy import func, select, text

from aqt.data import pipeline
from aqt.data.calendar import is_trading_day
from aqt.data.universe import build_universe
from aqt.db import session_scope
from aqt.jobs.runtime import is_safe_mode
from aqt.models import (
    DailyPrice, DataQualityIssue, DataSourceStatus, InstitutionalTrade, JobRun, MarginTrade, MarketIndex,
    RawPayload, Security, TechnicalIndicator, Valuation,
)


def _count(s, model, *where):
    return s.scalar(select(func.count()).select_from(model).where(*where))


def test_single_day_update(engine, fetcher, router, synthetic_market):
    d = synthetic_market.dates[0]
    out = pipeline.run_daily_update(engine, d, fetcher)
    assert out["TWSE_quotes"]["rows"] == 7 and out["TPEX_quotes"]["rows"] == 3
    with session_scope(engine) as s:
        assert _count(s, DailyPrice, DailyPrice.trade_date == d) == 10
        assert _count(s, InstitutionalTrade) == 6 + 3
        assert _count(s, MarginTrade) == 6 + 3
        assert _count(s, Valuation) == 6 + 3
        assert s.get(MarketIndex, ("TAIEX", d)).close == D("17853.76")
        assert is_trading_day(s, d, "TWSE") is True
        tsmc = s.scalar(select(DailyPrice).join(Security).where(Security.code == "2330"))
        assert tsmc.available_at.hour == 6 and tsmc.available_at.minute == 30  # 14:30 Taipei in UTC
        sus = s.scalar(select(DailyPrice).join(Security).where(Security.code == "1234"))
        assert sus.is_suspended and sus.close is None
        job = s.scalar(select(JobRun).where(JobRun.job_name == "daily_update"))
        assert job.status == "SUCCESS"


def test_raw_payloads_archived_verbatim(engine, fetcher, synthetic_market):
    pipeline.run_daily_update(engine, synthetic_market.dates[0], fetcher)
    with session_scope(engine) as s:
        raws = s.scalars(select(RawPayload)).all()
        assert len(raws) >= 9
        for r in raws:
            assert hashlib.sha256(gzip.decompress(r.content_gz)).hexdigest() == r.sha256
            assert r.parse_status in ("OK", "EMPTY")
        q = s.scalar(select(RawPayload).where(RawPayload.dataset == "twse_quotes"))
        assert b"SYNTHETIC TEST FIXTURE" in gzip.decompress(q.content_gz)


def test_non_trading_day_is_skipped(engine, fetcher, synthetic_market):
    holiday = date(2024, 1, 1)  # Monday; fixture market starts on Jan 2
    out = pipeline.run_daily_update(engine, holiday, fetcher)
    assert out["skipped"] == "not a trading day"
    with session_scope(engine) as s:
        assert is_trading_day(s, holiday, "TWSE") is False
        assert _count(s, DailyPrice) == 0
        assert s.scalar(select(JobRun.status)) == "SKIPPED"
    assert pipeline.run_daily_update(engine, date(2024, 1, 6), fetcher)["skipped"] == "weekend"


def test_rerun_is_idempotent(engine, fetcher, synthetic_market):
    d = synthetic_market.dates[0]
    pipeline.run_daily_update(engine, d, fetcher)
    pipeline.run_daily_update(engine, d, fetcher)
    with session_scope(engine) as s:
        assert _count(s, DailyPrice) == 10
        assert _count(s, Security) == 10


def test_backfill_indicators_limits_adjustment_universe(engine, fetcher, synthetic_market):
    m = synthetic_market
    start, end = m.dates[0], m.dates[-1]
    summary = pipeline.backfill(engine, start, end, fetcher)
    assert summary["trading_days"] == len(m.dates) and not summary["failed"]
    with session_scope(engine) as s:
        assert _count(s, DailyPrice) == 10 * len(m.dates)
        # prev_close & ±10% limits filled from the previous trading day
        tsmc = s.scalar(select(Security).where(Security.code == "2330"))
        p5 = s.get(DailyPrice, (tsmc.id, m.dates[5]))
        p4 = s.get(DailyPrice, (tsmc.id, m.dates[4]))
        assert p5.prev_close == p4.close
        assert p5.limit_down < p5.prev_close < p5.limit_up
        assert p5.limit_up <= p5.prev_close * D("1.1")
        # ex-dividend: prev_close on the ex-date is the reference price, old bars get adj_factor < 1
        hh = s.scalar(select(Security).where(Security.code == "2317"))
        ex = m.dates[70]
        bar_ex = s.get(DailyPrice, (hh.id, ex))
        bar_before = s.get(DailyPrice, (hh.id, m.dates[69]))
        assert bar_ex.prev_close == bar_before.close - D("5.00")
        expected = (bar_before.close - D("5.00")) / bar_before.close
        assert abs(bar_before.adj_factor - expected) < D("1e-8")
        assert bar_ex.adj_factor == 1
        # no false "limit breach" warning on the ex-date
        assert _count(s, DataQualityIssue, DataQualityIssue.check_name == "move_exceeds_limit",
                      DataQualityIssue.security_id == hh.id) == 0
        # indicators: MA60 only once 60 bars exist
        ti = s.get(TechnicalIndicator, (tsmc.id, end))
        assert ti.ma60 is not None and ti.rsi14 is not None and ti.k9 is not None and ti.macd_dif is not None
        assert s.get(TechnicalIndicator, (tsmc.id, m.dates[30])).ma60 is None
        closes = [float(s.get(DailyPrice, (tsmc.id, d)).close) for d in m.dates[-5:]]
        assert ti.ma5 == pytest.approx(sum(closes) / 5)
        # universe: ETF, illiquid and suspended names excluded with reasons
        u = build_universe(s, end)
        reasons = {s.get(Security, sid).code: r for sid, r in u.excluded.items()}
        assert reasons["0050"] == "not_common_stock"
        assert reasons["9999"] in ("illiquid",)
        assert {s.get(Security, sid).code for sid in u.eligible} >= {"2330", "2317", "6488"}


def test_universe_has_no_lookahead(engine, fetcher, synthetic_market):
    m = synthetic_market
    pipeline.backfill(engine, m.dates[0], m.dates[-1], fetcher)
    with session_scope(engine) as s:
        early = build_universe(s, m.dates[30])
        assert all(r == "insufficient_history" or r in ("not_common_stock", "no_trade_today")
                   for r in early.excluded.values())
        assert early.eligible == []


def test_quote_failure_enters_safe_mode_then_recovers(engine, fetcher, router, synthetic_market):
    d0, d1 = synthetic_market.dates[0], synthetic_market.dates[1]
    router.fail["/afterTrading/MI_INDEX"] = 500
    out = pipeline.run_daily_update(engine, d0, fetcher)
    assert any("TWSE quotes" in w for w in out["warnings"])
    assert is_safe_mode(engine)
    with session_scope(engine) as s:
        st = s.get(DataSourceStatus, ("twse", "twse_quotes"))
        assert st.status == "DEGRADED" and st.consecutive_failures == 1
        assert s.scalar(select(JobRun.status)) == "PARTIAL"
        assert s.execute(text("select count(*) from notifications where level='ERROR'")).scalar() >= 1
    router.fail.clear()
    pipeline.run_daily_update(engine, d1, fetcher)
    assert not is_safe_mode(engine)


def test_payload_for_wrong_date_is_rejected(engine, fetcher, router, synthetic_market):
    d0, d1 = synthetic_market.dates[0], synthetic_market.dates[1]
    router.override["/afterTrading/otc"] = synthetic_market.tpex_daily(d0)  # stale page served for d1
    pipeline.run_daily_update(engine, d1, fetcher)
    with session_scope(engine) as s:
        n = _count(s, DailyPrice, DailyPrice.trade_date == d1)
        assert n == 7  # TWSE only; TPEx rows for the wrong date were refused
        raw = s.scalar(select(RawPayload).where(RawPayload.dataset == "tpex_quotes"))
        assert raw.parse_status == "ERROR" and "answered for" in raw.parse_error
    assert is_safe_mode(engine)


def test_partial_data_triggers_row_count_drop(engine, fetcher, router, synthetic_market):
    d0, d1 = synthetic_market.dates[0], synthetic_market.dates[1]
    pipeline.run_daily_update(engine, d0, fetcher)
    partial = synthetic_market.twse_mi_index(d1)
    partial["tables"][1]["data"] = partial["tables"][1]["data"][:2]
    router.override["/afterTrading/MI_INDEX"] = partial
    pipeline.run_daily_update(engine, d1, fetcher)
    with session_scope(engine) as s:
        assert _count(s, DataQualityIssue, DataQualityIssue.check_name == "row_count_drop") == 1
    assert is_safe_mode(engine)


def test_bad_ohlc_flagged_and_excluded(engine, fetcher, router, synthetic_market):
    d = synthetic_market.dates[0]
    bad = synthetic_market.twse_mi_index(d)
    row = bad["tables"][1]["data"][0]  # 2330
    row[6], row[7] = row[7], row[6]  # swap high/low
    if row[6] == row[7]:
        pytest.skip("degenerate bar")
    router.override["/afterTrading/MI_INDEX"] = bad
    pipeline.run_daily_update(engine, d, fetcher)
    with session_scope(engine) as s:
        issue = s.scalar(select(DataQualityIssue).where(DataQualityIssue.check_name == "ohlc_inconsistent"))
        assert issue is not None and issue.detail["code"] == "2330"


def test_delisted_stock_kept_in_history(engine, fetcher, router, synthetic_market):
    """A stock that disappears mid-backfill keeps its history and remains queryable (survivorship)."""
    m = synthetic_market
    pipeline.run_daily_update(engine, m.dates[0], fetcher)
    gone = m.twse_mi_index(m.dates[1])
    gone["tables"][1]["data"] = [r for r in gone["tables"][1]["data"] if r[0] != "2603"]
    router.override["/afterTrading/MI_INDEX"] = gone
    pipeline.run_daily_update(engine, m.dates[1], fetcher)
    with session_scope(engine) as s:
        sec = s.scalar(select(Security).where(Security.code == "2603"))
        assert sec.first_seen_date == m.dates[0] and sec.last_seen_date == m.dates[0]
        assert _count(s, DailyPrice, DailyPrice.security_id == sec.id) == 1


def test_backfill_resume_only_fetches_missing(engine, fetcher, router, synthetic_market):
    d0, d1 = synthetic_market.dates[0], synthetic_market.dates[1]
    router.fail["/fund/T86"] = 403  # source refuses institutional data
    out = pipeline.backfill(engine, d0, d1, fetcher)
    assert out["missing"] == {"TWSE_institutional": 2}
    del router.fail["/fund/T86"]
    router.calls.clear()
    out = pipeline.backfill(engine, d0, d1, fetcher, only_missing=True)
    assert out["missing"] == {} and out["trading_days"] == 2
    fetched = {c.split("?")[0].rsplit("/", 1)[-1] for c in router.calls}
    assert fetched == {"T86", "TWT49U"}
    with session_scope(engine) as s:
        assert _count(s, InstitutionalTrade, InstitutionalTrade.trade_date == d1) > 0
