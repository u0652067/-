"""PHASE 1 gap fixes: G4/G8 corporate events, G6 restrictions, G7 financial publication time,
G13 anomaly classification, integrity coverage-vs-quality.

Parsers are tested on REAL trimmed official responses (tests/fixtures/real); pipeline behaviour on
the SYNTHETIC market."""
from datetime import date, datetime, timedelta
from decimal import Decimal as D

import httpx
import pytest
from sqlalchemy import select

from aqt.data import events, pipeline, quality_review
from aqt.data.fundamentals import latest_statements_as_of
from aqt.data.http import Fetcher
from aqt.data.integrity import run_integrity_check
from aqt.data.pit import TPE
from aqt.data.sources import corporate_events as ce
from aqt.data.sources import mops_history as mh
from aqt.data.sources import restrictions as rs
from aqt.data.tradability import blocked_securities
from aqt.data.universe import build_universe
from aqt.db import session_scope
from aqt.models import CorporateAction, DailyPrice, DataQualityIssue, FinancialStatement, Security, TradingRestriction
from tests.conftest import Router, load_fixture
from tests.fixtures.factory import FIXTURE_NOTE, Market


def _sid(s, code, market):
    return s.scalar(select(Security.id).where(Security.code == code, Security.market == market))


# ------------------------------------------------------------------ parsers on real responses
def test_corporate_event_parsers_real_payloads():
    r = ce.parse_tpex_ex_rights(load_fixture("real/tpex_exdailyq_2019.json")).records
    x = next(a for a in r if a.code == "4556" and a.ex_date == date(2019, 7, 15))
    assert (x.action_type, x.prev_close, x.ref_price, x.cash_dividend) == ("DIV", D("33.10"), D("31.85"), D("1.25"))
    cr = ce.parse_capital_reduction(load_fixture("real/twse_twtauu_2024.json")).records
    assert any(a.code == "3432" and a.ex_date == date(2024, 1, 22) and a.ref_price == D("19.69")
               and a.action_type == "CAPITAL_REDUCTION" for a in cr)
    cr2 = ce.parse_capital_reduction(load_fixture("real/tpex_revivt_2024.json")).records
    assert any(a.code == "3064" and a.ex_date == date(2024, 2, 5) and a.prev_close == D("10.65")
               and a.ref_price == D("35.50") for a in cr2)
    pv = ce.parse_par_change(load_fixture("real/tpex_pvchgrslt_2019_2026.json")).records
    assert any(a.code == "6548" and a.ex_date == date(2019, 9, 9) and a.prev_close == D("312.00")
               and a.ref_price == D("31.20") and a.action_type == "PAR_CHANGE" for a in pv)
    assert ce.parse_par_change(load_fixture("real/twse_twtb8u_2024.json")).records[0].ref_price == D("29.40")
    sp = {a.code: a for a in ce.parse_etf_split(load_fixture("real/twse_twtcau_2019_2026.json")).records}
    assert sp["0050"].action_type == "SPLIT" and sp["0050"].ref_price == D("47.16")
    assert sp["00632R"].action_type == "REVERSE_SPLIT"


def test_restriction_parsers_real_payloads():
    p = rs.parse_disposition(load_fixture("real/twse_punish_202401.json")).records
    x = next(a for a in p if a.code == "8467")
    assert (x.start_date, x.end_date, x.announced_date) == (date(2024, 1, 12), date(2024, 1, 29), date(2024, 1, 11))
    t = rs.parse_disposition(load_fixture("real/tpex_disposal_202401.json")).records
    assert all(a.code for a in t)  # "本日無處置資料" placeholder rows skipped
    assert any(a.code == "6535" and a.start_date == date(2024, 1, 26) and a.end_date == date(2024, 2, 19) for a in t)
    fd = rs.parse_twse_full_delivery(load_fixture("real/twse_twt85u_20190102.json"), date(2019, 1, 2)).records
    assert fd[0].code == "1418" and fd[0].detail["periodic_auction"] is True
    alt = rs.parse_tpex_altered(load_fixture("real/tpex_chtm_20230601.json"), date(2023, 6, 1)).records
    kinds = {(a.code, a.restriction_type) for a in alt}
    assert ("3064", "ALTERED_TRADING") in kinds and ("3064", "PERIODIC_AUCTION") in kinds
    assert ("1258", "STOP_TRADING") in kinds
    h = rs.parse_twse_halt(load_fixture("real/twse_twtawu_202401.json")).records[0]
    assert (h.code, h.start_date, h.end_date) == ("6472", date(2024, 1, 16), date(2024, 1, 16))


def test_mops_history_parsers_real_payloads():
    inc = {r.code: r for r in mh.parse_income_summary(
        load_fixture("real/mops_income_sii_2019q1.html").decode("utf-8"), 2019, 1).records}
    assert inc["2330"].revenue == 218704469 and inc["2330"].eps == D("2.37")
    assert inc["2330"].gross_profit == 90357557 and inc["2330"].net_income == 61387310
    assert inc["2801"].revenue == 5730989 + 2321758  # bank: net interest + non-interest income
    bal = {r.code: r for r in mh.parse_balance_summary(
        load_fixture("real/mops_balance_sii_2019q1.html").decode("utf-8"), 2019, 1).records}
    assert bal["2330"].total_assets == 2187436785 and bal["2330"].equity == 1743517602
    up = mh.parse_doc_uploads(mh.decode_doc(load_fixture("real/doc_t57sb01_2330_108.html")), "2330")
    assert up[(2019, 1)] == datetime(2019, 5, 15, 13, 41, 47, tzinfo=TPE)
    assert up[(2019, 4)] == datetime(2020, 2, 27, 13, 33, 51, tzinfo=TPE)  # annual report


# ------------------------------------------------------------------ G4: TPEx ex-rights regression
@pytest.fixture()
def tpex_div_market():
    return Market(date(2024, 1, 2), days=70, ex_div=("6488", 40, 120.0))  # >20% drop without the event


def _ex_payload(m, code, idx, div, ex_date=None):
    d = ex_date or m.dates[idx]
    prev = m.bars[("TPEX", code)][m.dates[idx - 1]]["close"]
    row = [f"{d.year - 1911}/{d.month:02d}/{d.day:02d}", code, "環球晶", f"{prev:.2f}", f"{prev - div:.2f}", "0",
           f"{div}", f"{div}", "除息", "", "", "", "", f"{div}", "0", "0", "0", "0", "0", "0", "0"]
    f = ["除權息日期", "代號", "名稱", "除權息前收盤價", "除權息參考價", "權值", "息值", "權值+息值", "權/息", "漲停價",
         "跌停價", "開始交易基準價", "減除股利參考價", "現金股利", "每仟股無償配股", "現金增資股數", "現金增資認購價",
         "公開承銷股數", "員工認購股數", "原股東認購股數", "按持股比例仟股認購"]
    return {"_fixture_note": FIXTURE_NOTE, "tables": [{"fields": f, "data": [row]}]}


def test_tpex_ex_rights_fixes_prev_close_adjustment_and_anomaly(engine, tpex_div_market):
    m = tpex_div_market
    router = Router(m)
    f = Fetcher(engine, transport=httpx.MockTransport(router), sleep=lambda s: None)
    pipeline.backfill(engine, m.dates[0], m.dates[-1], f)
    d = m.dates[40]
    with session_scope(engine) as s:
        sid = _sid(s, "6488", "TPEX")
        issue = s.scalar(select(DataQualityIssue).where(DataQualityIssue.security_id == sid,
                                                        DataQualityIssue.trade_date == d))
        assert issue is not None and issue.check_name == "move_exceeds_limit"  # the gap before the fix
    router.override["/bulletin/exDailyQ"] = _ex_payload(m, "6488", 40, 120.0)
    out = events.ingest_corporate_events(engine, f, m.dates[0], m.dates[-1], ("tpex_ex_rights",))
    assert out["tpex_ex_rights"] == 1 and out["adjusted_securities"] == 1
    events.refresh_prev_close_on_event_days(engine, m.dates[0], m.dates[-1])
    counts = quality_review.classify_move_issues(engine, None)
    f.close()
    assert counts == {"CORPORATE_ACTION_DIV": 1}
    prev = m.bars[("TPEX", "6488")][m.dates[39]]["close"]
    with session_scope(engine) as s:
        p = s.get(DailyPrice, (sid, d))
        assert float(p.prev_close) == pytest.approx(prev - 120.0, abs=0.01)  # reference price, not raw close
        before = s.get(DailyPrice, (sid, m.dates[39]))
        assert float(before.adj_factor) == pytest.approx(round(prev - 120.0, 2) / round(prev, 2), rel=1e-4)
        assert s.get(DailyPrice, (sid, d)).adj_factor == 1
        issue = s.scalar(select(DataQualityIssue).where(DataQualityIssue.security_id == sid,
                                                        DataQualityIssue.trade_date == d))
        assert issue.resolved and not issue.blocks_backtest
        # adjusted series is continuous across the ex-date: no artificial >20% drop
        assert abs(float(p.close) / (float(before.close) * float(before.adj_factor)) - 1) < 0.10
        assert sid not in blocked_securities(s, d)


def test_capital_reduction_and_par_change_events_adjust_history(engine, fetcher, router, synthetic_market):
    m = synthetic_market
    pipeline.backfill(engine, m.dates[0], m.dates[20], fetcher)
    d = m.dates[10]
    prev = m.bars[("TWSE", "1101")][m.dates[9]]["close"]
    roc = f"{d.year - 1911}/{d.month:02d}/{d.day:02d}"
    router.override["/reducation/TWTAUU"] = {"stat": "OK", "fields": [
        "恢復買賣日期", "股票代號", "名稱", "停止買賣前收盤價格", "恢復買賣參考價", "漲停價格", "跌停價格", "開盤競價基準",
        "除權參考價", "減資原因", "詳細資料"], "data": [[roc, "1101", "台泥", f"{prev:.2f}", f"{prev * 2:.2f}", "", "", "",
                                                    "--", "彌補虧損", ""]]}
    out = events.ingest_corporate_events(engine, fetcher, m.dates[0], m.dates[20], ("twse_capital_reduction",))
    assert out["twse_capital_reduction"] == 1
    with session_scope(engine) as s:
        sid = _sid(s, "1101", "TWSE")
        assert s.scalar(select(CorporateAction.action_type).where(CorporateAction.security_id == sid)) \
            == "CAPITAL_REDUCTION"
        assert float(s.get(DailyPrice, (sid, m.dates[9])).adj_factor) == pytest.approx(2.0, rel=1e-6)


def test_ex_date_on_closed_day_applies_to_next_trading_day(engine, tpex_div_market):
    """Typhoon closures: the official ex-date can be a day without trading (e.g. 2024-07-24/25)."""
    m = tpex_div_market
    idx = next(i for i in range(35, 60) if m.dates[i].weekday() == 0)  # a Monday
    m2 = Market(date(2024, 1, 2), days=70, ex_div=("6488", idx, 120.0))
    router = Router(m2)
    f = Fetcher(engine, transport=httpx.MockTransport(router), sleep=lambda s: None)
    pipeline.backfill(engine, m2.dates[0], m2.dates[-1], f)
    saturday = m2.dates[idx] - timedelta(days=2)
    router.override["/bulletin/exDailyQ"] = _ex_payload(m2, "6488", idx, 120.0, ex_date=saturday)
    events.ingest_corporate_events(engine, f, m2.dates[0], m2.dates[-1], ("tpex_ex_rights",))
    events.refresh_prev_close_on_event_days(engine, m2.dates[0], m2.dates[-1])
    assert quality_review.classify_move_issues(engine, None) == {"CORPORATE_ACTION_DIV": 1}
    f.close()
    prev = m2.bars[("TPEX", "6488")][m2.dates[idx - 1]]["close"]
    with session_scope(engine) as s:
        sid = _sid(s, "6488", "TPEX")
        assert float(s.get(DailyPrice, (sid, m2.dates[idx])).prev_close) == pytest.approx(prev - 120.0, abs=0.01)
        assert float(s.get(DailyPrice, (sid, m2.dates[idx - 1])).adj_factor) < 1


# ------------------------------------------------------------------ G6: restrictions -> universe
def test_restrictions_exclude_from_universe_point_in_time(engine, fetcher, router, synthetic_market):
    m = synthetic_market
    pipeline.backfill(engine, m.dates[0], m.dates[-1], fetcher)
    k = 70
    ann, start, end = m.dates[k - 1], m.dates[k], m.dates[k + 3]
    r = lambda x: f"{x.year - 1911}/{x.month:02d}/{x.day:02d}"
    router.override["/announcement/punish"] = {"stat": "OK", "fields": [
        "編號", "公布日期", "證券代號", "證券名稱", "累計", "處置條件", "處置起迄時間", "處置措施", "處置內容", "備註"],
        "data": [[1, r(ann), "2330", "台積電", 1, "連續三次", f"{r(start)}～{r(end)}", "第一次處置", "", ""]]}
    router.override["/fullDelivery/TWT85U"] = {"stat": "OK", "fields": ["證券代號", "證券名稱", "分盤集合競價(以**表示)"],
                                               "data": [["2317", "鴻海", "**"]]}
    events.ingest_restriction_ranges(engine, fetcher, m.dates[k - 5], m.dates[k + 5])
    events.ingest_restriction_snapshot(engine, fetcher, m.dates[80])
    with session_scope(engine) as s:
        tsmc, hon = _sid(s, "2330", "TWSE"), _sid(s, "2317", "TWSE")
        assert s.scalar(select(TradingRestriction.restriction_type).where(TradingRestriction.security_id == tsmc)) \
            == "DISPOSITION"
        assert build_universe(s, m.dates[k - 2]).excluded.get(tsmc) != "disposition"  # not yet announced
        assert build_universe(s, ann).excluded[tsmc] == "disposition"  # announced today, starts next day
        assert build_universe(s, m.dates[k + 2]).excluded[tsmc] == "disposition"
        assert tsmc in build_universe(s, m.dates[k + 8]).eligible  # disposition over
        assert build_universe(s, m.dates[80]).excluded[hon] == "altered_trading_full_delivery"
        assert hon in build_universe(s, m.dates[81]).eligible  # a daily list only describes its own day


# ------------------------------------------------------------------ G7: financial publication time
def test_financial_history_publication_time_gates_backtest_access(engine, router, synthetic_market):
    router.override["/ajax_t163sb04"] = load_fixture("real/mops_income_sii_2019q1.html")
    router.override["/ajax_t163sb05"] = load_fixture("real/mops_balance_sii_2019q1.html")
    router.override["/server-java/t57sb01"] = load_fixture("real/doc_t57sb01_2330_108.html")
    f = Fetcher(engine, transport=httpx.MockTransport(router), sleep=lambda s: None)
    out = events.ingest_financial_quarter(engine, f, "TWSE", 2019, 1)
    assert out == {"income": 4, "balance": 4}
    with session_scope(engine) as s:
        tsmc = _sid(s, "2330", "TWSE")
        fs = s.get(FinancialStatement, (tsmc, 2019, 1, "CUMULATIVE"))
        assert fs.revenue == 218704469 and fs.total_assets == 2187436785
        assert fs.published_basis == "STATUTORY_DEADLINE" and fs.pit_reliable is False
        # no reliable publication time -> never visible to fundamental backtests, even long after
        assert latest_statements_as_of(s, datetime(2025, 1, 1, tzinfo=TPE)) == {}
    todo = events.companies_needing_pubdates(engine, 2019, 2019)
    assert (tsmc, "2330", 2019) in todo
    assert events.ingest_publication_times(engine, f, tsmc, "2330", 2019) == 1
    f.close()
    with session_scope(engine) as s:
        fs = s.get(FinancialStatement, (tsmc, 2019, 1, "CUMULATIVE"))
        assert fs.published_basis == "DOC_UPLOAD_TIME" and fs.pit_reliable is True
        assert fs.published_at == datetime(2019, 5, 15, 13, 41, 47, tzinfo=TPE) == fs.available_at
        assert latest_statements_as_of(s, datetime(2019, 5, 15, 13, 41, 46, tzinfo=TPE)) == {}
        got = latest_statements_as_of(s, datetime(2019, 5, 15, 13, 41, 47, tzinfo=TPE))
        assert list(got) == [tsmc] and got[tsmc].eps == D("2.37")
        other = _sid(s, "1101", "TWSE")  # no upload time fetched for 1101 -> still excluded
        assert s.get(FinancialStatement, (other, 2019, 1, "CUMULATIVE")).pit_reliable is False
    with pytest.raises(ValueError):
        with session_scope(engine) as s:
            latest_statements_as_of(s, datetime(2025, 1, 1))


# ------------------------------------------------------------------ G13: anomaly classification
def _issue(s, sid, d):
    s.add(DataQualityIssue(check_name="move_exceeds_limit", severity="WARN", dataset="daily_prices",
                           security_id=sid, trade_date=d, detail={}))


def test_unexplained_move_becomes_blocking_error_new_listing_does_not(engine, fetcher, synthetic_market):
    m = synthetic_market
    pipeline.backfill(engine, m.dates[0], m.dates[30], fetcher)
    with session_scope(engine) as s:
        tsmc, hon, etf = _sid(s, "2330", "TWSE"), _sid(s, "2317", "TWSE"), _sid(s, "0050", "TWSE")
        s.get(Security, hon).listed_date = m.dates[0]
        _issue(s, tsmc, m.dates[20])  # nothing explains it
        _issue(s, hon, m.dates[2])  # third trading day after listing: no price limit
        _issue(s, etf, m.dates[21])
    counts = quality_review.classify_move_issues(engine, None)
    assert counts == {"UNEXPLAINED": 1, "NEW_LISTING_NO_LIMIT": 1, "ETF_NO_PRICE_LIMIT_UNVERIFIED": 1}
    with session_scope(engine) as s:
        un = s.scalar(select(DataQualityIssue).where(DataQualityIssue.security_id == tsmc))
        assert un.severity == "ERROR" and un.blocks_backtest and not un.resolved
        nl = s.scalar(select(DataQualityIssue).where(DataQualityIssue.security_id == hon))
        assert nl.severity == "WARN" and not nl.blocks_backtest and nl.resolved
        assert blocked_securities(s, m.dates[25])[tsmc] == "unexplained_price_anomaly"
        assert tsmc not in blocked_securities(s, m.dates[19])  # not before the anomaly
        assert tsmc not in blocked_securities(s, m.dates[20] + timedelta(days=121))
        assert hon not in blocked_securities(s, m.dates[25])


def test_integrity_reports_coverage_and_quality_separately(engine, fetcher, synthetic_market, tmp_path):
    m = synthetic_market
    pipeline.backfill(engine, m.dates[0], m.dates[9], fetcher)
    with session_scope(engine) as s:
        _issue(s, _sid(s, "2330", "TWSE"), m.dates[5])
    rep = run_integrity_check(engine, m.dates[0], m.dates[9], str(tmp_path), sha_sample=10)
    v = rep["verdict"]
    assert set(v) == {"coverage", "quality", "backtest_ready"} and "complete" not in v
    assert v["quality"]["status"] == "UNREVIEWED" and v["quality"]["unclassified"] >= 1
    assert v["backtest_ready"] is False
    quality_review.classify_move_issues(engine, None)
    quality_review.classify_known_checks(engine)
    v = run_integrity_check(engine, m.dates[0], m.dates[9], str(tmp_path), sha_sample=10)["verdict"]
    assert v["quality"]["status"] == "REVIEWED_WITH_EXCLUSIONS" and v["quality"]["blocking_backtest"] == 1
    assert v["quality"]["unclassified"] == 0
    md = open(rep["saved_to"].replace(".json", ".md"), encoding="utf-8").read()
    assert "覆蓋完整度" in md and "資料品質狀態" in md and "Complete:" not in md
