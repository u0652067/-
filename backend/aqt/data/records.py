"""Source-agnostic records produced by parsers and consumed by the repository layer."""
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal


@dataclass
class SecurityRecord:
    code: str
    name: str
    market: str  # TWSE | TPEX
    security_type: str = "STOCK"
    isin: str | None = None
    listed_date: date | None = None
    industry: str | None = None


@dataclass
class PriceRecord:
    code: str
    name: str
    market: str
    trade_date: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    change: Decimal | None
    volume: int
    turnover: int
    transactions: int | None = None
    # TPEx publishes next-day reference/limit prices; kept for cross-checking
    next_ref_price: Decimal | None = None


@dataclass
class IndexRecord:
    index_code: str
    name: str
    trade_date: date
    close: Decimal | None
    change: Decimal | None
    change_pct: Decimal | None


@dataclass
class InstitutionalRecord:
    code: str
    name: str
    trade_date: date
    foreign_buy: int | None = None
    foreign_sell: int | None = None
    foreign_net: int | None = None
    foreign_dealer_net: int | None = None
    trust_buy: int | None = None
    trust_sell: int | None = None
    trust_net: int | None = None
    dealer_net: int | None = None
    dealer_self_net: int | None = None
    dealer_hedge_net: int | None = None
    total_net: int | None = None


@dataclass
class MarginRecord:
    code: str
    name: str
    trade_date: date
    margin_buy: int | None = None
    margin_sell: int | None = None
    margin_redeem: int | None = None
    margin_balance_prev: int | None = None
    margin_balance: int | None = None
    margin_limit: int | None = None
    short_cover: int | None = None
    short_sell: int | None = None
    short_redeem: int | None = None
    short_balance_prev: int | None = None
    short_balance: int | None = None
    short_limit: int | None = None
    offset: int | None = None
    note: str | None = None


@dataclass
class ValuationRecord:
    code: str
    name: str
    trade_date: date
    pe_ratio: Decimal | None
    pb_ratio: Decimal | None
    dividend_yield: Decimal | None


@dataclass
class RevenueRecord:
    code: str
    name: str
    period: date
    revenue: int | None
    revenue_prev_month: int | None = None
    revenue_last_year: int | None = None
    mom_pct: Decimal | None = None
    yoy_pct: Decimal | None = None
    cum_revenue: int | None = None
    cum_revenue_last_year: int | None = None
    cum_yoy_pct: Decimal | None = None
    published_date: date | None = None
    industry: str | None = None


@dataclass
class FinancialRecord:
    code: str
    name: str
    fiscal_year: int
    fiscal_quarter: int
    revenue: int | None = None
    gross_profit: int | None = None
    operating_income: int | None = None
    net_income: int | None = None
    eps: Decimal | None = None
    total_assets: int | None = None
    total_liabilities: int | None = None
    equity: int | None = None
    published_date: date | None = None


@dataclass
class CorporateActionRecord:
    code: str
    name: str
    ex_date: date
    action_type: str
    prev_close: Decimal | None
    ref_price: Decimal | None
    cash_dividend: Decimal | None = None


@dataclass
class HolidayRecord:
    cal_date: date
    name: str
    is_trading_day: bool


@dataclass
class IntradayRecord:
    code: str
    market: str
    quote_time: datetime
    price: Decimal | None
    cum_volume: int | None
    best_bid: Decimal | None
    best_ask: Decimal | None
    delay_seconds: float | None


@dataclass
class ParseResult:
    """Parser output: records plus what the parser decided about the payload."""
    records: list = field(default_factory=list)
    indices: list = field(default_factory=list)
    is_trading_day: bool | None = None  # None = cannot tell
    note: str | None = None
