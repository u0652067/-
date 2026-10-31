"""Idempotent upserts from parser records into the database."""
from dataclasses import asdict
from datetime import date, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aqt.data import pit
from aqt.data.records import (
    CorporateActionRecord, FinancialRecord, HolidayRecord, IndexRecord, InstitutionalRecord,
    MarginRecord, PriceRecord, RevenueRecord, SecurityRecord, ValuationRecord,
)
from aqt.data.sources.twse import classify_code
from aqt.models import (
    CorporateAction, DailyPrice, FinancialStatement, InstitutionalTrade, MarginTrade, MarketIndex,
    MonthlyRevenue, Security, TradingCalendar, Valuation,
)
from aqt.models.base import utcnow

CHUNK = 2000


def _chunks(rows: list[dict]):
    for i in range(0, len(rows), CHUNK):
        yield rows[i:i + CHUNK]


def _upsert(session: Session, model, rows: list[dict], keys: list[str]) -> int:
    if not rows:
        return 0
    rows = list({tuple(r[k] for k in keys): r for r in rows}.values())  # de-duplicate, last wins
    cols = [c.name for c in model.__table__.columns]
    for chunk in _chunks(rows):
        stmt = insert(model).values(chunk)
        upd = {c: stmt.excluded[c] for c in chunk[0] if c in cols and c not in keys}
        stmt = stmt.on_conflict_do_update(index_elements=keys, set_=upd) if upd else stmt.on_conflict_do_nothing()
        session.execute(stmt)
    return len(rows)


# ------------------------------------------------------------------ securities
def security_id_map(session: Session, market: str | None = None) -> dict[tuple[str, str], int]:
    q = select(Security.code, Security.market, Security.id)
    if market:
        q = q.where(Security.market == market)
    return {(c, m): i for c, m, i in session.execute(q)}


def upsert_security_master(session: Session, recs: list[SecurityRecord]) -> int:
    rows = [dict(code=r.code, market=r.market, name=r.name, security_type=r.security_type, isin=r.isin,
                 listed_date=r.listed_date, industry=r.industry, status="ACTIVE", created_at=utcnow(),
                 updated_at=utcnow()) for r in recs]
    if all(r.isin is None for r in recs):
        for row in rows:  # registry without ISINs (company list fallback) must not erase known ISINs
            del row["isin"]
    return _upsert(session, Security, rows, ["code", "market"])


def mark_missing_as_delisted(session: Session, market: str, active_codes: set[str], as_of: date,
                             types: tuple[str, ...] = ("STOCK", "ETF")) -> int:
    """Securities absent from the current registry are flagged DELISTED (never deleted)."""
    q = select(Security).where(Security.market == market, Security.status == "ACTIVE")
    n = 0
    for sec in session.scalars(q):
        if sec.code not in active_codes and sec.security_type in types:
            sec.status = "DELISTED"
            sec.delisted_date = sec.last_seen_date or as_of
            n += 1
    return n


def ensure_securities(session: Session, items: list[tuple[str, str, str]], seen: date | None = None) -> dict:
    """items: (code, name, market). Creates unseen securities (e.g. delisted ones met during backfill)."""
    existing = security_id_map(session)
    new = [dict(code=c, market=m, name=n, security_type=classify_code(c), status="ACTIVE",
                first_seen_date=seen, last_seen_date=seen, created_at=utcnow(), updated_at=utcnow())
           for c, n, m in {(c, n, m) for c, n, m in items if (c, m) not in existing}]
    if new:
        session.execute(insert(Security).values(new).on_conflict_do_nothing())
        existing = security_id_map(session)
    if seen is not None:
        ids = [existing[(c, m)] for c, _, m in items if (c, m) in existing]
        for chunk in (ids[i:i + CHUNK] for i in range(0, len(ids), CHUNK)):
            session.execute(update(Security).where(Security.id.in_(chunk), (Security.last_seen_date.is_(None)) |
                                                   (Security.last_seen_date < seen)).values(last_seen_date=seen))
            session.execute(update(Security).where(Security.id.in_(chunk), (Security.first_seen_date.is_(None)) |
                                                   (Security.first_seen_date > seen)).values(first_seen_date=seen))
    return existing


# ------------------------------------------------------------------ daily datasets
def upsert_prices(session: Session, recs: list[PriceRecord], source: str, raw_id: int | None) -> int:
    if not recs:
        return 0
    ids = ensure_securities(session, [(r.code, r.name, r.market) for r in recs], recs[0].trade_date)
    rows = []
    for r in recs:
        rows.append(dict(
            security_id=ids[(r.code, r.market)], trade_date=r.trade_date, open=r.open, high=r.high, low=r.low,
            close=r.close, change=r.change, volume=r.volume, turnover=r.turnover, transactions=r.transactions,
            is_suspended=r.close is None and r.volume == 0, source=source, raw_payload_id=raw_id,
            available_at=pit.daily_dataset_available_at("daily_prices", r.trade_date), fetched_at=utcnow(),
        ))
    return _upsert(session, DailyPrice, rows, ["security_id", "trade_date"])


def upsert_indices(session: Session, recs: list[IndexRecord], source: str, raw_id: int | None) -> int:
    rows = [dict(index_code=r.index_code, trade_date=r.trade_date, name=r.name, close=r.close, change=r.change,
                 change_pct=r.change_pct, source=source, raw_payload_id=raw_id,
                 available_at=pit.daily_dataset_available_at("market_indices", r.trade_date)) for r in recs]
    return _upsert(session, MarketIndex, rows, ["index_code", "trade_date"])


def _daily_rows(session, recs, market, source, raw_id, dataset, fields):
    ids = ensure_securities(session, [(r.code, r.name, market) for r in recs])
    rows = []
    for r in recs:
        sid = ids.get((r.code, market))
        if sid is None:
            continue
        d = {k: getattr(r, k) for k in fields}
        d.update(security_id=sid, trade_date=r.trade_date, source=source, raw_payload_id=raw_id,
                 available_at=pit.daily_dataset_available_at(dataset, r.trade_date))
        rows.append(d)
    return rows


_INST_FIELDS = [f for f in InstitutionalRecord.__dataclass_fields__ if f not in ("code", "name", "trade_date")]
_MARGIN_FIELDS = [f for f in MarginRecord.__dataclass_fields__ if f not in ("code", "name", "trade_date")]
_VAL_FIELDS = ["pe_ratio", "pb_ratio", "dividend_yield"]


def upsert_institutional(session, recs: list[InstitutionalRecord], market, source, raw_id) -> int:
    rows = _daily_rows(session, recs, market, source, raw_id, "institutional_trades", _INST_FIELDS)
    return _upsert(session, InstitutionalTrade, rows, ["security_id", "trade_date"])


def upsert_margin(session, recs: list[MarginRecord], market, source, raw_id) -> int:
    rows = _daily_rows(session, recs, market, source, raw_id, "margin_trades", _MARGIN_FIELDS)
    return _upsert(session, MarginTrade, rows, ["security_id", "trade_date"])


def upsert_valuations(session, recs: list[ValuationRecord], market, source, raw_id) -> int:
    rows = _daily_rows(session, recs, market, source, raw_id, "valuations", _VAL_FIELDS)
    return _upsert(session, Valuation, rows, ["security_id", "trade_date"])


# ------------------------------------------------------------------ fundamentals
def upsert_revenues(session, recs: list[RevenueRecord], market, source, raw_id) -> int:
    ids = ensure_securities(session, [(r.code, r.name, market) for r in recs])
    rows = []
    for r in recs:
        d = asdict(r)
        for k in ("code", "name", "industry"):
            d.pop(k)
        d.update(security_id=ids[(r.code, market)], source=source, raw_payload_id=raw_id,
                 available_at=pit.monthly_revenue_available_at(r.period, r.published_date))
        rows.append(d)
    return _upsert_keep_earliest(session, MonthlyRevenue, rows, ["security_id", "period"])


def upsert_financials(session, recs: list[FinancialRecord], market, source, raw_id) -> int:
    ids = ensure_securities(session, [(r.code, r.name, market) for r in recs])
    rows = []
    for r in recs:
        d = {k: v for k, v in asdict(r).items() if k not in ("code", "name") and v is not None}
        d.update(security_id=ids[(r.code, market)], basis="CUMULATIVE", source=source, raw_payload_id=raw_id,
                 available_at=pit.financial_statement_available_at(r.fiscal_year, r.fiscal_quarter, r.published_date))
        if r.revenue and r.gross_profit is not None:
            d["gross_margin"] = round(r.gross_profit / r.revenue, 6)
        if r.revenue and r.operating_income is not None:
            d["operating_margin"] = round(r.operating_income / r.revenue, 6)
        rows.append(d)
    # income & balance files arrive separately -> group by key so each upsert has a uniform column set
    n = 0
    by_cols: dict[tuple, list] = {}
    for row in rows:
        by_cols.setdefault(tuple(sorted(row)), []).append(row)
    for group in by_cols.values():
        n += _upsert_keep_earliest(session, FinancialStatement, group,
                                   ["security_id", "fiscal_year", "fiscal_quarter", "basis"])
    return n


def _upsert_keep_earliest(session, model, rows, keys) -> int:
    """Upsert that never rewrites `available_at` / `published_date` once a row exists:
    a later re-fetch must not change when the system was allowed to know the value."""
    if not rows:
        return 0
    rows = list({tuple(r[k] for k in keys): r for r in rows}.values())
    for chunk in _chunks(rows):
        stmt = insert(model).values(chunk)
        upd = {c: stmt.excluded[c] for c in chunk[0] if c not in keys and c not in ("available_at", "published_date")}
        session.execute(stmt.on_conflict_do_update(index_elements=keys, set_=upd))
    return len(rows)


def upsert_corporate_actions(session, recs: list[CorporateActionRecord], market, source, raw_id) -> int:
    ids = ensure_securities(session, [(r.code, r.name, market) for r in recs])
    rows = [dict(security_id=ids[(r.code, market)], ex_date=r.ex_date, action_type=r.action_type,
                 prev_close=r.prev_close, ref_price=r.ref_price, cash_dividend=r.cash_dividend,
                 source=source, raw_payload_id=raw_id,
                 # TWSE announces ex-rights reference prices before the ex-date; usable from the ex-date open.
                 available_at=pit.at_tpe(r.ex_date, pit.time(8, 0))) for r in recs]
    return _upsert(session, CorporateAction, rows, ["security_id", "ex_date", "action_type"])


# ------------------------------------------------------------------ calendar
def upsert_calendar_day(session, market: str, d: date, is_trading: bool, source: str, note: str | None = None):
    _upsert(session, TradingCalendar, [dict(market=market, cal_date=d, is_trading_day=is_trading,
                                            source=source, note=note)], ["market", "cal_date"])


def upsert_holidays(session, recs: list[HolidayRecord], markets=("TWSE", "TPEX")) -> int:
    rows = []
    for r in recs:
        if r.cal_date.weekday() >= 5 and not r.is_trading_day:
            continue
        for m in markets:
            rows.append(dict(market=m, cal_date=r.cal_date, is_trading_day=r.is_trading_day,
                             source="holiday_schedule", note=r.name[:128]))
    # observed data beats the schedule -> do not overwrite rows from observed_data
    if not rows:
        return 0
    stmt = insert(TradingCalendar).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["market", "cal_date"],
        set_={"is_trading_day": stmt.excluded.is_trading_day, "note": stmt.excluded.note,
              "source": stmt.excluded.source},
        where=TradingCalendar.source != "observed_data")
    session.execute(stmt)
    return len(rows)


def get_last_job_success(session, job_name: str) -> datetime | None:
    from aqt.models import JobRun
    return session.scalar(select(JobRun.finished_at).where(JobRun.job_name == job_name, JobRun.status == "SUCCESS")
                          .order_by(JobRun.finished_at.desc()).limit(1))
