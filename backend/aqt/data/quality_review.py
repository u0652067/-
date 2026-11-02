"""G13: classify every `move_exceeds_limit` warning and block what cannot be explained.

A close more than 10% away from the previous close is impossible under the ±10% rule unless:
  * CORPORATE_ACTION_*  — an ex-rights/ex-dividend, capital reduction, par-value change or split on that
                          day (the limit is computed on the exchange's reference price, not the raw close);
  * NEW_LISTING_NO_LIMIT — the first five trading days after (re)listing have no price limit;
  * ETF_NO_PRICE_LIMIT_UNVERIFIED — ETFs with foreign constituents / futures ETFs have no limit; we cannot
                          yet verify which ETF is which, so these stay blocked (ETFs are not in the stock
                          universe anyway);
  * DATA_SOURCE_ERROR   — an independent official per-stock series disagrees with what we stored;
  * UNEXPLAINED         — none of the above: promoted to ERROR and blocked from backtests.

Independent check: TWSE STOCK_DAY (per stock, per month) / TPEx tradingStock — a different report
than the market-wide files the pipeline ingests.
"""
import logging
import random
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from aqt.data.http import Fetcher
from aqt.data.parsing import roc_to_date, to_decimal
from aqt.data.quality import LIMIT_TOLERANCE
from aqt.db import session_scope
from aqt.models import CorporateAction, DailyPrice, DataQualityIssue, Security
from aqt.models.base import utcnow

log = logging.getLogger(__name__)
NEW_LISTING_FREE_DAYS = 5
BLOCKING = {"ETF_NO_PRICE_LIMIT_UNVERIFIED", "DATA_SOURCE_ERROR", "UNEXPLAINED", "CORPORATE_ACTION_MISMATCH"}


def _independent_closes(fetcher: Fetcher, market: str, code: str, d: date) -> dict[date, Decimal]:
    first = d.replace(day=1)
    if market == "TWSE":
        res = fetcher.get("twse", "verify_stock_day", "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY",
                          {"date": first.strftime("%Y%m%d"), "stockNo": code, "response": "json"}, d)
        p = res.json()
        rows, ic = p.get("data") or [], 6
    else:
        res = fetcher.get("tpex", "verify_trading_stock", "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock",
                          {"code": code, "date": first.strftime("%Y/%m/%d"), "response": "json"}, d)
        p = res.json()
        rows, ic = ((p.get("tables") or [{}])[0].get("data") or []), 6
    out = {}
    for r in rows:
        try:
            out[roc_to_date(str(r[0]).strip())] = to_decimal(r[ic])
        except (ValueError, IndexError):
            continue
    return out


def verify_against_independent(fetcher: Fetcher, market: str, code: str, d: date, close, prev_day: date | None,
                               prev_close_raw) -> dict:
    got = _independent_closes(fetcher, market, code, d)
    if prev_day is not None and prev_day.month != d.month:
        got.update(_independent_closes(fetcher, market, code, prev_day))
    ind_close, ind_prev = got.get(d), got.get(prev_day) if prev_day else None
    ok = ind_close is not None and Decimal(str(close)) == ind_close and (
        prev_day is None or ind_prev is None or Decimal(str(prev_close_raw)) == ind_prev)
    return {"independent_close": str(ind_close), "independent_prev_close": str(ind_prev),
            "stored_close": str(close), "stored_prev_raw_close": str(prev_close_raw), "match": ok}


def _rows(engine: Engine, only_date: date | None = None):
    q = (select(DataQualityIssue.id, DataQualityIssue.security_id, DataQualityIssue.trade_date,
                Security.code, Security.market, Security.security_type, Security.listed_date)
         .join(Security, Security.id == DataQualityIssue.security_id)
         .where(DataQualityIssue.check_name == "move_exceeds_limit"))
    if only_date is not None:
        q = q.where(DataQualityIssue.trade_date == only_date)
    with session_scope(engine) as s:
        return s.execute(q).all()


def _context(s, sid: int, d: date):
    p = s.get(DailyPrice, (sid, d))
    prev = s.execute(select(DailyPrice.trade_date, DailyPrice.close)
                     .where(DailyPrice.security_id == sid, DailyPrice.trade_date < d,
                            DailyPrice.close.is_not(None))
                     .order_by(DailyPrice.trade_date.desc()).limit(1)).first()
    acts = [a for (a,) in s.execute(select(CorporateAction.action_type)
                                    .where(CorporateAction.security_id == sid, CorporateAction.ex_date == d))]
    return p, prev, acts


def classify_move_issues(engine: Engine, fetcher: Fetcher | None = None, verify: bool = True,
                         only_date: date | None = None) -> dict:
    """Classify (or re-classify) every move_exceeds_limit issue. With a fetcher, issues that are not
    explained by an event or a listing are checked against the independent per-stock series."""
    counts: dict[str, int] = {}
    now = utcnow()
    for iid, sid, d, code, market, stype, listed in _rows(engine, only_date):
        with session_scope(engine) as s:
            p, prev, acts = _context(s, sid, d)
            detail: dict = {}
            if p is None or p.close is None or p.prev_close is None:
                cls = "UNEXPLAINED"
            elif acts:
                move = abs(p.close - p.prev_close) / p.prev_close
                cls = (f"CORPORATE_ACTION_{acts[0]}" if move <= Decimal("0.10") + LIMIT_TOLERANCE
                       else "CORPORATE_ACTION_MISMATCH")
                detail = {"actions": acts, "move_vs_reference": f"{move:.4f}"}
            elif listed and d >= listed and s.scalar(
                    select(func.count()).select_from(DailyPrice)
                    .where(DailyPrice.security_id == sid, DailyPrice.trade_date.between(listed, d))) \
                    <= NEW_LISTING_FREE_DAYS:
                cls, detail = "NEW_LISTING_NO_LIMIT", {"listed_date": str(listed)}
            elif stype == "ETF":
                cls = "ETF_NO_PRICE_LIMIT_UNVERIFIED"
            else:
                cls = "UNEXPLAINED"
                if fetcher is not None and verify:
                    try:
                        v = verify_against_independent(fetcher, market, code, d, p.close,
                                                       prev[0] if prev else None, prev[1] if prev else None)
                        detail["verification"] = v
                        if not v["match"]:
                            cls = "DATA_SOURCE_ERROR"
                    except Exception as e:  # verification failure keeps the issue blocked
                        detail["verification_error"] = str(e)[:300]
            row = s.get(DataQualityIssue, iid)
            row.classification, row.reviewed_at = cls, now
            row.blocks_backtest = cls in BLOCKING
            row.resolved = cls not in BLOCKING
            if cls in ("UNEXPLAINED", "DATA_SOURCE_ERROR", "CORPORATE_ACTION_MISMATCH"):
                row.severity = "ERROR"
            row.detail = {**(row.detail or {}), "review": detail} if detail else row.detail
        counts[cls] = counts.get(cls, 0) + 1
    return counts


def classify_known_checks(engine: Engine) -> int:
    """Checks whose cause is known by construction (e.g. a day with only after-hours fixed-price
    trades has volume but no close): the day is already excluded by its ERROR severity."""
    from sqlalchemy import update
    with session_scope(engine) as s:
        res = s.execute(update(DataQualityIssue)
                        .where(DataQualityIssue.check_name == "volume_without_close",
                               DataQualityIssue.classification.is_(None))
                        .values(classification="NO_CLOSE_FIXED_PRICE_ONLY", reviewed_at=utcnow()))
        return res.rowcount or 0


def sample_verification(engine: Engine, fetcher: Fetcher, per_class: int = 5, seed: int = 7) -> list[dict]:
    """Spot-check stored prices of a random sample per classification against the independent series."""
    rnd = random.Random(seed)
    with session_scope(engine) as s:
        rows = s.execute(select(DataQualityIssue.classification, DataQualityIssue.security_id,
                                DataQualityIssue.trade_date, Security.code, Security.market)
                         .join(Security, Security.id == DataQualityIssue.security_id)
                         .where(DataQualityIssue.check_name == "move_exceeds_limit")).all()
    by: dict[str, list] = {}
    for r in rows:
        by.setdefault(r[0], []).append(r)
    out = []
    for cls, items in sorted(by.items()):
        for c, sid, d, code, market in rnd.sample(items, min(per_class, len(items))):
            with session_scope(engine) as s:
                p, prev, acts = _context(s, sid, d)
                close, prev_d, prev_c = p.close, prev[0] if prev else None, prev[1] if prev else None
                ref = p.prev_close
            try:
                v = verify_against_independent(fetcher, market, code, d, close, prev_d, prev_c)
            except Exception as e:
                v = {"match": None, "error": str(e)[:200]}
            out.append({"classification": cls, "code": code, "market": market, "date": str(d),
                        "reference_prev_close": str(ref), "corporate_actions": acts, **v})
    return out


def summary(engine: Engine) -> list[dict]:
    with session_scope(engine) as s:
        rows = s.execute(select(DataQualityIssue.check_name, DataQualityIssue.severity,
                                DataQualityIssue.classification, DataQualityIssue.blocks_backtest, func.count())
                         .group_by(DataQualityIssue.check_name, DataQualityIssue.severity,
                                   DataQualityIssue.classification, DataQualityIssue.blocks_backtest)
                         .order_by(DataQualityIssue.check_name)).all()
    return [dict(check=a, severity=b, classification=c, blocks_backtest=d, n=n) for a, b, c, d, n in rows]
