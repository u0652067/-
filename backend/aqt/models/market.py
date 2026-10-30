"""PHASE 1 market-data tables.

Point-in-time rule: every table whose data is *published* after the period it
describes carries `available_at` — the earliest moment the system is allowed to
use that row in a decision or backtest. Readers must filter on it.
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, LargeBinary, Numeric,
    String, Text, UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from aqt.models.base import Base, TimestampMixin, utcnow

PRICE = Numeric(12, 4)
RATIO = Numeric(14, 6)


class Security(Base, TimestampMixin):
    """Every security ever seen, including delisted ones (survivorship-bias guard)."""
    __tablename__ = "securities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(12), nullable=False)
    market: Mapped[str] = mapped_column(String(8), nullable=False)  # TWSE | TPEX
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    security_type: Mapped[str] = mapped_column(String(16), nullable=False, default="STOCK")  # STOCK|ETF|WARRANT|OTHER
    industry: Mapped[str | None] = mapped_column(String(64))
    isin: Mapped[str | None] = mapped_column(String(16))
    listed_date: Mapped[date | None] = mapped_column(Date)
    delisted_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")  # ACTIVE|DELISTED|SUSPENDED
    first_seen_date: Mapped[date | None] = mapped_column(Date)
    last_seen_date: Mapped[date | None] = mapped_column(Date)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("code", "market", name="uq_securities_code_market"),)


class TradingCalendar(Base):
    __tablename__ = "trading_calendar"
    market: Mapped[str] = mapped_column(String(8), primary_key=True)
    cal_date: Mapped[date] = mapped_column(Date, primary_key=True)
    is_trading_day: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)  # holiday_schedule | observed_data
    note: Mapped[str | None] = mapped_column(String(128))


class RawPayload(Base):
    """Every raw response we fetched, kept verbatim (rule 12: keep raw data + fetch time)."""
    __tablename__ = "raw_payloads"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    dataset: Mapped[str] = mapped_column(String(64), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    data_date: Mapped[date | None] = mapped_column(Date)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content_gz: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    parse_status: Mapped[str] = mapped_column(String(16), default="PENDING")  # PENDING|OK|EMPTY|ERROR
    parse_error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("ix_raw_payloads_dataset_date", "source", "dataset", "data_date"),)


class DailyPrice(Base):
    """Raw (unadjusted) end-of-day quotes. Adjustment is applied on read via adj_factor."""
    __tablename__ = "daily_prices"
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[Decimal | None] = mapped_column(PRICE)
    high: Mapped[Decimal | None] = mapped_column(PRICE)
    low: Mapped[Decimal | None] = mapped_column(PRICE)
    close: Mapped[Decimal | None] = mapped_column(PRICE)
    change: Mapped[Decimal | None] = mapped_column(PRICE)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)  # shares
    turnover: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)  # TWD
    transactions: Mapped[int | None] = mapped_column(Integer)
    prev_close: Mapped[Decimal | None] = mapped_column(PRICE)
    limit_up: Mapped[Decimal | None] = mapped_column(PRICE)
    limit_down: Mapped[Decimal | None] = mapped_column(PRICE)
    adj_factor: Mapped[Decimal] = mapped_column(Numeric(18, 10), nullable=False, default=1)
    is_suspended: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_payload_id: Mapped[int | None] = mapped_column(ForeignKey("raw_payloads.id"))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    __table_args__ = (Index("ix_daily_prices_date", "trade_date"),)


class MarketIndex(Base):
    __tablename__ = "market_indices"
    index_code: Mapped[str] = mapped_column(String(32), primary_key=True)  # TAIEX, TPEX, sector indices
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    close: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    change: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    change_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_payload_id: Mapped[int | None] = mapped_column(ForeignKey("raw_payloads.id"))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CorporateAction(Base):
    """Ex-rights / ex-dividend events used to build adj_factor."""
    __tablename__ = "corporate_actions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"), nullable=False)
    ex_date: Mapped[date] = mapped_column(Date, nullable=False)
    action_type: Mapped[str] = mapped_column(String(16), nullable=False)  # DIV | RIGHTS | DIV_RIGHTS | CAPITAL_REDUCTION
    prev_close: Mapped[Decimal | None] = mapped_column(PRICE)
    ref_price: Mapped[Decimal | None] = mapped_column(PRICE)
    cash_dividend: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    stock_dividend_ratio: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_payload_id: Mapped[int | None] = mapped_column(ForeignKey("raw_payloads.id"))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint("security_id", "ex_date", "action_type", name="uq_corp_action"),)


class TechnicalIndicator(Base):
    __tablename__ = "technical_indicators"
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    ma5: Mapped[float | None]
    ma10: Mapped[float | None]
    ma20: Mapped[float | None]
    ma60: Mapped[float | None]
    k9: Mapped[float | None]
    d9: Mapped[float | None]
    rsi6: Mapped[float | None]
    rsi14: Mapped[float | None]
    macd_dif: Mapped[float | None]
    macd_signal: Mapped[float | None]
    macd_hist: Mapped[float | None]
    bb_upper: Mapped[float | None]
    bb_mid: Mapped[float | None]
    bb_lower: Mapped[float | None]
    vol_ma5: Mapped[float | None]
    vol_ma20: Mapped[float | None]
    turnover_ma20: Mapped[float | None]
    atr14: Mapped[float | None]
    high_20: Mapped[float | None]
    low_20: Mapped[float | None]
    high_60: Mapped[float | None]
    volume_ratio: Mapped[float | None]  # today volume / vol_ma20 (prior 20 days)
    calc_version: Mapped[str] = mapped_column(String(16), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    __table_args__ = (Index("ix_technical_indicators_date", "trade_date"),)


class InstitutionalTrade(Base):
    """三大法人買賣超 (shares)."""
    __tablename__ = "institutional_trades"
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    foreign_buy: Mapped[int | None] = mapped_column(BigInteger)
    foreign_sell: Mapped[int | None] = mapped_column(BigInteger)
    foreign_net: Mapped[int | None] = mapped_column(BigInteger)  # 外陸資(不含外資自營商)
    foreign_dealer_net: Mapped[int | None] = mapped_column(BigInteger)
    trust_buy: Mapped[int | None] = mapped_column(BigInteger)
    trust_sell: Mapped[int | None] = mapped_column(BigInteger)
    trust_net: Mapped[int | None] = mapped_column(BigInteger)
    dealer_net: Mapped[int | None] = mapped_column(BigInteger)
    dealer_self_net: Mapped[int | None] = mapped_column(BigInteger)
    dealer_hedge_net: Mapped[int | None] = mapped_column(BigInteger)
    total_net: Mapped[int | None] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_payload_id: Mapped[int | None] = mapped_column(ForeignKey("raw_payloads.id"))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MarginTrade(Base):
    """融資融券 (units: 張 / lots of 1000 shares, as published)."""
    __tablename__ = "margin_trades"
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    margin_buy: Mapped[int | None] = mapped_column(BigInteger)
    margin_sell: Mapped[int | None] = mapped_column(BigInteger)
    margin_redeem: Mapped[int | None] = mapped_column(BigInteger)
    margin_balance_prev: Mapped[int | None] = mapped_column(BigInteger)
    margin_balance: Mapped[int | None] = mapped_column(BigInteger)
    margin_limit: Mapped[int | None] = mapped_column(BigInteger)
    short_cover: Mapped[int | None] = mapped_column(BigInteger)  # 融券買進
    short_sell: Mapped[int | None] = mapped_column(BigInteger)   # 融券賣出
    short_redeem: Mapped[int | None] = mapped_column(BigInteger)
    short_balance_prev: Mapped[int | None] = mapped_column(BigInteger)
    short_balance: Mapped[int | None] = mapped_column(BigInteger)
    short_limit: Mapped[int | None] = mapped_column(BigInteger)
    offset: Mapped[int | None] = mapped_column(BigInteger)  # 資券互抵
    note: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_payload_id: Mapped[int | None] = mapped_column(ForeignKey("raw_payloads.id"))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Valuation(Base):
    """本益比 / 殖利率 / 股價淨值比 as published by the exchange each day."""
    __tablename__ = "valuations"
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    pe_ratio: Mapped[Decimal | None] = mapped_column(RATIO)
    pb_ratio: Mapped[Decimal | None] = mapped_column(RATIO)
    dividend_yield: Mapped[Decimal | None] = mapped_column(RATIO)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_payload_id: Mapped[int | None] = mapped_column(ForeignKey("raw_payloads.id"))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MonthlyRevenue(Base):
    __tablename__ = "monthly_revenues"
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"), primary_key=True)
    period: Mapped[date] = mapped_column(Date, primary_key=True)  # first day of revenue month
    revenue: Mapped[int | None] = mapped_column(BigInteger)  # thousand TWD as published
    revenue_prev_month: Mapped[int | None] = mapped_column(BigInteger)
    revenue_last_year: Mapped[int | None] = mapped_column(BigInteger)
    mom_pct: Mapped[Decimal | None] = mapped_column(RATIO)
    yoy_pct: Mapped[Decimal | None] = mapped_column(RATIO)
    cum_revenue: Mapped[int | None] = mapped_column(BigInteger)
    cum_revenue_last_year: Mapped[int | None] = mapped_column(BigInteger)
    cum_yoy_pct: Mapped[Decimal | None] = mapped_column(RATIO)
    published_date: Mapped[date | None] = mapped_column(Date)  # actual announcement date when known
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_payload_id: Mapped[int | None] = mapped_column(ForeignKey("raw_payloads.id"))


class FinancialStatement(Base):
    """Quarterly (single-quarter or cumulative per `basis`) income-statement & balance-sheet items, thousand TWD."""
    __tablename__ = "financial_statements"
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"), primary_key=True)
    fiscal_year: Mapped[int] = mapped_column(Integer, primary_key=True)
    fiscal_quarter: Mapped[int] = mapped_column(Integer, primary_key=True)
    basis: Mapped[str] = mapped_column(String(12), primary_key=True, default="CUMULATIVE")  # CUMULATIVE | QUARTER
    revenue: Mapped[int | None] = mapped_column(BigInteger)
    gross_profit: Mapped[int | None] = mapped_column(BigInteger)
    operating_income: Mapped[int | None] = mapped_column(BigInteger)
    net_income: Mapped[int | None] = mapped_column(BigInteger)
    eps: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    gross_margin: Mapped[Decimal | None] = mapped_column(RATIO)
    operating_margin: Mapped[Decimal | None] = mapped_column(RATIO)
    total_assets: Mapped[int | None] = mapped_column(BigInteger)
    total_liabilities: Mapped[int | None] = mapped_column(BigInteger)
    equity: Mapped[int | None] = mapped_column(BigInteger)
    published_date: Mapped[date | None] = mapped_column(Date)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_payload_id: Mapped[int | None] = mapped_column(ForeignKey("raw_payloads.id"))


class IntradayQuote(Base):
    """Intraday snapshots. `delay_seconds` MUST be shown in the UI; no licensed real-time feed yet."""
    __tablename__ = "intraday_quotes"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"), nullable=False)
    quote_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)  # exchange timestamp
    price: Mapped[Decimal | None] = mapped_column(PRICE)
    cum_volume: Mapped[int | None] = mapped_column(BigInteger)
    best_bid: Mapped[Decimal | None] = mapped_column(PRICE)
    best_ask: Mapped[Decimal | None] = mapped_column(PRICE)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    delay_seconds: Mapped[float | None]
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    __table_args__ = (Index("ix_intraday_quotes_sec_time", "security_id", "quote_time"),)


class DataQualityIssue(Base, TimestampMixin):
    __tablename__ = "data_quality_issues"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    check_name: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)  # INFO|WARN|ERROR
    dataset: Mapped[str] = mapped_column(String(64), nullable=False)
    security_id: Mapped[int | None] = mapped_column(ForeignKey("securities.id"))
    trade_date: Mapped[date | None] = mapped_column(Date)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    __table_args__ = (Index("ix_dq_issues_date", "trade_date", "check_name"),)


class DataSourceStatus(Base):
    __tablename__ = "data_source_status"
    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    dataset: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="UNKNOWN")  # OK|DEGRADED|DOWN|UNKNOWN
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latest_data_date: Mapped[date | None] = mapped_column(Date)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
