"""Performance statistics. Costs are always included: every figure is net of commission, tax and
slippage unless the name says `gross`. Win rate counts every closed trade (losers are never dropped)."""
import math
from datetime import date

TRADING_DAYS = 252


def max_drawdown(values: list[float]) -> tuple[float, int | None, int | None]:
    """(drawdown <= 0, index of the peak, index of the trough)."""
    peak, mdd, peak_i, start, end = -math.inf, 0.0, 0, None, None
    for i, v in enumerate(values):
        if v > peak:
            peak, peak_i = v, i
        dd = v / peak - 1 if peak > 0 else 0.0
        if dd < mdd:
            mdd, start, end = dd, peak_i, i
    return mdd, start, end


def _returns(values: list[float]) -> list[float]:
    return [values[i] / values[i - 1] - 1 for i in range(1, len(values)) if values[i - 1] > 0]


def sharpe(rets: list[float], rf_annual: float = 0.0) -> float | None:
    if len(rets) < 2:
        return None
    rf = rf_annual / TRADING_DAYS
    ex = [r - rf for r in rets]
    mu = sum(ex) / len(ex)
    sd = math.sqrt(sum((r - mu) ** 2 for r in ex) / (len(ex) - 1))
    return mu / sd * math.sqrt(TRADING_DAYS) if sd > 0 else None


def compute_metrics(equity_curve: list, trades: list, fills: list, initial_cash: float, benchmark: dict | None = None
                    ) -> dict:
    dates = [r[0] for r in equity_curve]
    values = [r[1] for r in equity_curve]
    if not values:
        return {"days": 0}
    rets = _returns(values)
    total = values[-1] / initial_cash - 1
    years = max((date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days / 365.25, 1 / 365.25)
    cagr = (values[-1] / initial_cash) ** (1 / years) - 1 if values[-1] > 0 else -1.0
    mdd, s, e = max_drawdown([initial_cash] + values)
    vol = (math.sqrt(sum((r - sum(rets) / len(rets)) ** 2 for r in rets) / (len(rets) - 1)) * math.sqrt(TRADING_DAYS)
           if len(rets) > 1 else None)
    wins = [t["net_pnl"] for t in trades if t["net_pnl"] > 0]
    losses = [t["net_pnl"] for t in trades if t["net_pnl"] <= 0]
    n = len(trades)
    commission = sum(float(f.get("commission", 0)) for f in fills if f.get("type") in ("BUY", "SELL"))
    tax = sum(float(f.get("tax", 0)) for f in fills if f.get("type") in ("BUY", "SELL"))
    slip = sum(float(f.get("slippage_cost", 0)) for f in fills if f.get("type") in ("BUY", "SELL"))
    traded = sum(float(f["price"]) * float(f["shares"]) for f in fills if f.get("type") in ("BUY", "SELL"))
    gross_pnl = sum(t["gross_pnl"] for t in trades)
    out = {
        "days": len(values), "start": dates[0], "end": dates[-1],
        "final_equity": round(values[-1], 2), "total_return": total, "cagr": cagr,
        "annual_volatility": vol, "sharpe": sharpe(rets), "max_drawdown": mdd,
        # index 0 of the drawdown series is the initial capital before the first day
        "max_drawdown_period": [dates[s - 1] if s else "initial", dates[e - 1] if e else None],
        "trades": n, "win_rate": len(wins) / n if n else None,
        "avg_win": sum(wins) / len(wins) if wins else None, "avg_loss": sum(losses) / len(losses) if losses else None,
        "expectancy_per_trade": sum(t["net_pnl"] for t in trades) / n if n else None,
        "profit_factor": (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else None,
        "gross_pnl_closed_trades": gross_pnl, "net_pnl_closed_trades": sum(t["net_pnl"] for t in trades),
        "costs": {"commission": commission, "tax": tax, "slippage_vs_open": slip,
                  "total_explicit": commission + tax, "total_incl_slippage": commission + tax + slip},
        "turnover": traded / initial_cash / years if years else None,
        "avg_exposure": sum(r[3] for r in equity_curve) / len(equity_curve),
        "net_of_costs": True,
    }
    if benchmark and benchmark.get("curve"):
        b = [c for _, c in benchmark["curve"]]
        out["benchmark_total_return"] = b[-1] / b[0] - 1
        out["excess_return_vs_benchmark"] = total - out["benchmark_total_return"]
        out["benchmark_max_drawdown"] = max_drawdown(b)[0]
    return out
