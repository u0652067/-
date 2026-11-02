"""Ingestion for the PHASE 1 gap datasets: corporate events (G4/G8), trading restrictions (G6),
historical financial statements and their publication time (G7).

Every fetch goes through `Fetcher` (raw response archived + sha256) and records source health.
"""
import logging
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from aqt.data import repository as repo
from aqt.data.http import Fetcher, mark_parsed
from aqt.data.sources import corporate_events as ce
from aqt.data.sources import mops_history as mh
from aqt.data.sources import restrictions as rs
from aqt.db import session_scope
from aqt.indicators.compute import recompute_adj_factors
from aqt.jobs.runtime import record_source
from aqt.models import CorporateAction, FinancialStatement, Security

log = logging.getLogger(__name__)


def _year_chunks(start: date, end: date, days: int = 366):
    s0 = start
    while s0 <= end:
        e0 = min(end, s0 + timedelta(days=days - 1))
        yield s0, e0
        s0 = e0 + timedelta(days=1)


def _fetch_parse(engine, fetcher, source, tag, url, params, data_date, parse, form=None):
    try:
        res = fetcher.get(source, tag, url, params, data_date, form=form)
        parsed = parse(res)
    except Exception as e:
        record_source(engine, source, tag, False, str(e))
        if "res" in locals():
            mark_parsed(engine, res.raw_id, "ERROR", str(e)[:2000])
        raise
    return res, parsed


# ------------------------------------------------------------------ G4 / G8 corporate events
def ingest_corporate_events(engine: Engine, fetcher: Fetcher, start: date, end: date,
                            names=tuple(ce.EVENTS), recompute: bool = True) -> dict:
    out: dict = {}
    touched: set[int] = set()
    for name in names:
        market, source, build, parse = ce.EVENTS[name]
        n = 0
        for s0, e0 in _year_chunks(start, end):
            url, params = build(s0, e0)
            res, parsed = _fetch_parse(engine, fetcher, source, name, url, params, e0, lambda r: parse(r.json()))
            with session_scope(engine) as s:
                n += repo.upsert_corporate_actions(s, parsed.records, market, source, res.raw_id)
                ids = repo.security_id_map(s, market)
                touched |= {ids[(r.code, market)] for r in parsed.records if (r.code, market) in ids}
            mark_parsed(engine, res.raw_id, "OK" if parsed.records else "EMPTY")
            record_source(engine, source, name, True, data_date=e0)
        out[name] = n
    if recompute:
        out["adjusted_securities"] = recompute_adjustments(engine, touched)
    return out


def recompute_adjustments(engine: Engine, security_ids) -> int:
    ids = sorted(security_ids)
    for i in range(0, len(ids), 200):
        with session_scope(engine) as s:
            for sid in ids[i:i + 200]:
                recompute_adj_factors(s, sid)
    return len(ids)


def refresh_prev_close_on_event_days(engine: Engine, start: date, end: date) -> int:
    """Re-derive prev_close / price limits on every event day (idempotent) so a newly loaded event
    replaces the raw previous close with the exchange's reference price."""
    from aqt.data.quality import fill_prev_close_and_limits
    with session_scope(engine) as s:
        days = s.execute(select(Security.market, CorporateAction.ex_date).distinct()
                         .join(Security, Security.id == CorporateAction.security_id)
                         .where(CorporateAction.ex_date.between(start, end))).all()
    n = 0
    for market, d in days:
        with session_scope(engine) as s:
            n += fill_prev_close_and_limits(s, d, market)
    return n


# ------------------------------------------------------------------ G6 trading restrictions
def ingest_restriction_ranges(engine: Engine, fetcher: Fetcher, start: date, end: date) -> dict:
    jobs = [("twse_disposition", "TWSE", "twse", rs.twse_disposition_request, rs.parse_disposition, 366),
            ("tpex_disposition", "TPEX", "tpex", rs.tpex_disposition_request, rs.parse_disposition, 92),
            ("twse_halt", "TWSE", "twse", rs.twse_halt_request, rs.parse_twse_halt, 366)]
    out: dict = {}
    for name, market, source, build, parse, days in jobs:
        tot = {"rows": 0, "skipped_unknown_codes": 0}
        for s0, e0 in _year_chunks(start, end, days):
            url, params = build(s0, e0)
            res, parsed = _fetch_parse(engine, fetcher, source, name, url, params, e0, lambda r: parse(r.json()))
            with session_scope(engine) as s:
                r = repo.upsert_restrictions(s, parsed.records, market, source, res.raw_id)
            tot = {k: tot[k] + r[k] for k in tot}
            mark_parsed(engine, res.raw_id, "OK" if parsed.records else "EMPTY")
            record_source(engine, source, name, True, data_date=e0)
        out[name] = tot
    return out


def ingest_restriction_snapshot(engine: Engine, fetcher: Fetcher, d: date) -> dict:
    """Daily lists: TWSE altered trading (TWT85U) and TPEx altered/periodic/managed/stopped (chtm)."""
    out = {}
    for name, market, source, build, parse in (
            ("twse_full_delivery", "TWSE", "twse", rs.twse_full_delivery_request, rs.parse_twse_full_delivery),
            ("tpex_altered", "TPEX", "tpex", rs.tpex_altered_request, rs.parse_tpex_altered)):
        url, params = build(d)
        res, parsed = _fetch_parse(engine, fetcher, source, name, url, params, d, lambda r: parse(r.json(), d))
        with session_scope(engine) as s:
            out[name] = repo.upsert_restrictions(s, parsed.records, market, source, res.raw_id)
        mark_parsed(engine, res.raw_id, "OK" if parsed.records else "EMPTY")
        record_source(engine, source, name, True, data_date=d)
    return out


# ------------------------------------------------------------------ G7 historical financial statements
def ingest_financial_quarter(engine: Engine, fetcher: Fetcher, market: str, year: int, quarter: int) -> dict:
    out = {}
    for report, parse in (("income", mh.parse_income_summary), ("balance", mh.parse_balance_summary)):
        url, form = mh.summary_request(report, market, year, quarter)
        tag = f"{report}_hist_{market.lower()}"
        res, parsed = _fetch_parse(engine, fetcher, mh.SOURCE, tag, url, None, date(year, quarter * 3, 1),
                                   lambda r: parse(r.content.decode("utf-8", errors="replace"), year, quarter),
                                   form=form)
        with session_scope(engine) as s:
            out[report] = repo.upsert_financial_history(s, parsed.records, market, mh.SOURCE, res.raw_id)
        mark_parsed(engine, res.raw_id, "OK" if parsed.records else "EMPTY")
        record_source(engine, mh.SOURCE, tag, True)
    return out


def companies_needing_pubdates(engine: Engine, start_year: int, end_year: int) -> list[tuple[int, str, int]]:
    """(security_id, code, fiscal_year) with statements whose publication time is still unknown."""
    with session_scope(engine) as s:
        rows = s.execute(select(FinancialStatement.security_id, Security.code, FinancialStatement.fiscal_year)
                         .join(Security, Security.id == FinancialStatement.security_id)
                         .where(FinancialStatement.fiscal_year.between(start_year, end_year),
                                FinancialStatement.published_basis != "DOC_UPLOAD_TIME")
                         .group_by(FinancialStatement.security_id, Security.code, FinancialStatement.fiscal_year)
                         .order_by(FinancialStatement.fiscal_year, Security.code)).all()
    return [(a, b, c) for a, b, c in rows]


def ingest_publication_times(engine: Engine, fetcher: Fetcher, security_id: int, code: str, year: int) -> int:
    url, params = mh.doc_request(code, year)
    res, uploads = _fetch_parse(engine, fetcher, mh.DOC_SOURCE, "financial_report_uploads", url, params,
                                date(year, 12, 31), lambda r: mh.parse_doc_uploads(mh.decode_doc(r.content), code))
    with session_scope(engine) as s:
        n = repo.set_financial_published_at(s, security_id, uploads, res.raw_id)
    mark_parsed(engine, res.raw_id, "OK" if uploads else "EMPTY")
    record_source(engine, mh.DOC_SOURCE, "financial_report_uploads", True)
    return n


def financial_pit_summary(engine: Engine) -> list[dict]:
    with session_scope(engine) as s:
        rows = s.execute(select(FinancialStatement.fiscal_year, FinancialStatement.published_basis,
                                FinancialStatement.pit_reliable, func.count())
                         .group_by(FinancialStatement.fiscal_year, FinancialStatement.published_basis,
                                   FinancialStatement.pit_reliable)
                         .order_by(FinancialStatement.fiscal_year)).all()
    return [dict(fiscal_year=y, basis=b, pit_reliable=r, rows=n) for y, b, r, n in rows]
