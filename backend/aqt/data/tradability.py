"""Single source of truth for "may this security be traded on this day?" (G6 / G8 / G13).

`build_universe` calls `blocked_securities`; signal generation, backtests and the simulation engine
must take their candidates from `build_universe` (or call `blocked_securities` directly) — never
from raw price tables — so every consumer applies the same exclusions.

Exclusions, all point-in-time (only information known at the explicit `known_at`):
  * exchange restrictions active on `as_of` or announced by `as_of` for the next trading day:
    disposition (處置), altered trading / full delivery (變更交易・全額交割), periodic auction (分盤),
    managed stock (管理股票), stop trading (停止交易), trading halt (暫停交易);
  * unexplained price anomalies (data-quality issues with blocks_backtest) within the last
    QUALITY_BLOCK_DAYS calendar days — the adjusted price history around them is not trustworthy.
"""
from datetime import date, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from aqt.models import DataQualityIssue, TradingRestriction

QUALITY_BLOCK_DAYS = 120  # covers the longest indicator look-back (MA60 ≈ 90 calendar days)
NEXT_DAY_HORIZON = 4  # calendar days: a restriction starting by the next trading day (weekend-safe)
REASON = {
    "DISPOSITION": "disposition", "ALTERED_TRADING": "altered_trading_full_delivery",
    "PERIODIC_AUCTION": "periodic_auction", "MANAGED": "managed_stock", "STOP_TRADING": "stop_trading",
    "SUSPENDED": "trading_halt",
}


def blocked_securities(session: Session, as_of: date, known_at: datetime,
                       include_upcoming: bool = True) -> dict[int, str]:
    """Securities not tradable on `as_of`, using only what was known at `known_at` (timezone-aware).

    Decisions pass as_of T and known_at = T 18:00, with include_upcoming so a restriction announced for
    the next trading day already counts. An open fill passes as_of T+1, known_at = T+1 09:00 and
    include_upcoming=False: only restrictions in force that day and public before the open apply.
    """
    if known_at.tzinfo is None:
        raise ValueError("known_at must be timezone-aware")
    horizon = NEXT_DAY_HORIZON if include_upcoming else 0
    out: dict[int, str] = {}
    rows = session.execute(
        select(TradingRestriction.security_id, TradingRestriction.restriction_type)
        .where(TradingRestriction.available_at <= known_at,
               TradingRestriction.start_date <= as_of + timedelta(days=horizon),
               or_(TradingRestriction.end_date.is_(None), TradingRestriction.end_date >= as_of),
               # daily snapshots only describe their own day; a range restriction may start tomorrow
               or_(TradingRestriction.end_date.is_(None), TradingRestriction.end_date != TradingRestriction.start_date,
                   TradingRestriction.start_date == as_of))).all()
    for sid, kind in rows:
        out.setdefault(sid, REASON.get(kind, kind.lower()))
    q = (select(DataQualityIssue.security_id)
         .where(DataQualityIssue.blocks_backtest.is_(True), DataQualityIssue.security_id.is_not(None),
                DataQualityIssue.effective_at <= known_at,  # a later discovery never rewrites this moment
                DataQualityIssue.trade_date <= as_of,
                DataQualityIssue.trade_date > as_of - timedelta(days=QUALITY_BLOCK_DAYS)))
    for (sid,) in session.execute(q):
        out.setdefault(sid, "unexplained_price_anomaly")
    return out


def is_tradable(session: Session, security_id: int, as_of: date, known_at: datetime) -> tuple[bool, str | None]:
    reason = blocked_securities(session, as_of, known_at).get(security_id)
    return reason is None, reason
