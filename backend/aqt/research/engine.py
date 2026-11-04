"""Event-driven daily backtest engine (technical data only).

Day loop for every observed trading day d in [start, end]:
  1. corporate actions: events of held securities whose ex-date is <= d, that were public by d's open
     (`available_at <= d 09:00`) and that the position was held through, adjust the holding under the
     explicit dividend mode (only TOTAL_RETURN_REINVEST: value-preserving share adjustment, i.e.
     frictionless reinvestment of distributions — reported as such, never compared with a price index);
  2. pending orders (decided after the close of an earlier decision day T) execute at d's open.
     Only information available by T's decision time sizes an order: the participation cap is a share of
     the *historical* average daily volume up to T; the fill day's own volume, high, low and close are
     never read. Restrictions and quality exclusions are those known at d 09:00, never later that day.
     Sells first, then buys within available cash;
  3. the portfolio is marked to market at d's close. A holding without a tradable bar is NOT exited:
     it stays frozen at its last close and is recorded as untradable; a run that ends with (or spends
     a long stretch with) such holdings is marked unusable for evaluation. No exit is ever faked from
     future knowledge (later absence of quotes, current Security.status, last-seen dates);
  4. on decision days the strategy receives a point-in-time DataView for d and returns target weights;
     buys outside `build_universe(d).eligible` are refused and logged.

Fail closed: a run starts only when `system_state.data_readiness` exists, technical_backtest_ready is
explicitly true, coverage is complete and the quality status is reviewed.

Deterministic: no randomness, iteration sorted by security id. No database writes (see records.py).
"""
import hashlib
import json
import logging
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from aqt.data.pit import MARKET_OPEN_TIME, TPE, at_tpe
from aqt.data.tradability import blocked_securities
from aqt.models import CorporateAction, DailyPrice, MarketIndex, Security
from aqt.research import DISCLAIMER, ENGINE_VERSION
from aqt.research.costs import CostModel, FillResult
from aqt.research.dataview import ALL_MARKETS, DEFAULT_DECISION_TIME, DataView, FundamentalsNotAllowed
from aqt.research.metrics import compute_metrics
from aqt.research.strategies import Strategy

log = logging.getLogger(__name__)
NO_TRADE_BLOCKS = {"stop_trading", "trading_halt"}  # cannot trade at all (sells are retried)
SELL_RETRY_DAYS = 20
ACCEPTED_QUALITY = {"CLEAN", "REVIEWED_WITH_EXCLUSIONS"}
UNTRADABLE_UNUSABLE_DAYS = 20  # a holding untradable this long makes the run unusable for evaluation

DIVIDEND_MODES = {"TOTAL_RETURN_REINVEST"}
# total-return benchmark matching the dividend mode, and the markets it covers. It is compared with a
# strategy only when the strategy trades inside that scope; the price index is never comparable.
BENCHMARKS = {"TOTAL_RETURN_REINVEST": ("TWSE:發行量加權股價報酬指數", "total_return", ("TWSE",))}
PRICE_INDEX_REFERENCE = "TAIEX"


@dataclass
class BacktestConfig:
    start: date
    end: date
    initial_cash: float = 10_000_000.0
    cost_model: CostModel = field(default_factory=CostModel)
    decision_time: time = DEFAULT_DECISION_TIME
    allow_fundamentals: bool = False
    dividend_mode: str = "TOTAL_RETURN_REINVEST"
    volume_lookback_days: int = 20  # participation cap basis: average daily volume up to the decision day
    markets: tuple[str, ...] = ALL_MARKETS  # universe scope; the benchmark comparison depends on it
    terminal: str = "MARK"  # MARK: open positions are valued at the last close net of selling costs
                            # (labelled, not an execution); CARRY: state handed to the next walk-forward window

    def __post_init__(self):
        if self.dividend_mode not in DIVIDEND_MODES:
            raise ValueError(f"unsupported dividend_mode {self.dividend_mode!r}; cash-dividend accrual needs "
                             "verifiable payment dates, which are not ingested")
        if self.terminal not in ("MARK", "CARRY"):
            raise ValueError("terminal must be MARK or CARRY")

    def benchmark_comparable(self) -> bool:
        return set(self.markets) <= set(BENCHMARKS[self.dividend_mode][2])

    def to_dict(self) -> dict:
        bench, kind, scope = BENCHMARKS[self.dividend_mode]
        return {"start": str(self.start), "end": str(self.end), "initial_cash": self.initial_cash,
                "cost_model": self.cost_model.to_dict(), "decision_time": str(self.decision_time),
                "allow_fundamentals": self.allow_fundamentals, "dividend_mode": self.dividend_mode,
                "benchmark_index": bench, "benchmark_kind": kind, "benchmark_scope": list(scope),
                "markets": list(self.markets), "benchmark_comparable": self.benchmark_comparable(),
                "volume_lookback_days": self.volume_lookback_days,
                "participation_basis": "average daily volume of the volume_lookback_days up to the decision day",
                "fill_rule": "next trading day open", "terminal": self.terminal, "engine_version": ENGINE_VERSION}


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
    untradable_since: date | None = None
    untradable_days: int = 0


@dataclass
class PortfolioState:
    """Everything a later walk-forward window inherits: actual cash, holdings, pending orders, marks,
    applied corporate actions, and the last real trading day (the rebalance calendar continues from it:
    a window boundary is not an event)."""
    cash: Decimal
    positions: dict = field(default_factory=dict)
    pending: list = field(default_factory=list)
    last_close: dict = field(default_factory=dict)
    applied_actions: set = field(default_factory=set)
    last_trading_day: date | None = None

    def equity(self) -> Decimal:
        return self.cash + sum((self.last_close.get(s, Decimal(0)) * Decimal(str(p.shares))
                                for s, p in self.positions.items()), Decimal(0))


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
    evaluation: dict = field(default_factory=dict)  # usability verdict + untradable holdings
    rebalance_days: list = field(default_factory=list)  # days the strategy actually decided
    final_state: PortfolioState | None = None
    disclaimer: str = DISCLAIMER

    def results_hash(self) -> str:
        blob = json.dumps({"equity": self.equity_curve, "trades": self.trades, "fills": self.fills,
                           "rejections": self.rejections, "evaluation": self.evaluation,
                           "rebalance_days": self.rebalance_days}, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()


class DataNotReady(RuntimeError):
    """Readiness missing, not explicitly true, or quality not reviewed: the engine refuses to run."""


def check_readiness(session: Session, allow_fundamentals: bool = False) -> dict:
    r = get_state_safe(session)
    if not r:
        raise DataNotReady("system_state.data_readiness is missing: run integrity-check first")
    problems = []
    if r.get("technical_backtest_ready") is not True:
        problems.append(f"technical_backtest_ready={r.get('technical_backtest_ready')!r}")
    if r.get("coverage_complete") is not True:
        problems.append(f"coverage_complete={r.get('coverage_complete')!r}")
    if r.get("quality_status") not in ACCEPTED_QUALITY:
        problems.append(f"quality_status={r.get('quality_status')!r}")
    if problems:
        raise DataNotReady("data not ready for technical backtests: " + ", ".join(problems))
    if allow_fundamentals and r.get("fundamental_backtest_ready") is not True:
        raise FundamentalsNotAllowed("fundamental_backtest_ready is not true: fundamentals cannot be enabled")
    return r


class Backtester:
    def __init__(self, session: Session, strategy: Strategy, config: BacktestConfig,
                 initial_state: PortfolioState | None = None):
        self.readiness = check_readiness(session, config.allow_fundamentals)
        self.s, self.strategy, self.cfg = session, strategy, config
        self.cm = config.cost_model
        st = deepcopy(initial_state) if initial_state else PortfolioState(Decimal(str(config.initial_cash)))
        self.cash, self.pos, self.pending = st.cash, st.positions, st.pending
        self.last_close, self.applied = st.last_close, st.applied_actions
        self.last_trading_day = st.last_trading_day
        self.rebalance_days: list[str] = []
        self.start_equity = float(st.equity())
        self.equity_curve, self.trades, self.fills, self.rejections = [], [], [], []
        self.untradable_log: list[dict] = []
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

    def _liquidity(self, sid: int, decided_on: date) -> tuple[float | None, float | None]:
        """(average volume, average turnover) over the lookback window ending at the decision day —
        all of it published before the decision time."""
        known_by = datetime.combine(decided_on, self.cfg.decision_time, tzinfo=TPE)
        rows = self.s.execute(select(DailyPrice.volume, DailyPrice.turnover)
                              .where(DailyPrice.security_id == sid, DailyPrice.trade_date <= decided_on,
                                     DailyPrice.available_at <= known_by)
                              .order_by(DailyPrice.trade_date.desc()).limit(self.cfg.volume_lookback_days)).all()
        if not rows:
            return None, None
        return sum(v for v, _ in rows) / len(rows), sum(t for _, t in rows) / len(rows)

    def _stype(self, sid: int) -> str:
        if sid not in self._types:
            self._types[sid] = self.s.scalar(select(Security.security_type).where(Security.id == sid)) or "STOCK"
        return self._types[sid]

    # ------------------------------------------------------------------ portfolio mechanics
    def _apply_corporate_actions(self, d: date) -> None:
        held = [sid for sid, p in self.pos.items() if p.shares > 0]
        if not held:
            return
        known_by = at_tpe(d, MARKET_OPEN_TIME)
        rows = self.s.execute(select(CorporateAction.id, CorporateAction.security_id, CorporateAction.ex_date,
                                     CorporateAction.action_type, CorporateAction.prev_close,
                                     CorporateAction.ref_price)
                              .where(CorporateAction.security_id.in_(held), CorporateAction.ex_date <= d,
                                     CorporateAction.available_at <= known_by)
                              .order_by(CorporateAction.security_id, CorporateAction.ex_date)).all()
        for aid, sid, ex, kind, pc, ref in rows:
            p = self.pos[sid]
            # only events the position was held through, applied once; an event published after its
            # ex-date is applied when it becomes public (the price drop shows as a loss until then)
            if aid in self.applied or p.entry_date is None or p.entry_date >= ex:
                continue
            self.applied.add(aid)
            if not pc or not ref or ref <= 0:
                continue
            ratio = float(pc / ref)
            p.shares *= ratio
            self.fills.append({"date": str(d), "sid": sid, "type": "CORPORATE_ACTION", "action": kind,
                               "ex_date": str(ex), "share_ratio": round(ratio, 8),
                               "dividend_mode": self.cfg.dividend_mode})

    def _fill(self, o: Order, d: date, bar: DailyPrice | None, blocked: dict[int, str]) -> FillResult:
        # only pre-open facts of the fill day: whether it opened, the open price and the price limits
        if bar is None or bar.open is None:
            return FillResult(False, reason="no_open_on_fill_day")
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
        avg_vol, avg_turnover = self._liquidity(o.sid, o.decided_on)
        cap = self.cm.round_lots((avg_vol or 0) * self.cm.max_participation)
        if o.side == "BUY":
            want = self.cm.round_lots(o.shares)
            shares = min(want, cap)
        else:
            want = o.shares  # may hold an odd remainder created by stock dividends / reductions
            shares = min(want, cap) if cap > 0 else 0
            if shares > 0 and want - shares < self.cm.lot_size:
                shares = want  # the odd remainder goes with the last sale
        if shares <= 0:
            return FillResult(False, reason="volume_participation_cap",
                              detail={"avg_volume_basis": avg_vol, "decided_on": str(o.decided_on)})
        price = self.cm.fill_price(o.side, bar.open, avg_turnover, bar.limit_up, bar.limit_down, is_etf)
        amount = (price * Decimal(str(shares))).quantize(Decimal("1"))
        res = FillResult(True, shares=shares, price=price, commission=self.cm.commission(amount),
                         tax=self.cm.tax(amount, is_etf) if o.side == "SELL" else Decimal(0),
                         slippage_cost=abs(price - bar.open) * Decimal(str(shares)))
        if shares < want:
            res.detail["partial_of"] = want
        return res

    def _execute(self, d: date) -> None:
        if not self.pending:
            return
        orders = sorted(self.pending, key=lambda o: (o.side != "SELL", o.sid))  # sells first
        bars = self._bars({o.sid for o in orders}, d)
        # only what was known before the open of the fill day, and only what is in force that day
        blocked = blocked_securities(self.s, d, at_tpe(d, MARKET_OPEN_TIME), include_upcoming=False)
        keep: list[Order] = []
        for o in orders:
            if o.side == "BUY":
                bar = bars.get(o.sid)
                if bar is not None and bar.open:  # affordability from the open price (known at the open)
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

    def _book(self, o: Order, d: date, r: FillResult, kind: str | None = None) -> None:
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
        self.fills.append({"date": str(d), "sid": o.sid, "type": kind or o.side, "shares": r.shares,
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

    def _mark(self, d: date) -> None:
        held = {sid for sid, p in self.pos.items() if p.shares > 0}
        bars = self._bars(held, d)
        for sid in sorted(held):
            b, p = bars.get(sid), self.pos[sid]
            if b is not None and b.close is not None:
                self.last_close[sid] = b.close
                if p.untradable_since is not None:
                    self.untradable_log.append({"sid": sid, "from": str(p.untradable_since), "to": str(d),
                                                "days": p.untradable_days, "resolved": True})
                p.untradable_since, p.untradable_days = None, 0
            else:  # frozen at the last close; never exited from hindsight
                p.untradable_since = p.untradable_since or d
                p.untradable_days += 1
        mv = sum((self.last_close.get(sid, Decimal(0)) * Decimal(str(p.shares)) for sid, p in self.pos.items()),
                 Decimal(0))
        eq = self.cash + mv
        self.equity_curve.append([str(d), float(eq), float(self.cash), float(mv / eq) if eq else 0.0])

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

    def _evaluation(self, end: date) -> dict:
        open_issues = [{"sid": sid, "from": str(p.untradable_since), "to": str(end), "days": p.untradable_days,
                        "resolved": False} for sid, p in sorted(self.pos.items()) if p.untradable_since]
        long_ones = [x for x in self.untradable_log + open_issues if x["days"] >= UNTRADABLE_UNUSABLE_DAYS]
        reasons = []
        if open_issues and self.cfg.terminal == "MARK":  # a CARRY window hands them to the next window
            reasons.append(f"{len(open_issues)} holding(s) untradable at the end (possible delisting; G9 "
                           "official delisting events not connected) — valued at a stale last close")
        if long_ones:
            reasons.append(f"{len(long_ones)} holding(s) untradable for >= {UNTRADABLE_UNUSABLE_DAYS} trading days")
        return {"usable_for_evaluation": not reasons, "unusable_reasons": reasons,
                "untradable_holdings": self.untradable_log + open_issues,
                "terminal": self.cfg.terminal}

    # ------------------------------------------------------------------ run
    def run(self) -> BacktestResult:
        days = self._days()
        prev = self.last_trading_day  # the real previous trading day, also across window boundaries
        for d in days:
            self._apply_corporate_actions(d)
            self._execute(d)
            self._mark(d)
            # the last day of the final window has no next open; a CARRY window fills in the next one
            can_fill_later = d != days[-1] or self.cfg.terminal == "CARRY"
            if self.strategy.is_decision_day(d, prev) and can_fill_later:
                view = DataView(self.s, d, self.cfg.decision_time, self.cfg.allow_fundamentals, self.cfg.markets)
                targets = self.strategy.target_weights(view, {k: v.shares for k, v in self.pos.items()})
                if targets is not None:
                    self._rebalance(d, targets, view)
                    self.rebalance_days.append(str(d))
            prev = d
        self.last_trading_day = prev
        end = days[-1] if days else self.cfg.end
        evaluation = self._evaluation(end)
        if self.cfg.terminal == "MARK":
            # valuation of what is still held: last close net of selling costs. Labelled END_MARK — it is
            # not an execution and is not subject to the fill rules.
            for sid in sorted(self.pos):
                p = self.pos[sid]
                px = self.last_close.get(sid, Decimal(0))
                value = px * Decimal(str(p.shares))
                is_etf = self._stype(sid) == "ETF"
                comm = self.cm.commission(value) if value else Decimal(0)
                tax = self.cm.tax(value, is_etf) if value else Decimal(0)
                self._book(Order(sid, "SELL", p.shares, end, "end_of_test_mark"), end,
                           FillResult(True, shares=p.shares, price=px, commission=comm, tax=tax), kind="END_MARK")
            self.pending = []
            if self.equity_curve:
                self.equity_curve[-1] = [str(end), float(self.cash), float(self.cash), 0.0]
        bench = self._benchmark(days)
        metrics = compute_metrics(self.equity_curve, self.trades, self.fills, self.start_equity, bench)
        metrics["dividend_mode"] = self.cfg.dividend_mode
        metrics["usable_for_evaluation"] = evaluation["usable_for_evaluation"]
        metrics["benchmark_comparable"] = self.cfg.benchmark_comparable()
        state = PortfolioState(self.cash, self.pos, self.pending, self.last_close, self.applied, self.last_trading_day)
        return BacktestResult(config={**self.cfg.to_dict(), "start_equity": self.start_equity,
                                      "data_readiness_at_run": self.readiness},
                              strategy={"code": self.strategy.code, "version": self.strategy.version,
                                        "params": self.strategy.params, "params_hash": self.strategy.params_hash()},
                              equity_curve=self.equity_curve, trades=self.trades, fills=self.fills,
                              rejections=self.rejections, metrics=metrics, benchmark=bench, evaluation=evaluation,
                              rebalance_days=self.rebalance_days, final_state=deepcopy(state))

    def _benchmark(self, days: list[date]) -> dict:
        if not days:
            return {}
        code, kind, scope = BENCHMARKS[self.cfg.dividend_mode]
        comparable = self.cfg.benchmark_comparable()

        def curve(c):
            rows = self.s.execute(select(MarketIndex.trade_date, MarketIndex.close)
                                  .where(MarketIndex.index_code == c, MarketIndex.trade_date.between(days[0], days[-1]))
                                  .order_by(MarketIndex.trade_date)).all()
            return [[str(d), float(v)] for d, v in rows if v is not None]
        price, index_curve = curve(PRICE_INDEX_REFERENCE), curve(code)
        # compute_metrics only sees `curve`: an index of another market scope yields no relative figures
        return {"index": code, "kind": kind, "scope": list(scope), "comparable": comparable,
                "not_comparable_reason": None if comparable else
                f"strategy markets {list(self.cfg.markets)} exceed the benchmark scope {list(scope)}",
                "dividend_mode": self.cfg.dividend_mode, "curve": index_curve if comparable else [],
                "reference_only_total_return": (None if comparable or len(index_curve) < 2 else
                                                index_curve[-1][1] / index_curve[0][1] - 1),
                "price_index_reference": {"index": PRICE_INDEX_REFERENCE, "kind": "price (dividends excluded)",
                                          "comparable": False,
                                          "total_return": price[-1][1] / price[0][1] - 1 if len(price) > 1 else None}}


def get_state_safe(session: Session):
    from aqt.models import SystemState
    row = session.get(SystemState, "data_readiness")
    return row.value if row else None


def run_backtest(session: Session, strategy: Strategy, config: BacktestConfig,
                 initial_state: PortfolioState | None = None) -> BacktestResult:
    return Backtester(session, strategy, config, initial_state).run()
