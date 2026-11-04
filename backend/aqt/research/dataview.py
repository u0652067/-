"""Point-in-time data access for strategies.

A `DataView` is created by the engine for one decision day `as_of` (decision after the close, at
`decision_time`). Every query is bounded by `trade_date <= as_of` and `available_at <= decision_time`;
strategies get no session and no way to ask for later data.

Adjusted prices are rebuilt here from the corporate actions that were *public* at the decision time
(`ex_date <= as_of` and `available_at <= decision_time`) — the stored backward factors and the stored
technical-indicator table also encode events published later, so neither is used. Indicators offered
to strategies (moving averages, average turnover) are computed from this point-in-time history.
"""
from datetime import date, datetime, time, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from aqt.data.pit import DECISION_TIME, TPE
from aqt.data.universe import Universe, build_universe
from aqt.models import CorporateAction, DailyPrice, MarketIndex, Security

DEFAULT_DECISION_TIME = DECISION_TIME  # after quotes (14:30), institutional (16:30), valuations (17:30)
ALL_MARKETS = ("TWSE", "TPEX")


class FundamentalsNotAllowed(RuntimeError):
    """Fundamental data is refused in technical backtests (fundamental_backtest_ready is not true)."""


class DataView:
    def __init__(self, session: Session, as_of: date, decision_time: time = DEFAULT_DECISION_TIME,
                 allow_fundamentals: bool = False, markets: tuple[str, ...] = ALL_MARKETS,
                 universe_cache: dict | None = None):
        self._s = session
        self.markets = tuple(markets)
        self._cache = universe_cache  # same as_of and decision time -> same universe, within one run
        self.as_of = as_of
        self.decision_at = datetime.combine(as_of, decision_time, tzinfo=TPE)
        self._allow_fundamentals = allow_fundamentals
        self._universe: Universe | None = None

    # ------------------------------------------------------------------ universe
    def universe(self) -> Universe:
        """The only source of tradable candidates (restrictions, quality blocks, liquidity, history)."""
        if self._universe is None:
            key = (self.as_of, self.decision_at)
            u = self._cache.get(key) if self._cache is not None else None
            if u is None:
                u = build_universe(self._s, self.as_of, known_at=self.decision_at)
                if self._cache is not None:
                    self._cache[key] = u
            u = Universe(u.as_of, list(u.eligible), dict(u.excluded))
            if set(self.markets) != set(ALL_MARKETS):  # strategy restricted to the benchmark's market
                inside = set(self._s.scalars(select(Security.id).where(Security.market.in_(self.markets))))
                for sid in [s for s in u.eligible if s not in inside]:
                    u.excluded[sid] = "outside_market_scope"
                u.eligible = [s for s in u.eligible if s in inside]
            self._universe = u
        return self._universe

    # ------------------------------------------------------------------ prices
    def _pit_factors(self, security_ids: list[int], since: date) -> dict[int, list[tuple[date, float]]]:
        rows = self._s.execute(select(CorporateAction.security_id, CorporateAction.ex_date,
                                      CorporateAction.prev_close, CorporateAction.ref_price)
                               .where(CorporateAction.security_id.in_(security_ids),
                                      CorporateAction.ex_date > since, CorporateAction.ex_date <= self.as_of,
                                      CorporateAction.available_at <= self.decision_at)).all()
        out: dict[int, list] = {}
        for sid, ex, pc, ref in rows:
            if pc and ref and pc > 0:
                out.setdefault(sid, []).append((ex, float(ref / pc)))
        return out

    def history(self, security_ids: list[int], lookback_days: int = 90) -> pd.DataFrame:
        """Columns: sid, d, close (raw), adj_close (point-in-time, on the as_of scale), volume, turnover."""
        cols = ["sid", "d", "close", "adj_close", "volume", "turnover"]
        if not security_ids:
            return pd.DataFrame(columns=cols)
        since = self.as_of - timedelta(days=lookback_days * 2 + 10)
        q = (select(DailyPrice.security_id, DailyPrice.trade_date, DailyPrice.close, DailyPrice.volume,
                    DailyPrice.turnover)
             .where(DailyPrice.security_id.in_(security_ids), DailyPrice.trade_date <= self.as_of,
                    DailyPrice.trade_date > since, DailyPrice.available_at <= self.decision_at,
                    DailyPrice.close.is_not(None))
             .order_by(DailyPrice.security_id, DailyPrice.trade_date))
        df = pd.DataFrame(self._s.execute(q).all(), columns=["sid", "d", "close", "volume", "turnover"])
        if df.empty:
            return pd.DataFrame(columns=cols)
        df["close"] = df["close"].astype(float)
        df["factor"] = 1.0
        for sid, events in self._pit_factors(sorted(df.sid.unique().tolist()), since).items():
            m = df.sid == sid
            for ex, ratio in events:  # rows before the ex-date are scaled by ref/prev (public events only)
                df.loc[m & (df.d < ex), "factor"] *= ratio
        df["adj_close"] = df["close"] * df["factor"]
        df = df.groupby("sid", group_keys=False).tail(lookback_days)
        return df[cols].reset_index(drop=True)

    def indicators(self, security_ids: list[int], windows: tuple[int, ...] = (20, 60)) -> pd.DataFrame:
        """For `as_of`: adj_close, ma{n} of the point-in-time adjusted close, turnover_ma20 (raw TWD).
        Securities without a bar on `as_of` or with too little history get NaN."""
        out_cols = ["sid", "adj_close", *[f"ma{n}" for n in windows], "turnover_ma20"]
        h = self.history(security_ids, lookback_days=max(max(windows), 20))
        if h.empty:
            return pd.DataFrame(columns=out_cols)
        rows = []
        for sid, g in h.groupby("sid", sort=True):
            if g.d.iloc[-1] != self.as_of:
                continue
            r = {"sid": int(sid), "adj_close": g.adj_close.iloc[-1],
                 "turnover_ma20": float(g.turnover.tail(20).astype(float).mean()) if len(g) >= 20 else float("nan")}
            for n in windows:
                r[f"ma{n}"] = float(g.adj_close.tail(n).mean()) if len(g) >= n else float("nan")
            rows.append(r)
        return pd.DataFrame(rows, columns=out_cols)

    def index_close(self, index_code: str, lookback_days: int = 260) -> pd.Series:
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
                "fundamental data is disabled for technical backtests (fundamental_backtest_ready is not true)")
        from aqt.data.fundamentals import latest_statements_as_of
        return latest_statements_as_of(self._s, self.decision_at, security_ids)
