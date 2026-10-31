"""Parsers against REAL exchange responses (trimmed copies in fixtures/real/, fetched 2026-10-31).

Expected values were read off the raw rows by hand, so a column mix-up fails here.
"""
from datetime import date
from decimal import Decimal as D

import pytest

from aqt.data import pipeline
from aqt.db import session_scope
from aqt.data.sources import companies, isin, tpex, twse
from aqt.models import Security
from sqlalchemy import select
from tests.conftest import load_fixture

D0 = date(2026, 10, 30)


def _by(res):
    return {r.code: r for r in res.records}


def test_twse_mi_index_real():
    res = twse.parse_daily_quotes(load_fixture("real/twse_mi_index_20261030.json"), D0)
    q = _by(res)
    assert q["2330"].close == D("2370.00") and q["2330"].change == D("-45.00")
    assert q["1101"].change == D("0.15") and q["1101"].volume == 41966086 and q["1101"].transactions == 12958
    taiex = [i for i in res.indices if i.index_code == "TAIEX"][0]
    assert taiex.close == D("45211.83") and taiex.change == D("-1156.50")


def test_tpex_otc_real():
    q = _by(tpex.parse_daily_quotes(load_fixture("real/tpex_otc_20261030.json"), D0))
    r = q["3105"]
    assert (r.open, r.high, r.low, r.close, r.change) == (D("567.00"), D("589.00"), D("541.00"), D("578.00"), D("-16.00"))
    assert r.volume == 38635000 and r.turnover == 21960557000 and r.transactions == 28202


def test_tpex_otc_empty_on_holiday():
    payload = {"tables": [{"title": "上櫃股票每日收盤行情(不含定價)", "totalCount": 0, "fields": ["代號", "名稱", "收盤 "],
                           "data": []}], "date": "20261009", "stat": "ok"}
    assert tpex.parse_daily_quotes(payload, date(2026, 10, 9)).is_trading_day is False


def test_twse_t86_real():
    r = _by(twse.parse_institutional(load_fixture("real/twse_t86_20261030.json"), D0))["2330"]
    assert (r.foreign_buy, r.foreign_sell, r.foreign_net) == (18503985, 27136819, -8632834)
    assert r.trust_net == 1854677 and r.dealer_net == 922012
    assert r.dealer_self_net == -18249 and r.dealer_hedge_net == 940261 and r.total_net == -5856145


def test_tpex_insti_real():
    r = _by(tpex.parse_institutional(load_fixture("real/tpex_insti_20261030.json"), D0))["3105"]
    assert (r.foreign_buy, r.foreign_sell, r.foreign_net, r.foreign_dealer_net) == (14536478, 15299068, -762590, 0)
    assert (r.trust_buy, r.trust_sell, r.trust_net) == (1316400, 882220, 434180)
    assert (r.dealer_self_net, r.dealer_hedge_net, r.dealer_net, r.total_net) == (34863, -283686, -248823, -577233)


def test_twse_margin_real():
    r = _by(twse.parse_margin(load_fixture("real/twse_margin_20261030.json"), D0))["1101"]
    assert (r.margin_buy, r.margin_sell, r.margin_redeem, r.margin_balance_prev, r.margin_balance) == (1134, 1524, 1, 37624, 37233)
    assert (r.short_cover, r.short_sell, r.short_redeem, r.short_balance_prev, r.short_balance) == (23, 121, 51, 468, 515)


def test_tpex_margin_real():
    r = _by(tpex.parse_margin(load_fixture("real/tpex_margin_20261030.json"), D0))["3105"]
    assert (r.margin_buy, r.margin_sell, r.margin_balance_prev, r.margin_balance, r.margin_limit) == (1898, 1782, 40995, 41109, 105985)
    # TPEx orders 券賣 before 券買 — the reverse of TWSE
    assert (r.short_sell, r.short_cover, r.short_balance_prev, r.short_balance) == (376, 550, 1566, 1392)
    assert r.short_balance_prev + r.short_sell - r.short_cover - r.short_redeem == r.short_balance


def test_valuations_real():
    tw = _by(twse.parse_valuation(load_fixture("real/twse_bwibbu_20261030.json"), D0))
    assert tw["2330"].pe_ratio == D("27.47") and tw["2330"].pb_ratio == D("9.55")
    assert tw["1101"].pe_ratio is None  # '-' = loss-making
    tp = _by(tpex.parse_valuation(load_fixture("real/tpex_pe_20261030.json"), D0))
    assert tp["3105"].pe_ratio == D("67.92") and tp["3105"].dividend_yield == D("0.35")


def test_company_registry_real():
    tw = _by(companies.parse(load_fixture("real/twse_companies_20261030.json")["rows"], "TWSE"))
    assert tw["2330"].name == "台積電" and tw["2330"].industry == "半導體業"
    assert tw["2330"].listed_date == date(1994, 9, 5) and tw["2330"].security_type == "STOCK"
    tp = _by(companies.parse(load_fixture("real/tpex_companies_20261030.json")["rows"], "TPEX"))
    assert tp["3105"].name == "穩懋" and tp["3105"].market == "TPEX"


def test_isin_security_notice_is_an_error():
    with pytest.raises(isin.RegistryBlocked):
        isin.parse(isin.decode(load_fixture("real/isin_security_notice.html")), "TWSE")


def _synthetic_company_rows(n, real_rows):
    # SYNTHETIC: padding so the registry passes the >=100 sanity check; shape copied from the real rows
    rows = list(real_rows)
    rows += [{"公司代號": str(9000 + i), "公司簡稱": f"SYN{i}", "產業別": "20", "上市日期": "20000101"}
             for i in range(n)]
    return rows


def test_sync_securities_falls_back_to_company_list(engine, fetcher, router):
    notice = load_fixture("real/isin_security_notice.html")
    router.override["/isin/C_public.jsp"] = notice
    router.override["t187ap03_L"] = _synthetic_company_rows(120, load_fixture("real/twse_companies_20261030.json")["rows"])
    with session_scope(engine) as s:  # an ETF and a known ISIN must survive the fallback
        s.add_all([Security(code="0050", market="TWSE", name="元大台灣50", security_type="ETF", status="ACTIVE"),
                   Security(code="2330", market="TWSE", name="台積電", security_type="STOCK", isin="TW0002330008",
                            status="ACTIVE"),
                   Security(code="1234", market="TWSE", name="已下市", security_type="STOCK", status="ACTIVE")])
    out = pipeline.sync_securities(engine, fetcher)
    assert out["TWSE"]["source"] == companies.SOURCE and out["TWSE"]["marked_delisted"] == 1
    assert "TPEX" not in out  # neither registry reachable in the mock -> left untouched
    with session_scope(engine) as s:
        sec = {x.code: x for x in s.scalars(select(Security).where(Security.market == "TWSE"))}
        assert sec["0050"].status == "ACTIVE"
        assert sec["2330"].isin == "TW0002330008" and sec["2330"].industry == "半導體業"
        assert sec["1234"].status == "DELISTED"


def test_tpex_financials_real_mixed_keys():
    from aqt.data.sources import mops
    inc = _by(mops.parse_income_openapi(load_fixture("real/tpex_income_2026q2.json")["rows"]))["3105"]
    assert (inc.fiscal_year, inc.fiscal_quarter, inc.eps) == (2026, 2, D("3.56"))
    assert inc.revenue == 9847328 and inc.published_date == date(2026, 10, 30)
    bal = _by(mops.parse_balance_openapi(load_fixture("real/tpex_balance_2026q2.json")["rows"]))["1240"]
    assert (bal.total_assets, bal.total_liabilities, bal.equity) == (2315123, 753544, 1561579)
