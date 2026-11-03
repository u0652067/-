"""Strategy interface and the PHASE 2 example strategies.

A strategy decides after the close of a decision day using only the `DataView` it is given and returns
target portfolio weights (sid -> weight, sum <= 1). The engine turns targets into next-open orders,
refuses buys outside the point-in-time universe, and applies all execution rules.

The examples exist to exercise and validate the machinery. They are not recommendations and no claim is
made that they are effective or profitable.
"""
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date

from aqt.research.dataview import DataView


@dataclass
class Strategy:
    code: str = "base"
    version: str = "V0"
    params: dict = field(default_factory=dict)
    description: str = ""

    def params_hash(self) -> str:
        blob = json.dumps({"code": self.code, "version": self.version, "params": self.params}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    def is_decision_day(self, d: date, prev: date | None) -> bool:
        return True

    def target_weights(self, view: DataView, holdings: dict[int, float]) -> dict[int, float] | None:
        """None = keep the current portfolio unchanged."""
        raise NotImplementedError


def _first_of(period: str, d: date, prev: date | None) -> bool:
    if prev is None:
        return True
    if period == "month":
        return (d.year, d.month) != (prev.year, prev.month)
    if period == "week":
        return d.isocalendar()[:2] != prev.isocalendar()[:2]
    return True


@dataclass
class CashBaseline(Strategy):
    """Never invests: the zero-risk, zero-cost reference line."""
    code: str = "cash_baseline"
    version: str = "V1"
    description: str = "Holds cash; any non-zero result indicates an engine bug."

    def is_decision_day(self, d, prev):
        return prev is None

    def target_weights(self, view, holdings):
        return {}


@dataclass
class EqualWeightLiquid(Strategy):
    """Baseline: the N most liquid stocks of the point-in-time universe, equal weight, monthly rebalance."""
    code: str = "ew_liquid_baseline"
    version: str = "V1"
    params: dict = field(default_factory=lambda: {"top_n": 50, "rebalance": "month"})
    description: str = "Equal-weight baseline over the most liquid eligible stocks."

    def is_decision_day(self, d, prev):
        return _first_of(self.params["rebalance"], d, prev)

    def target_weights(self, view, holdings):
        eligible = view.universe().eligible
        ind = view.indicators(eligible, ("turnover_ma20",))
        if ind.empty:
            return {}
        top = ind.sort_values(["turnover_ma20", "sid"], ascending=[False, True]).head(self.params["top_n"])
        n = len(top)
        return {int(s): 1.0 / n for s in top["sid"]}


@dataclass
class MaTrendMomentum(Strategy):
    """Minimal technical example: stocks in an up-trend (close > MA20 > MA60), ranked by
    `lookback`-day return, top N equal weight, weekly rebalance."""
    code: str = "ma_trend_momentum"
    version: str = "V1"
    params: dict = field(default_factory=lambda: {"top_n": 20, "lookback": 20, "rebalance": "week"})
    description: str = "Trend filter + momentum rank; example for validating the backtest machinery."

    def is_decision_day(self, d, prev):
        return _first_of(self.params["rebalance"], d, prev)

    def target_weights(self, view, holdings):
        eligible = view.universe().eligible
        ind = view.indicators(eligible, ("ma20", "ma60")).dropna()
        if ind.empty:
            return {}
        trend = ind[(ind.adj_close_stored > ind.ma20) & (ind.ma20 > ind.ma60)]
        if trend.empty:
            return {}
        lb = int(self.params["lookback"])
        h = view.history([int(x) for x in trend.sid], lookback_days=lb + 1)
        ret = h.groupby("sid")["adj_close"].agg(lambda s: s.iloc[-1] / s.iloc[0] - 1 if len(s) > lb else None)
        ordered = sorted(ret.dropna().items(), key=lambda kv: (-kv[1], kv[0]))  # deterministic ties
        ranked = sorted(int(s) for s, _ in ordered[: self.params["top_n"]])
        return {int(s): 1.0 / len(ranked) for s in ranked} if ranked else {}


REGISTRY = {c().code: c for c in (CashBaseline, EqualWeightLiquid, MaTrendMomentum)}


def make(code: str, **params) -> Strategy:
    cls = REGISTRY[code]
    s = cls()
    s.params = {**s.params, **params}
    return s
