"""PHASE 1 daily data pipeline (steps 1–7 of the daily routine).

Order per trading day d:
  0. corporate actions with ex-date d (needed for prev_close / limits)
  1-3. quotes (+ volume) & indices per market  -> decides whether d is a trading day
  5. institutional flows, margin, valuations
  4. prev_close / limits, adj factors, technical indicators
  7. data-quality checks, safe-mode decision, tradable-universe build
Fundamentals (step 6) run on their own schedule (`sync_fundamentals`).
"""
import logging
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.engine import Engine

from aqt.data import quality, repository as repo
from aqt.data.http import Fetcher, FetchError, mark_parsed
from aqt.data.parsing import parse_any_date
from aqt.data.sources import companies, isin, mops, tpex, twse
from aqt.data.universe import build_universe
from aqt.db import session_scope
from aqt.indicators.compute import compute_for_dates, recompute_adj_factors
from aqt.jobs.runtime import enter_safe_mode, exit_safe_mode, job_run, record_source
from aqt.models import CorporateAction

log = logging.getLogger(__name__)
MARKETS = ("TWSE", "TPEX")


class PayloadDateMismatch(FetchError):
    pass


DAILY = {
    ("TWSE", "quotes"): (twse.SOURCE, twse.daily_quotes_request, twse.parse_daily_quotes),
    ("TPEX", "quotes"): (tpex.SOURCE, tpex.daily_quotes_request, tpex.parse_daily_quotes),
    ("TWSE", "institutional"): (twse.SOURCE, twse.institutional_request, twse.parse_institutional),
    ("TPEX", "institutional"): (tpex.SOURCE, tpex.institutional_request, tpex.parse_institutional),
    ("TWSE", "margin"): (twse.SOURCE, twse.margin_request, twse.parse_margin),
    ("TPEX", "margin"): (tpex.SOURCE, tpex.margin_request, tpex.parse_margin),
    ("TWSE", "valuation"): (twse.SOURCE, twse.valuation_request, twse.parse_valuation),
    ("TPEX", "valuation"): (tpex.SOURCE, tpex.valuation_request, tpex.parse_valuation),
}
SECONDARY = ("institutional", "margin", "valuation")


def _check_payload_date(payload, d: date) -> None:
    if not isinstance(payload, dict):
        return
    for k in ("date", "reportDate"):
        v = payload.get(k)
        if v:
            try:
                got = parse_any_date(str(v))
            except ValueError:
                return
            if got != d:
                raise PayloadDateMismatch(f"asked for {d}, source answered for {got}")
            return


def ingest_daily(engine: Engine, fetcher: Fetcher, market: str, dataset: str, d: date) -> dict:
    source, build, parse = DAILY[(market, dataset)]
    url, params = build(d)
    tag = f"{market.lower()}_{dataset}"
    try:
        res = fetcher.get(source, tag, url, params, d)
        payload = res.json()
        parsed = parse(payload, d)
        if parsed.records:
            _check_payload_date(payload, d)
    except Exception as e:
        record_source(engine, source, tag, False, str(e))
        if "res" in locals():
            mark_parsed(engine, res.raw_id, "ERROR", str(e)[:2000])
        raise
    with session_scope(engine) as s:
        if dataset == "quotes":
            n = repo.upsert_prices(s, parsed.records, source, res.raw_id)
            repo.upsert_indices(s, parsed.indices, source, res.raw_id)
            if parsed.is_trading_day is not None:
                repo.upsert_calendar_day(s, market, d, parsed.is_trading_day, "observed_data", parsed.note)
        elif dataset == "institutional":
            n = repo.upsert_institutional(s, parsed.records, market, source, res.raw_id)
        elif dataset == "margin":
            n = repo.upsert_margin(s, parsed.records, market, source, res.raw_id)
        else:
            n = repo.upsert_valuations(s, parsed.records, market, source, res.raw_id)
    mark_parsed(engine, res.raw_id, "OK" if parsed.records else "EMPTY", parsed.note)
    record_source(engine, source, tag, True, data_date=d if parsed.records else None)
    return {"rows": n, "is_trading_day": parsed.is_trading_day, "note": parsed.note}


def ingest_corporate_actions(engine: Engine, fetcher: Fetcher, start: date, end: date) -> int:
    url, params = twse.corporate_actions_request(start, end)
    try:
        res = fetcher.get(twse.SOURCE, "twse_ex_rights", url, params, end)
        parsed = twse.parse_corporate_actions(res.json())
    except Exception as e:
        record_source(engine, twse.SOURCE, "twse_ex_rights", False, str(e))
        raise
    with session_scope(engine) as s:
        n = repo.upsert_corporate_actions(s, parsed.records, "TWSE", twse.SOURCE, res.raw_id)
        sids = {sid for (sid,) in s.execute(select(CorporateAction.security_id)
                                            .where(CorporateAction.ex_date.between(start, end)))}
        for sid in sids:
            recompute_adj_factors(s, sid)
    mark_parsed(engine, res.raw_id, "OK" if parsed.records else "EMPTY")
    record_source(engine, twse.SOURCE, "twse_ex_rights", True, data_date=end)
    return n


def run_daily_update(engine: Engine, d: date, fetcher: Fetcher | None = None,
                     compute_indicators: bool = True, manage_safe_mode: bool = True,
                     corporate_actions: bool = True) -> dict:
    own = fetcher is None
    fetcher = fetcher or Fetcher(engine)
    try:
        with job_run(engine, "daily_update", {"date": d}) as job:
            r = job.result
            if d.weekday() >= 5:
                job.status, r["skipped"] = "SKIPPED", "weekend"
                return r
            if corporate_actions:
                try:
                    r["corporate_actions"] = ingest_corporate_actions(engine, fetcher, d, d)
                except Exception as e:
                    job.partial(f"corporate_actions: {e}")

            trading_markets, quote_failures = [], []
            for m in MARKETS:
                try:
                    out = ingest_daily(engine, fetcher, m, "quotes", d)
                    r[f"{m}_quotes"] = out
                    if out["is_trading_day"]:
                        trading_markets.append(m)
                except Exception as e:
                    quote_failures.append(f"{m}: {e}")
                    job.partial(f"{m} quotes: {e}")

            if not trading_markets and not quote_failures:
                job.status, r["skipped"] = "SKIPPED", "not a trading day"
                return r

            for m in trading_markets:
                for ds in SECONDARY:
                    try:
                        r[f"{m}_{ds}"] = ingest_daily(engine, fetcher, m, ds, d)
                    except Exception as e:
                        job.partial(f"{m} {ds}: {e}")

            issues = []
            with session_scope(engine) as s:
                for m in trading_markets:
                    quality.fill_prev_close_and_limits(s, d, m)
                s.flush()
                for m in trading_markets:
                    issues += quality.check_prices(s, d, m)
                issues += quality.check_institutional(s, d)
                quality.save_issues(s, issues)
            r["quality"] = quality.summarize(issues)

            if compute_indicators and trading_markets:
                with session_scope(engine) as s:
                    r["indicators"] = compute_for_dates(s, d, d)
                with session_scope(engine) as s:
                    u = build_universe(s, d)
                r["universe"] = {"eligible": len(u.eligible), "excluded": u.exclusion_counts()}

            blocking = [i for i in issues if i.severity == "ERROR" and i.security_id is None]
            if manage_safe_mode:
                if quote_failures or blocking:
                    reasons = quote_failures + [f"{i.check_name}: {i.detail}" for i in blocking]
                    enter_safe_mode(engine, f"{d} 行情資料異常：" + "; ".join(reasons)[:1500])
                    job.partial("safe mode entered")
                else:
                    exit_safe_mode(engine, f"{d} 行情資料完整，通過品質檢查")
            return r
    finally:
        if own:
            fetcher.close()


def backfill(engine: Engine, start: date, end: date, fetcher: Fetcher | None = None) -> dict:
    """Date-by-date backfill. Iterating dates (not today's stock list) means stocks that were later
    delisted are captured too — the core survivorship-bias guard."""
    own = fetcher is None
    fetcher = fetcher or Fetcher(engine)
    summary = {"days": 0, "trading_days": 0, "failed": []}
    try:
        try:
            # TWT49U accepts a range; chunk by ~90 days
            s0 = start
            while s0 <= end:
                e0 = min(end, s0 + timedelta(days=90))
                ingest_corporate_actions(engine, fetcher, s0, e0)
                s0 = e0 + timedelta(days=1)
        except Exception as e:
            summary["failed"].append(f"corporate_actions: {e}")
        d = start
        while d <= end:
            if d.weekday() < 5:
                summary["days"] += 1
                try:
                    r = run_daily_update(engine, d, fetcher, compute_indicators=False, manage_safe_mode=False,
                                         corporate_actions=False)
                    if not r.get("skipped"):
                        summary["trading_days"] += 1
                except Exception as e:
                    summary["failed"].append(f"{d}: {e}")
            d += timedelta(days=1)
        with session_scope(engine) as s:
            # corporate actions were loaded before the prices existed -> apply adjustments now
            sids = {sid for (sid,) in s.execute(select(CorporateAction.security_id).distinct())}
            for sid in sids:
                recompute_adj_factors(s, sid)
        with session_scope(engine) as s:
            summary["indicators"] = compute_for_dates(s, start, end)
    finally:
        if own:
            fetcher.close()
    return summary


def _fetch_registry(fetcher: Fetcher, m: str):
    """ISIN registry first (has ETFs, ISIN codes); company list from the OpenAPI as fallback."""
    url, params = isin.request(m)
    try:
        res = fetcher.get(isin.SOURCE, f"isin_{m.lower()}", url, params)
        parsed = isin.parse(isin.decode(res.content), m)
        if len(parsed.records) < 100:
            raise FetchError(f"only {len(parsed.records)} securities parsed — refusing to update")
        return isin.SOURCE, f"isin_{m.lower()}", res, parsed, None
    except Exception as e:
        primary_error = f"isin: {e}"
    res = fetcher.get(companies.SOURCE, f"companies_{m.lower()}", companies.URL[m])
    parsed = companies.parse(res.json(), m)
    if len(parsed.records) < 100:
        raise FetchError(f"{primary_error}; company list: only {len(parsed.records)} parsed")
    return companies.SOURCE, f"companies_{m.lower()}", res, parsed, primary_error


def sync_securities(engine: Engine, fetcher: Fetcher | None = None) -> dict:
    own = fetcher is None
    fetcher = fetcher or Fetcher(engine)
    out = {}
    try:
        with job_run(engine, "sync_securities") as job:
            for m in MARKETS:
                try:
                    source, tag, res, parsed, primary_error = _fetch_registry(fetcher, m)
                except Exception as e:
                    record_source(engine, isin.SOURCE, f"isin_{m.lower()}", False, str(e))
                    job.partial(f"{m}: {e}")
                    continue
                if primary_error:
                    record_source(engine, isin.SOURCE, f"isin_{m.lower()}", False, primary_error)
                    job.partial(f"{m}: {primary_error} — used company list fallback")
                codes = {r.code for r in parsed.records}
                with session_scope(engine) as s:
                    n = repo.upsert_security_master(s, parsed.records)
                    if source == companies.SOURCE:
                        # the company list only covers common-stock issuers: only plain stock codes
                        # can be judged delisted from it
                        known = repo.security_id_map(s, m)
                        codes |= {c for c, _ in known if twse.classify_code(c) != "STOCK"}
                        gone = repo.mark_missing_as_delisted(s, m, codes, date.today(), types=("STOCK",))
                    else:
                        gone = repo.mark_missing_as_delisted(s, m, codes, date.today())
                mark_parsed(engine, res.raw_id, "OK")
                record_source(engine, source, tag, True, data_date=date.today())
                out[m] = {"source": source, "upserted": n, "marked_delisted": gone}
            job.result.update(out)
    finally:
        if own:
            fetcher.close()
    return out


def sync_calendar(engine: Engine, year: int, fetcher: Fetcher | None = None) -> int:
    own = fetcher is None
    fetcher = fetcher or Fetcher(engine)
    try:
        url, params = twse.holidays_request(year)
        res = fetcher.get(twse.SOURCE, "holidays", url, params)
        parsed = twse.parse_holidays(res.json())
        with session_scope(engine) as s:
            n = repo.upsert_holidays(s, parsed.records)
        mark_parsed(engine, res.raw_id, "OK" if parsed.records else "EMPTY")
        record_source(engine, twse.SOURCE, "holidays", True)
        return n
    finally:
        if own:
            fetcher.close()


def sync_fundamentals(engine: Engine, fetcher: Fetcher | None = None) -> dict:
    """Latest monthly revenue + latest quarterly income statement / balance sheet, both markets."""
    own = fetcher is None
    fetcher = fetcher or Fetcher(engine)
    jobs = [("revenue", mops.REVENUE_URL, mops.parse_revenue_openapi, repo.upsert_revenues),
            ("income", mops.INCOME_URL, mops.parse_income_openapi, repo.upsert_financials),
            ("balance", mops.BALANCE_URL, mops.parse_balance_openapi, repo.upsert_financials)]
    try:
        with job_run(engine, "sync_fundamentals") as job:
            for name, urls, parse, upsert in jobs:
                for m in MARKETS:
                    tag = f"{name}_{m.lower()}"
                    try:
                        res = fetcher.get(mops.SOURCE, tag, urls[m])
                        parsed = parse(res.json())
                        with session_scope(engine) as s:
                            job.result[tag] = upsert(s, parsed.records, m, mops.SOURCE, res.raw_id)
                        mark_parsed(engine, res.raw_id, "OK" if parsed.records else "EMPTY")
                        record_source(engine, mops.SOURCE, tag, True)
                    except Exception as e:
                        record_source(engine, mops.SOURCE, tag, False, str(e))
                        job.partial(f"{tag}: {e}")
            return job.result
    finally:
        if own:
            fetcher.close()


def backfill_revenue(engine: Engine, market: str, year: int, month: int, fetcher: Fetcher | None = None) -> int:
    own = fetcher is None
    fetcher = fetcher or Fetcher(engine)
    try:
        url = mops.historical_revenue_url(market, year, month)
        res = fetcher.get(mops.SOURCE, f"revenue_hist_{market.lower()}", url, None, date(year, month, 1))
        parsed = mops.parse_revenue_history_html(isin.decode(res.content), year, month)
        with session_scope(engine) as s:
            n = repo.upsert_revenues(s, parsed.records, market, mops.SOURCE, res.raw_id)
        mark_parsed(engine, res.raw_id, "OK" if parsed.records else "EMPTY")
        return n
    finally:
        if own:
            fetcher.close()
