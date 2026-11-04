"""Point-in-time market regimes, used only to slice results.

The label of day d is computed from index closes up to and including d, so a return earned on day d+1
is attributed to the regime known before that day:
  * trend: TAIEX close vs its 200-day average (BULL / BEAR);
  * volatility: 20-day realised volatility vs the median of its own past values (LOW_VOL / HIGH_VOL).
Days without enough history are UNKNOWN. Nothing here feeds a strategy decision.
"""
import math
from datetime import date

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from aqt.models import MarketIndex
from aqt.research.metrics import TRADING_DAYS, max_drawdown, sharpe

REGIME_INDEX = "TAIEX"
TREND_WINDOW, VOL_WINDOW, MIN_VOL_HISTORY = 200, 20, 60


def regime_labels(closes: pd.Series) -> dict[date, str]:
    """closes indexed by date, ascending. Each label only looks backwards (expanding median, rolling means)."""
    ret = closes.pct_change()
    ma = closes.rolling(TREND_WINDOW).mean()
    vol = ret.rolling(VOL_WINDOW).std()
    # strictly past values of the volatility series: today's level is compared with history
    median = vol.shift(1).expanding(MIN_VOL_HISTORY).median()
    out = {}
    for d in closes.index:
        if pd.isna(ma[d]) or pd.isna(vol[d]) or pd.isna(median[d]):
            out[d] = "UNKNOWN"
        else:
            out[d] = f"{'BULL' if closes[d] >= ma[d] else 'BEAR'}_{'HIGH' if vol[d] > median[d] else 'LOW'}_VOL"
    return out


def load_regimes(session: Session, end: date) -> dict[date, str]:
    rows = session.execute(select(MarketIndex.trade_date, MarketIndex.close)
                           .where(MarketIndex.index_code == REGIME_INDEX, MarketIndex.trade_date <= end,
                                  MarketIndex.close.is_not(None))
                           .order_by(MarketIndex.trade_date)).all()
    if not rows:
        return {}
    return regime_labels(pd.Series([float(c) for _, c in rows], index=[d for d, _ in rows]))


def regime_metrics(equity_curve: list, regimes: dict[date, str]) -> dict:
    """Daily returns grouped by the regime known at the previous close."""
    if len(equity_curve) < 2 or not regimes:
        return {}
    known = sorted(regimes)
    groups: dict[str, list[float]] = {}
    for prev, cur in zip(equity_curve, equity_curve[1:]):
        if prev[1] <= 0:
            continue
        label = regimes.get(date.fromisoformat(prev[0]), "UNKNOWN")
        groups.setdefault(label, []).append(cur[1] / prev[1] - 1)
    out = {}
    for label, rets in sorted(groups.items()):
        curve, v = [1.0], 1.0
        for r in rets:
            v *= 1 + r
            curve.append(v)
        out[label] = {"days": len(rets), "compounded_return": v - 1,
                      "mean_daily_return": sum(rets) / len(rets),
                      "annualised_return_simple": sum(rets) / len(rets) * TRADING_DAYS,
                      "sharpe": sharpe(rets), "worst_drawdown_within_regime_days": max_drawdown(curve)[0]}
        if not math.isfinite(out[label]["compounded_return"]):
            out[label]["compounded_return"] = None
    out["_note"] = {"index": REGIME_INDEX, "kept_known": len(known),
                    "definition": "label of the previous close; regime days are not contiguous"}
    return out
