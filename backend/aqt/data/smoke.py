"""Live smoke test of every source + parser, without writing to the database.

Run on the production host after deployment (`python -m aqt.cli check-sources`): parsers were
built against the documented formats, and this is how a format change is caught early.
"""
from datetime import datetime, timedelta

from aqt.data.http import Fetcher
from aqt.data.pit import TPE
from aqt.data.sources import companies, isin, mops, tpex, twse, twse_mis


def _recent_weekday():
    d = datetime.now(TPE).date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def check_sources() -> dict:
    d = _recent_weekday()
    f = Fetcher(archive=False)
    out = {"probe_date": str(d)}
    checks = [
        ("twse_quotes", lambda: twse.parse_daily_quotes(f.get("t", "t", *twse.daily_quotes_request(d)).json(), d)),
        ("tpex_quotes", lambda: tpex.parse_daily_quotes(f.get("t", "t", *tpex.daily_quotes_request(d)).json(), d)),
        ("twse_institutional", lambda: twse.parse_institutional(f.get("t", "t", *twse.institutional_request(d)).json(), d)),
        ("tpex_institutional", lambda: tpex.parse_institutional(f.get("t", "t", *tpex.institutional_request(d)).json(), d)),
        ("twse_margin", lambda: twse.parse_margin(f.get("t", "t", *twse.margin_request(d)).json(), d)),
        ("tpex_margin", lambda: tpex.parse_margin(f.get("t", "t", *tpex.margin_request(d)).json(), d)),
        ("twse_valuation", lambda: twse.parse_valuation(f.get("t", "t", *twse.valuation_request(d)).json(), d)),
        ("tpex_valuation", lambda: tpex.parse_valuation(f.get("t", "t", *tpex.valuation_request(d)).json(), d)),
        ("twse_ex_rights", lambda: twse.parse_corporate_actions(
            f.get("t", "t", *twse.corporate_actions_request(d - timedelta(days=30), d)).json())),
        ("twse_holidays", lambda: twse.parse_holidays(f.get("t", "t", *twse.holidays_request(d.year)).json())),
        ("isin_twse", lambda: isin.parse(isin.decode(f.get("t", "t", *isin.request("TWSE")).content), "TWSE")),
        ("isin_tpex", lambda: isin.parse(isin.decode(f.get("t", "t", *isin.request("TPEX")).content), "TPEX")),
        ("companies_twse", lambda: companies.parse(f.get("t", "t", companies.URL["TWSE"]).json(), "TWSE")),
        ("companies_tpex", lambda: companies.parse(f.get("t", "t", companies.URL["TPEX"]).json(), "TPEX")),
        ("revenue_twse", lambda: mops.parse_revenue_openapi(f.get("t", "t", mops.REVENUE_URL["TWSE"]).json())),
        ("revenue_tpex", lambda: mops.parse_revenue_openapi(f.get("t", "t", mops.REVENUE_URL["TPEX"]).json())),
        ("income_twse", lambda: mops.parse_income_openapi(f.get("t", "t", mops.INCOME_URL["TWSE"]).json())),
        ("income_tpex", lambda: mops.parse_income_openapi(f.get("t", "t", mops.INCOME_URL["TPEX"]).json())),
        ("balance_twse", lambda: mops.parse_balance_openapi(f.get("t", "t", mops.BALANCE_URL["TWSE"]).json())),
        ("balance_tpex", lambda: mops.parse_balance_openapi(f.get("t", "t", mops.BALANCE_URL["TPEX"]).json())),
        ("revenue_history_twse", lambda: mops.parse_revenue_history_html(
            isin.decode(f.get("t", "t", mops.historical_revenue_url("TWSE", d.year - 1, 12)).content), d.year - 1, 12)),
        ("mis_intraday", lambda: twse_mis.parse(f.get("t", "t", *twse_mis.request([("2330", "TWSE")])).json())),
    ]
    try:
        for name, fn in checks:
            try:
                res = fn()
                sample = res.records[0] if res.records else None
                out[name] = {"ok": bool(res.records), "records": len(res.records), "note": res.note,
                             "sample": repr(sample)[:300]}
            except Exception as e:
                out[name] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
    finally:
        f.close()
    return out
