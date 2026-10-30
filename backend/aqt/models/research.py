"""PHASE 2/4/6 research tables: strategies & versions, backtests, scans, regimes, reviews."""
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from aqt.models.base import Base, TimestampMixin


class Strategy(Base, TimestampMixin):
    __tablename__ = "strategies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(48), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    horizon: Mapped[str] = mapped_column(String(8), nullable=False)  # SHORT|SWING
    description: Mapped[str] = mapped_column(Text, default="")


class StrategyVersion(Base, TimestampMixin):
    """Immutable once created (no DELETE; params cannot change — create a new version instead)."""
    __tablename__ = "strategy_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"), nullable=False)
    version: Mapped[str] = mapped_column(String(16), nullable=False)  # V1.0, V1.1 ...
    parent_version_id: Mapped[int | None] = mapped_column(ForeignKey("strategy_versions.id"))
    params: Mapped[dict] = mapped_column(JSONB, nullable=False)
    code_ref: Mapped[str | None] = mapped_column(String(80))  # git commit of the rule implementation
    change_reason: Mapped[str] = mapped_column(Text, nullable=False)
    stage: Mapped[str] = mapped_column(String(16), nullable=False, default="CANDIDATE")
    # CANDIDATE -> BACKTESTED -> OOS_PASSED -> WF_PASSED -> PAPER -> ACTIVE ; or REJECTED / RETIRED
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("strategy_id", "version", name="uq_strategy_version"),)


class StrategyStageChange(Base, TimestampMixin):
    __tablename__ = "strategy_stage_changes"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    strategy_version_id: Mapped[int] = mapped_column(ForeignKey("strategy_versions.id"), nullable=False)
    from_stage: Mapped[str | None] = mapped_column(String(16))
    to_stage: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict)  # backtest ids, metrics


class BacktestRun(Base, TimestampMixin):
    __tablename__ = "backtest_runs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    strategy_version_id: Mapped[int] = mapped_column(ForeignKey("strategy_versions.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # IN_SAMPLE|OUT_OF_SAMPLE|WALK_FORWARD|PAPER
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    universe_rule: Mapped[str] = mapped_column(String(64), nullable=False)  # point-in-time universe id
    cost_model: Mapped[dict] = mapped_column(JSONB, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)
    regime_metrics: Mapped[dict] = mapped_column(JSONB, default=dict)
    equity_curve: Mapped[list] = mapped_column(JSONB, default=list)
    data_snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    code_ref: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(12), default="DONE")


class BacktestTrade(Base):
    __tablename__ = "backtest_trades"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    backtest_run_id: Mapped[int] = mapped_column(ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"), nullable=False)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    exit_date: Mapped[date | None] = mapped_column(Date)
    entry_price: Mapped[float]
    exit_price: Mapped[float | None]
    quantity: Mapped[int]
    costs: Mapped[float]
    net_pnl: Mapped[float | None]
    exit_reason: Mapped[str | None] = mapped_column(String(32))
    __table_args__ = (Index("ix_backtest_trades_run", "backtest_run_id"),)


class MarketRegime(Base):
    __tablename__ = "market_regimes"
    regime_date: Mapped[date] = mapped_column(Date, primary_key=True)
    regime: Mapped[str] = mapped_column(String(24), nullable=False)  # BULL_TREND|BEAR_TREND|RANGE|HIGH_VOL ...
    trend_score: Mapped[float | None]
    volatility: Mapped[float | None]
    breadth: Mapped[float | None]
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)


class ScanRun(Base, TimestampMixin):
    __tablename__ = "scan_runs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    scan_date: Mapped[date] = mapped_column(Date, nullable=False)
    data_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    universe_count: Mapped[int] = mapped_column(Integer, nullable=False)
    excluded_count: Mapped[int] = mapped_column(Integer, nullable=False)
    exclusions: Mapped[dict] = mapped_column(JSONB, default=dict)  # reason -> count
    scoring_version: Mapped[str] = mapped_column(String(16), nullable=False)


class StockScore(Base):
    __tablename__ = "stock_scores"
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"), primary_key=True)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"), primary_key=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    total: Mapped[float]
    trend: Mapped[float | None]
    momentum: Mapped[float | None]
    volume: Mapped[float | None]
    chip: Mapped[float | None]
    fundamental: Mapped[float | None]
    risk: Mapped[float | None]
    regime_fit: Mapped[float | None]
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)


class Review(Base, TimestampMixin):
    """Daily / weekly / monthly reviews & research reports. Append-only."""
    __tablename__ = "reviews"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    period_type: Mapped[str] = mapped_column(String(8), nullable=False)  # DAILY|WEEKLY|MONTHLY|QUARTERLY|YEARLY
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    findings: Mapped[dict] = mapped_column(JSONB, default=dict)
    llm_call_id: Mapped[int | None] = mapped_column(ForeignKey("llm_calls.id"))


class ResearchProposal(Base, TimestampMixin):
    __tablename__ = "research_proposals"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    origin_review_id: Mapped[int | None] = mapped_column(ForeignKey("reviews.id"))
    weakness: Mapped[str] = mapped_column(Text, nullable=False)
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    plan: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")  # OPEN|TESTING|ACCEPTED|REJECTED
    candidate_version_id: Mapped[int | None] = mapped_column(ForeignKey("strategy_versions.id"))
    conclusion: Mapped[str | None] = mapped_column(Text)
