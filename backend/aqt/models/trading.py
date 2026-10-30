"""PHASE 3 simulated-trading tables (schema defined now, engine built in PHASE 3).

Audit rules enforced in the database (see migration 0002):
  * fills, cash_ledger, order_events, trade_decisions are append-only
    (UPDATE/DELETE raise) and hash-chained (row_hash = sha256(prev_hash || payload)).
  * sim_accounts.mode is CHECK-constrained to 'SIMULATION'.
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer,
    Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from aqt.models.base import Base, TimestampMixin, utcnow

MONEY = Numeric(18, 4)
PRICE = Numeric(12, 4)


class SimAccount(Base, TimestampMixin):
    __tablename__ = "sim_accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="SIMULATION")
    initial_capital: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="STOPPED")  # RUNNING|STOPPED|SAFE_MODE
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("mode = 'SIMULATION'", name="simulation_only"),
                      CheckConstraint("initial_capital > 0", name="positive_capital"))


class AccountSettings(Base):
    """Current risk settings; every change is also appended to account_settings_history."""
    __tablename__ = "account_settings"
    account_id: Mapped[int] = mapped_column(ForeignKey("sim_accounts.id"), primary_key=True)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False, default="BALANCED")  # CONSERVATIVE|BALANCED|AGGRESSIVE
    position_size_pct: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False, default=Decimal("0.10"))
    max_positions: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    max_daily_loss_pct: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False, default=Decimal("0.02"))
    max_drawdown_pct: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False, default=Decimal("0.15"))
    stop_loss_pct: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False, default=Decimal("0.07"))
    take_profit_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), default=Decimal("0.20"))
    trailing_stop_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    allow_day_trade: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AccountSettingsHistory(Base, TimestampMixin):
    __tablename__ = "account_settings_history"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("sim_accounts.id"), nullable=False)
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False)
    changed_by: Mapped[str] = mapped_column(String(32), nullable=False, default="user")


class TradeDecision(Base):
    """Portfolio Manager decision incl. full reasoning — written BEFORE the order. Append-only."""
    __tablename__ = "trade_decisions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("sim_accounts.id"), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    data_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)  # latest data timestamp used
    security_id: Mapped[int | None] = mapped_column(ForeignKey("securities.id"))
    action: Mapped[str] = mapped_column(String(8), nullable=False)  # BUY|SELL|HOLD|SKIP
    strategy_version_id: Mapped[int | None] = mapped_column(ForeignKey("strategy_versions.id"))
    market_regime: Mapped[str | None] = mapped_column(String(32))
    score: Mapped[float | None]
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    analyst_inputs: Mapped[dict] = mapped_column(JSONB, default=dict)
    risk_check: Mapped[dict] = mapped_column(JSONB, default=dict)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False)
    planned_stop: Mapped[Decimal | None] = mapped_column(PRICE)
    planned_target: Mapped[Decimal | None] = mapped_column(PRICE)
    llm_call_id: Mapped[int | None] = mapped_column(ForeignKey("llm_calls.id"))
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    row_hash: Mapped[str | None] = mapped_column(String(64))


class Order(Base, TimestampMixin):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("sim_accounts.id"), nullable=False)
    decision_id: Mapped[int | None] = mapped_column(ForeignKey("trade_decisions.id"))
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)  # BUY|SELL
    order_type: Mapped[str] = mapped_column(String(8), nullable=False)  # LIMIT|MARKET
    time_in_force: Mapped[str] = mapped_column(String(8), nullable=False, default="ROD")  # ROD|IOC|FOK
    limit_price: Mapped[Decimal | None] = mapped_column(PRICE)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)  # shares
    filled_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="NEW")
    # NEW|ACCEPTED|PARTIALLY_FILLED|FILLED|CANCELLED|REJECTED|EXPIRED  (accepted != filled)
    reject_reason: Mapped[str | None] = mapped_column(Text)
    strategy_version_id: Mapped[int | None] = mapped_column(ForeignKey("strategy_versions.id"))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    stop_price: Mapped[Decimal | None] = mapped_column(PRICE)
    target_price: Mapped[Decimal | None] = mapped_column(PRICE)
    market_regime: Mapped[str | None] = mapped_column(String(32))
    __table_args__ = (CheckConstraint("quantity > 0", name="positive_qty"),
                      Index("ix_orders_account_created", "account_id", "created_at"))


class OrderEvent(Base):
    """Append-only lifecycle log of orders."""
    __tablename__ = "order_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    row_hash: Mapped[str | None] = mapped_column(String(64))


class Fill(Base):
    __tablename__ = "fills"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    account_id: Mapped[int] = mapped_column(ForeignKey("sim_accounts.id"), nullable=False)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    fill_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reference_price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)  # price before slippage
    fill_price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    gross_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    commission: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    tax: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    slippage_cost: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    net_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)  # signed cash impact
    price_source: Mapped[str] = mapped_column(String(32), nullable=False)
    price_delay_seconds: Mapped[float | None]
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    row_hash: Mapped[str | None] = mapped_column(String(64))


class Position(Base):
    __tablename__ = "positions"
    account_id: Mapped[int] = mapped_column(ForeignKey("sim_accounts.id"), primary_key=True)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"), primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_cost: Mapped[Decimal] = mapped_column(PRICE, nullable=False)  # incl. buy commission
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stop_price: Mapped[Decimal | None] = mapped_column(PRICE)
    target_price: Mapped[Decimal | None] = mapped_column(PRICE)
    strategy_version_id: Mapped[int | None] = mapped_column(ForeignKey("strategy_versions.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ClosedTrade(Base):
    """Round trip; win rate is computed only from these (never from open positions)."""
    __tablename__ = "closed_trades"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("sim_accounts.id"), nullable=False)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"), nullable=False)
    strategy_version_id: Mapped[int | None] = mapped_column(ForeignKey("strategy_versions.id"))
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exit_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_cost: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    exit_proceeds: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    total_costs: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    net_pnl: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    return_pct: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    holding_days: Mapped[int] = mapped_column(Integer, nullable=False)
    exit_reason: Mapped[str] = mapped_column(String(32), nullable=False)  # STOP|TARGET|SIGNAL|RISK|TIME
    entry_fill_ids: Mapped[list] = mapped_column(JSONB, default=list)
    exit_fill_ids: Mapped[list] = mapped_column(JSONB, default=list)


class CashLedger(Base):
    __tablename__ = "cash_ledger"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("sim_accounts.id"), nullable=False)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    entry_type: Mapped[str] = mapped_column(String(16), nullable=False)  # DEPOSIT|BUY|SELL|DIVIDEND|FEE
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    balance_after: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    fill_id: Mapped[int | None] = mapped_column(ForeignKey("fills.id"))
    settlement_date: Mapped[date | None] = mapped_column(Date)  # T+2
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    row_hash: Mapped[str | None] = mapped_column(String(64))


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"
    account_id: Mapped[int] = mapped_column(ForeignKey("sim_accounts.id"), primary_key=True)
    snap_date: Mapped[date] = mapped_column(Date, primary_key=True)
    cash: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    market_value: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    total_equity: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    unrealized_pnl: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    peak_equity: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    drawdown_pct: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    benchmark_close: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    __table_args__ = (UniqueConstraint("account_id", "snap_date", name="uq_equity_snap"),)
