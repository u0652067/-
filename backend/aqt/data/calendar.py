"""Trading calendar: holiday schedule for planning, observed market data as ground truth."""
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aqt.models import DailyPrice, Security, TradingCalendar


def is_trading_day(session: Session, d: date, market: str = "TWSE") -> bool | None:
    """True/False when known; None if neither the schedule nor observed data covers the day."""
    row = session.get(TradingCalendar, (market, d))
    if row is not None:
        return row.is_trading_day
    if d.weekday() >= 5:
        return False
    return None


def expected_trading_day(session: Session, d: date, market: str = "TWSE") -> bool:
    """Best guess for scheduling: unknown weekdays are assumed to be trading days."""
    v = is_trading_day(session, d, market)
    return True if v is None else v


def observed_trading_dates(session: Session, market: str, start: date, end: date) -> list[date]:
    q = (select(DailyPrice.trade_date).join(Security, Security.id == DailyPrice.security_id)
         .where(Security.market == market, DailyPrice.trade_date.between(start, end))
         .group_by(DailyPrice.trade_date).order_by(DailyPrice.trade_date))
    return list(session.scalars(q))


def previous_observed_trading_date(session: Session, market: str, d: date) -> date | None:
    q = (select(func.max(DailyPrice.trade_date)).join(Security, Security.id == DailyPrice.security_id)
         .where(Security.market == market, DailyPrice.trade_date < d))
    return session.scalar(q)


def last_expected_trading_day(session: Session, today: date, market: str = "TWSE") -> date:
    d = today
    for _ in range(30):
        if expected_trading_day(session, d, market):
            return d
        d -= timedelta(days=1)
    return d
