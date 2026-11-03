"""Event-driven daily backtest engine (technical data only).

Day loop for every observed trading day d in [start, end]:
  1. corporate actions effective on d (ex-date in (previous trading day, d]) adjust holdings so their
     value is preserved (total-return treatment: shares *= prev_close / reference price);
  2. pending orders (decided after the close of the previous decision day) execute at d's open:
     sells first, then buys within available cash — see `_fill` for every rejection rule;
  3. holdings of securities that stopped trading for good (delisted) are liquidated at their last close;
  4. the portfolio is marked to market at d's close;
  5. on decision days the strategy receives a point-in-time DataView for d and returns target weights;
     buys outside `build_universe(d).eligible` are refused and logged.

Deterministic: no randomness, all iteration sorted by security id. No database writes (see records.py).
"""
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aqt.data.tradability import blocked_securities
from aqt.models import CorporateAction, DailyPrice, Security, TechnicalIndicator
from aqt.research import DISCLAIMER, ENGINE_VERSION
from aqt.research.costs import CostModel, FillResult
from aqt.research.dataview import DEFAULT_DECISION_TIME, DataView, FundamentalsNotAllowed
from aqt.research.metrics import compute_metrics
from aqt.research.strategies import Strategy

log = logging.getLogger(__name__)
NO_TRADE_BLOCKS = {"stop_trading", "trading_halt"}  # cannot trade at all (sells are retried)
SELL_RETRY_DAYS = 20


@dataclass
class BacktestConfig:
    start: date
    end: date
    initial_cash: float = 10_000_000.0
    cost_model: CostModel = field(default_factory=CostModel)
    decision_time: time = DEFAULT_DECISION_TIME
    allow_fundamentals: bool = False
    benchmark_index: str = "TAIEX"

    def to_dict(self) -> dict:
        return {"start": str(self.start), "end": str(self.end), "initial_cash": self.initial_cash,
                "cost_model": self.cost_model.to_dict(), "decision_time": str(self.decision_time),
                "allow_fundamentals": self.allow_fundamentals, "benchmark_index": self.benchmark_index,
                "fill_rule": "next trading day open", "engine_version": ENGINE_VERSION}


@dataclass
class Order:
    sid: int
    side: str  # BUY | SELL
    shares: float
    decided_on: date
    reason: str = "rebalance"
    attempts: int = 0


@dataclass
class Position:
    shares: float = 0.0
    cost: Decimal = Decimal(0)  # cash paid incl. entry costs for the open shares
    entry_date: date | None = None
    entry_costs: Decimal = Decimal(0)
    bought_shares: float = 0.0
    bought_value: Decimal = Decimal(0)
    realized_gross: Decimal = Decimal(0)
    exit_costs: Decimal = Decimal(0)
    sold_shares: float = 0.0
    sold_value: Decimal = Decimal(0)


@dataclass
class BacktestResult:
    config: dict
    strategy: dict
    equity_curve: list  # [date, equity, cash, exposure]
    trades: list  # closed round trips
    fills: list
    rejections: list
    metrics: dict
    benchmark: dict
    disclaimer: str = DISCLAIMER

    def results_hash(self) -> str:
        blob = json.dumps({"equity": self.equity_curve, "trades": self.trades, "fills": self.fills,
                           "rejections": self.rejections}, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()


class Backtester:
    def __init__(self, session: Session, strategy: Strategy, config: BacktestConfig):
        if config.allow_fundamentals:
            ready = (get_state_safe(session) or {}).get("fundamental_backtest_ready")
            if not ready:
                raise FundamentalsNotAllowed("fundamental_backtest_ready is false: fundamentals cannot be enabled")
        self.s, self.strategy, self.cfg = session, strategy, config
        self.cm = config.cost_model
        self.cash = Decimal(str(config.initial_cash))
        self.pos: dict[int, Position] = {}
        self.pending: list[Order] = []
        self.equity_curve, self.trades, self.fills, self.rejections = [], [], [], []
        self.last_close: dict[int, Decimal] = {}
        self._types: dict[int, str] = {}

    # ------------------------------------------------------------------ data helpers (engine-only)
    def _days(self) -> list[date]:
        return list(self.s.scalars(select(DailyPrice.trade_date).distinct()
                                   .where(DailyPrice.trade_date.between(self.cfg.start, self.cfg.end))
                                   .order_by(DailyPrice.trade_date)))

    def _bars(self, sids, d: date) -> dict[int, DailyPrice]:
        if not sids:
            return {}
        return {p.security_id: p for p in self.s.scalars(
            select(DailyPrice).where(DailyPrice.security_id.in_(sorted(sids)), DailyPrice.trade_date == d))}

    def _avg_turnover(self, sid: int, d: date) -> float | None:
        v = self.s.scalar(select(TechnicalIndicator.turnover_ma20)
                          .where(TechnicalIndicator.security_id == sid, TechnicalIndicator.trade_date < d)
                          .order_by(TechnicalIndicator.trade_date.desc()).limit(1))
        return float(v) if v is not None else None

    def _stype(self, sid: int) -> str:
        if sid not in self._types:
            self._types[sid] = self.s.scalar(select(Security.security_type).where(Security.id == sid)) or "STOCK"
        return self._types[sid]

    # ------------------------------------------------------------------ portfolio mechanics
    def _apply_corporate_actions(self, d: date, prev: date | None) -> None:
        held = [sid for sid, p in self.pos.items() if p.shares > 0]
        if not held or prev is None:
            return
        rows = self.s.execute(select(CorporateAction.security_id, CorporateAction.ex_date, CorporateAction.action_type,
                                     CorporateAction.prev_close, CorporateAction.ref_price)
                              .where(CorporateAction.security_id.in_(held), CorporateAction.ex_date > prev,
                                     CorporateAction.ex_date <= d)
                              .order_by(CorporateAction.security_id, CorporateAction.ex_date)).all()
        for sid, ex, kind, pc, ref in rows:
            if not pc or not ref or ref <= 0:
                continue
            ratio = float(pc / ref)
            p = self.pos[sid]
            p.shares *= ratio
            self.fills.append({"date": str(d), "sid": sid, "type": "CORPORATE_ACTION", "action": kind,
                               "ex_date": str(ex), "share_ratio": round(ratio, 8)})

    def _fill(self, o: Order, d: date, bar: DailyPrice | None, blocked: dict[int, str]) -> FillResult:
        if bar is None or bar.open is None or bar.close is None or bar.volume <= 0:
            return FillResult(False, reason="no_trade_on_fill_day")
        reason = blocked.get(o.sid)
        if reason in NO_TRADE_BLOCKS:
            return FillResult(False, reason=f"restricted:{reason}")
        if o.side == "BUY" and reason:
            return FillResult(False, reason=f"restricted:{reason}")  # no new positions in restricted names
        is_etf = self._stype(o.sid) == "ETF"
        if o.side == "BUY" and bar.limit_up is not None and bar.open >= bar.limit_up:
            return FillResult(False, reason="limit_up_at_open")
        if o.side == "SELL" and bar.limit_down is not None and bar.open <= bar.limit_down:
            return FillResult(False, reason="limit_down_at_open")
        cap = self.cm.round_lots(bar.volume * self.cm.max_participation)  # whole lots of the day's volume
        if o.side == "BUY":
            want = self.cm.round_lots(o.shares)
            shares = min(want, cap)
        else:
            want = o.shares  # may hold an odd remainder created by stock dividends / reductions
            shares = min(want, cap) if cap > 0 else 0
            if shares > 0 and want - shares < self.cm.lot_size:
                shares = want  # the odd remainder goes with the last sale
        if shares <= 0:
            return FillResult(False, reason="volume_participation_cap", detail={"day_volume": bar.volume})
        price = self.cm.fill_price(o.side, bar.open, self._avg_turnover(o.sid, d), bar.limit_up, bar.limit_down,
                                   is_etf)
        amount = (price * Decimal(str(shares))).quantize(Decimal("1"))
        res = FillResult(True, shares=shares, price=price, commission=self.cm.commission(amount),
                         tax=self.cm.tax(amount, is_etf) if o.side == "SELL" else Decimal(0),
                         slippage_cost=abs(price - bar.open) * Decimal(str(shares)))
        if shares < want:
            res.detail["partial_of"] = want
        return res

    def _execute(self, d: date, prev: date | None) -> None:
        if not self.pending:
            return
        orders = sorted(self.pending, key=lambda o: (o.side != "SELL", o.sid))  # sells first
        bars = self._bars({o.sid for o in orders}, d)
        blocked = blocked_securities(self.s, d)
        keep: list[Order] = []
        for o in orders:
            if o.side == "BUY":
                # cash check before filling: shrink to what is affordable incl. commission
                bar = bars.get(o.sid)
                if bar is not None and bar.open:
                    est = bar.open * Decimal("1.01")
                    affordable = self.cm.round_lots(float(self.cash / (est * Decimal("1.002"))))
                    o.shares = min(o.shares, affordable)
                    if o.shares <= 0:
                        self._reject(o, d, "insufficient_cash")
                        continue
            r = self._fill(o, d, bars.get(o.sid), blocked)
            if not r.filled:
                if o.side == "SELL" and o.attempts < SELL_RETRY_DAYS:
                    o.attempts += 1
                    keep.append(o)  # exits are retried on the following days
                self._reject(o, d, r.reason, r.detail)
                continue
            self._book(o, d, r)
            if o.side == "SELL" and r.detail.get("partial_of"):
                keep.append(Order(o.sid, "SELL", r.detail["partial_of"] - r.shares, o.decided_on, o.reason,
                                  o.attempts + 1))
        self.pending = keep

    def _book(self, o: Order, d: date, r: FillResult) -> None:
        value = r.price * Decimal(str(r.shares))
        p = self.pos.setdefault(o.sid, Position())
        if o.side == "BUY":
            self.cash -= value + r.costs
            if p.shares <= 0:
                p.entry_date = d
            p.shares += r.shares
            p.cost += value + r.costs
            p.entry_costs += r.costs
            p.bought_shares += r.shares
            p.bought_value += value
        else:
            sell = min(r.shares, p.shares)
            frac = Decimal(str(sell / p.shares)) if p.shares else Decimal(1)
            cost_out = p.cost * frac
            self.cash += value - r.costs
            p.realized_gross += value - cost_out + p.entry_costs * frac  # gross: before any costs
            p.exit_costs += r.costs
            p.cost -= cost_out
            p.shares -= sell
            p.sold_shares += sell
            p.sold_value += value
            if p.shares <= 1e-9:
                self._close_trade(o.sid, p, d, o.reason)
        self.fills.append({"date": str(d), "sid": o.sid, "type": o.side, "shares": r.shares,
                           "price": str(r.price), "commission": str(r.commission), "tax": str(r.tax),
                           "slippage_cost": str(r.slippage_cost.quantize(Decimal("0.01"))),
                           "decided_on": str(o.decided_on), **({"partial_of": r.detail["partial_of"]}
                                                                if "partial_of" in r.detail else {})})

    def _close_trade(self, sid: int, p: Position, d: date, reason: str) -> None:
        costs = p.entry_costs + p.exit_costs
        gross = p.realized_gross
        self.trades.append({
            "sid": sid, "entry_date": str(p.entry_date), "exit_date": str(d),
            "quantity": round(p.bought_shares, 4),
            "entry_price": float(p.bought_value / Decimal(str(p.bought_shares))) if p.bought_shares else None,
            "exit_price": float(p.sold_value / Decimal(str(p.sold_shares))) if p.sold_shares else None,
            "gross_pnl": float(gross), "entry_costs": float(p.entry_costs), "exit_costs": float(p.exit_costs),
            "costs": float(costs), "net_pnl": float(gross - costs), "exit_reason": reason})
        del self.pos[sid]

    def _reject(self, o: Order, d: date, reason: str, detail: dict | None = None) -> None:
        self.rejections.append({"date": str(d), "sid": o.sid, "side": o.side, "shares": o.shares,
                                "reason": reason, **(detail or {})})

    def _liquidate_delisted(self, d: date, bars: dict) -> None:
        for sid in sorted(self.pos):
            p = self.pos[sid]
            if p.shares <= 0 or sid in bars:
                continue
            later = self.s.scalar(select(func.min(DailyPrice.trade_date))
                                  .where(DailyPrice.security_id == sid, DailyPrice.trade_date > d))
            st = self.s.execute(select(Security.status, Security.last_seen_date).where(Security.id == sid)).first()
            if later is None and st and st[0] == "DELISTED":
                px = self.last_close.get(sid, Decimal(0))
                value = px * Decimal(str(p.shares))
                costs = self.cm.commission(value) + self.cm.tax(value) if value > 0 else Decimal(0)
                self._book(Order(sid, "SELL", p.shares, d, "delisted"), d,
                           FillResult(True, shares=p.shares, price=px, commission=costs, tax=Decimal(0)))
                self.pending = [o for o in self.pending if o.sid != sid]

    def _mark(self, d: date) -> dict[int, DailyPrice]:
        held = {sid for sid, p in self.pos.items() if p.shares > 0}
        bars = self._bars(held, d)
        for sid, b in bars.items():
            if b.close is not None:
                self.last_close[sid] = b.close
        mv = sum((self.last_close.get(sid, Decimal(0)) * Decimal(str(p.shares)) for sid, p in self.pos.items()),
                 Decimal(0))
        eq = self.cash + mv
        self.equity_curve.append([str(d), float(eq), float(self.cash), float(mv / eq) if eq else 0.0])
        return bars

    def _rebalance(self, d: date, targets: dict[int, float], view: DataView) -> None:
        # the universe is only needed when something may be bought (building it is the costly query)
        eligible = set(view.universe().eligible) if any(w > 0 for w in targets.values()) else set()
        equity = Decimal(str(self.equity_curve[-1][1]))
        closes = {sid: b.close for sid, b in self._bars(set(targets) | set(self.pos), d).items()
                  if b.close is not None}
        self.pending = [o for o in self.pending if o.side == "SELL" and o.reason != "rebalance"]
        for sid in sorted(set(self.pos) | set(targets)):
            cur = self.pos[sid].shares if sid in self.pos else 0.0
            w = targets.get(sid, 0.0)
            if w > 0 and sid not in eligible and cur <= 0:
                self.rejections.append({"date": str(d), "sid": sid, "side": "BUY", "shares": None,
                                        "reason": "not_in_universe"})
                continue
            px = closes.get(sid) or self.last_close.get(sid)
            if not px:
                continue
            want = float(equity * Decimal(str(w)) / px)
            if want < cur:
                sell = cur - want if w > 0 else cur
                if w > 0:
                    sell = self.cm.round_lots(sell)
                if sell > 0:
                    self.pending.append(Order(sid, "SELL", sell, d))
            elif want > cur and sid in eligible:
                buy = self.cm.round_lots(want - cur)
                if buy > 0:
                    self.pending.append(Order(sid, "BUY", buy, d))

    # ------------------------------------------------------------------ run
    def run(self) -> BacktestResult:
        days = self._days()
        prev = None
        last_decision = None
        for d in days:
            self._apply_corporate_actions(d, prev)
            self._execute(d, prev)
            bars = self._mark(d)
            self._liquidate_delisted(d, bars)
            if self.strategy.is_decision_day(d, last_decision) and d != days[-1]:
                view = DataView(self.s, d, self.cfg.decision_time, self.cfg.allow_fundamentals)
                targets = self.strategy.target_weights(view, {k: v.shares for k, v in self.pos.items()})
                if targets is not None:
                    self._rebalance(d, targets, view)
                last_decision = d
            prev = d
        # positions still open at the end are valued at the last close net of selling costs (not traded)
        end = days[-1] if days else self.cfg.end
        for sid in sorted(self.pos):
            p = self.pos[sid]
            px = self.last_close.get(sid, Decimal(0))
            value = px * Decimal(str(p.shares))
            costs = self.cm.commission(value) + self.cm.tax(value, self._stype(sid) == "ETF") if value else Decimal(0)
            self._book(Order(sid, "SELL", p.shares, end, "end_of_test"), end,
                       FillResult(True, shares=p.shares, price=px, commission=costs, tax=Decimal(0)))
        if self.equity_curve:
            self.equity_curve[-1] = [str(end), float(self.cash), float(self.cash), 0.0]
        bench = self._benchmark(days)
        metrics = compute_metrics(self.equity_curve, self.trades, self.fills, self.cfg.initial_cash, bench)
        return BacktestResult(config=self.cfg.to_dict(),
                              strategy={"code": self.strategy.code, "version": self.strategy.version,
                                        "params": self.strategy.params, "params_hash": self.strategy.params_hash()},
                              equity_curve=self.equity_curve, trades=self.trades, fills=self.fills,
                              rejections=self.rejections, metrics=metrics, benchmark=bench)

    def _benchmark(self, days: list[date]) -> dict:
        from aqt.models import MarketIndex
        if not days:
            return {}
        rows = self.s.execute(select(MarketIndex.trade_date, MarketIndex.close)
                              .where(MarketIndex.index_code == self.cfg.benchmark_index,
                                     MarketIndex.trade_date.between(days[0], days[-1]))
                              .order_by(MarketIndex.trade_date)).all()
        return {"index": self.cfg.benchmark_index, "kind": "price index (dividends not included)",
                "curve": [[str(d), float(c)] for d, c in rows if c is not None]}


def get_state_safe(session: Session):
    from aqt.models import SystemState
    row = session.get(SystemState, "data_readiness")
    return row.value if row else None


def run_backtest(session: Session, strategy: Strategy, config: BacktestConfig) -> BacktestResult:
    return Backtester(session, strategy, config).run()
