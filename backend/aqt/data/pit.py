"""Point-in-time availability rules.

`available_at` is the earliest moment a datum may influence a decision or a backtest.
When the true publication time is unknown we use a conservative (late) bound, never an early one.
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TPE = ZoneInfo("Asia/Taipei")

# Typical publication times of exchange after-hours reports, padded conservatively.
DATASET_PUBLISH_TIME = {
    "daily_prices": time(14, 30),
    "market_indices": time(14, 30),
    "institutional_trades": time(16, 30),
    "valuations": time(17, 30),
    "margin_trades": time(21, 30),
}


def at_tpe(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=TPE)


def daily_dataset_available_at(dataset: str, trade_date: date) -> datetime:
    return at_tpe(trade_date, DATASET_PUBLISH_TIME[dataset])


def monthly_revenue_available_at(period: date, published_date: date | None = None) -> datetime:
    """Listed companies must report month M revenue by the 10th of month M+1.

    Known publication date -> usable from the next calendar day 00:00 (time-of-day unknown).
    Unknown -> usable from the 11th of M+1.
    """
    if published_date is not None:
        return at_tpe(published_date + timedelta(days=1), time(0, 0))
    y, m = (period.year + 1, 1) if period.month == 12 else (period.year, period.month + 1)
    return at_tpe(date(y, m, 11), time(0, 0))


def financial_statement_deadline(fiscal_year: int, quarter: int) -> date:
    """Statutory filing deadlines for general (non-financial) listed companies."""
    if quarter == 1:
        return date(fiscal_year, 5, 15)
    if quarter == 2:
        return date(fiscal_year, 8, 14)
    if quarter == 3:
        return date(fiscal_year, 11, 14)
    if quarter == 4:
        return date(fiscal_year + 1, 3, 31)
    raise ValueError(quarter)


def financial_statement_available_at(fiscal_year: int, quarter: int, published_date: date | None = None) -> datetime:
    if published_date is not None:
        return at_tpe(published_date + timedelta(days=1), time(0, 0))
    return at_tpe(financial_statement_deadline(fiscal_year, quarter) + timedelta(days=1), time(0, 0))
