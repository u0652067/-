"""Single source of truth for "may this security be traded on this day?" (G6 / G8 / G13).

`build_universe` calls `blocked_securities`; signal generation, backtests and the simulation engine
must take their candidates from `build_universe` (or call `blocked_securities` directly) — never
from raw price tables — so every consumer applies the same exclusions.

Exclusions, all point-in-time (only information available by the close of `as_of`):
  * exchange restrictions active on `as_of` or announced by `as_of` for the next trading day:
    disposition (處置), altered trading / full delivery (變更交易・全額交割), periodic auction (分盤),
    managed stock (管理股票), stop trading (停止交易), trading halt (暫停交易);
  * unexplained price anomalies (data-quality issues with blocks_backtest) within the last
    QUALITY_BLOCK_DAYS calendar days — the adjusted price history around them is not trustworthy.
"""
from datetime import date, datetime, time, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from aqt.data.pit import TPE
from aqt.models import DataQualityIssue, TradingRestriction

QUALITY_BLOCK_DAYS = 120  # covers the longest indicator look-back (MA60 ≈ 90 calendar days)
NEXT_DAY_HORIZON = 4  # calendar days: a restriction starting by the next trading day (weekend-safe)
REASON = {
    "DISPOSITION": "disposition", "ALTERED_TRADING": "altered_trading_full_delivery",
    "PERIODIC_AUCTION": "periodic_auction", "MANAGED": "managed_stock", "STOP_TRADING": "stop_trading",
    "SUSPENDED": "trading_halt",
}


def blocked_securities(session: Session, as_of: date) -> dict[int, str]:
    known_by = datetime.combine(as_of, time(23, 59, 59), tzinfo=TPE)
    out: dict[int, str] = {}
    rows = session.execute(
        select(TradingRestriction.security_id, TradingRestriction.restriction_type)
        .where(TradingRestriction.available_at <= known_by,
               TradingRestriction.start_date <= as_of + timedelta(days=NEXT_DAY_HORIZON),
               or_(TradingRestriction.end_date.is_(None), TradingRestriction.end_date >= as_of),
               # daily snapshots only describe their own day; a range restriction may start tomorrow
               or_(TradingRestriction.end_date.is_(None), TradingRestriction.end_date != TradingRestriction.start_date,
                   TradingRestriction.start_date == as_of))).all()
    for sid, kind in rows:
        out.setdefault(sid, REASON.get(kind, kind.lower()))
    q = (select(DataQualityIssue.security_id)
         .where(DataQualityIssue.blocks_backtest.is_(True), DataQualityIssue.security_id.is_not(None),
                DataQualityIssue.trade_date <= as_of,
                DataQualityIssue.trade_date > as_of - timedelta(days=QUALITY_BLOCK_DAYS)))
    for (sid,) in session.execute(q):
        out.setdefault(sid, "unexplained_price_anomaly")
    return out


def is_tradable(session: Session, security_id: int, as_of: date) -> tuple[bool, str | None]:
    reason = blocked_securities(session, as_of).get(security_id)
    return reason is None, reason
