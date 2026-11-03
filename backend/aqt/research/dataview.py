"""Point-in-time data access for strategies.

A `DataView` is created by the engine for one decision day `as_of` (decision after the close, at
`decision_time`). Every query is bounded by `trade_date <= as_of` and `available_at <= decision_time`;
strategies get no session and no way to ask for later data.

Adjusted prices are re-based to `as_of` (adj(s) / adj(as_of)), which only uses corporate actions that
happened on or before `as_of` — the stored backward factors also encode later events, which a strategy
must not see. Technical indicators are stored on the backward-adjusted scale, so only scale-free
comparisons (ratios such as close / MA20) are meaningful; absolute price thresholds must use raw prices.
"""
from datetime import date, datetime, time, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from aqt.data.pit import TPE
from aqt.data.universe import Universe, build_universe
from aqt.models import DailyPrice, MarketIndex, Security, TechnicalIndicator

DEFAULT_DECISION_TIME = time(18, 0)  # after quotes (14:30), institutional (16:30), valuations (17:30)


class FundamentalsNotAllowed(RuntimeError):
    """Fundamental data is refused in technical backtests (fundamental_backtest_ready is false)."""


class DataView:
    def __init__(self, session: Session, as_of: date, decision_time: time = DEFAULT_DECISION_TIME,
                 allow_fundamentals: bool = False):
        self._s = session
        self.as_of = as_of
        self.decision_at = datetime.combine(as_of, decision_time, tzinfo=TPE)
        self._allow_fundamentals = allow_fundamentals
        self._universe: Universe | None = None

    # ------------------------------------------------------------------ universe
    def universe(self) -> Universe:
        """The only source of tradable candidates (restrictions, quality blocks, liquidity, history)."""
        if self._universe is None:
            self._universe = build_universe(self._s, self.as_of)
        return self._universe

    # ------------------------------------------------------------------ prices
    def history(self, security_ids: list[int], lookback_days: int = 90) -> pd.DataFrame:
        """Columns: sid, d, close (raw), adj_close (re-based to as_of), volume, turnover."""
        if not security_ids:
            return pd.DataFrame(columns=["sid", "d", "close", "adj_close", "volume", "turnover"])
        q = (select(DailyPrice.security_id, DailyPrice.trade_date, DailyPrice.close, DailyPrice.adj_factor,
                    DailyPrice.volume, DailyPrice.turnover)
             .where(DailyPrice.security_id.in_(security_ids), DailyPrice.trade_date <= self.as_of,
                    DailyPrice.trade_date > self.as_of - timedelta(days=lookback_days * 2 + 10),
                    DailyPrice.available_at <= self.decision_at, DailyPrice.close.is_not(None))
             .order_by(DailyPrice.security_id, DailyPrice.trade_date))
        df = pd.DataFrame(self._s.execute(q).all(), columns=["sid", "d", "close", "adj", "volume", "turnover"])
        if df.empty:
            return df.assign(adj_close=[]).drop(columns="adj")
        df["close"] = df["close"].astype(float)
        df["adj"] = df["adj"].astype(float)
        last_adj = df.groupby("sid")["adj"].transform("last")  # factor on the latest row (<= as_of)
        df["adj_close"] = df["close"] * df["adj"] / last_adj
        df = df.groupby("sid", group_keys=False).tail(lookback_days)
        return df.drop(columns="adj").reset_index(drop=True)

    def indicators(self, security_ids: list[int], columns: tuple[str, ...] = ("ma20", "ma60")) -> pd.DataFrame:
        """Indicators of `as_of` itself plus the adjusted close on the same scale (ratio use only)."""
        if not security_ids:
            return pd.DataFrame(columns=["sid", "adj_close_stored", *columns])
        cols = [getattr(TechnicalIndicator, c) for c in columns]
        q = (select(TechnicalIndicator.security_id, DailyPrice.close * DailyPrice.adj_factor, *cols)
             .join(DailyPrice, (DailyPrice.security_id == TechnicalIndicator.security_id)
                   & (DailyPrice.trade_date == TechnicalIndicator.trade_date))
             .where(TechnicalIndicator.security_id.in_(security_ids), TechnicalIndicator.trade_date == self.as_of,
                    DailyPrice.available_at <= self.decision_at))
        df = pd.DataFrame(self._s.execute(q).all(), columns=["sid", "adj_close_stored", *columns])
        return df.astype({c: float for c in ["adj_close_stored", *columns]}) if not df.empty else df

    def index_close(self, index_code: str = "TAIEX", lookback_days: int = 260) -> pd.Series:
        q = (select(MarketIndex.trade_date, MarketIndex.close)
             .where(MarketIndex.index_code == index_code, MarketIndex.trade_date <= self.as_of,
                    MarketIndex.available_at <= self.decision_at)
             .order_by(MarketIndex.trade_date.desc()).limit(lookback_days))
        rows = self._s.execute(q).all()[::-1]
        return pd.Series([float(c) for _, c in rows], index=[d for d, _ in rows], dtype=float)

    def security_types(self, security_ids: list[int]) -> dict[int, str]:
        return dict(self._s.execute(select(Security.id, Security.security_type)
                                    .where(Security.id.in_(security_ids))).all())

    # ------------------------------------------------------------------ fundamentals (gated)
    def fundamentals(self, security_ids: list[int]):
        if not self._allow_fundamentals:
            raise FundamentalsNotAllowed(
                "fundamental data is disabled for technical backtests (fundamental_backtest_ready = false)")
        from aqt.data.fundamentals import latest_statements_as_of
        return latest_statements_as_of(self._s, self.decision_at, security_ids)
