"""Taiwan execution-cost model: commission, securities transaction tax, liquidity-tiered slippage,
tick rounding, board lots, price limits and a volume-participation cap."""
from dataclasses import asdict, dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from aqt.data import tw_rules

# 20-day average traded value (TWD) -> one-way slippage in basis points
DEFAULT_SLIPPAGE_TIERS = ((1_000_000_000, 5), (200_000_000, 10), (50_000_000, 20), (10_000_000, 40), (0, 80))


@dataclass(frozen=True)
class CostModel:
    commission_discount: float = 1.0  # 1.0 = statutory max 0.1425%
    min_commission: int = 20
    lot_size: int = tw_rules.LOT_SIZE  # board lot; odd lots are not simulated
    max_participation: float = 0.05  # max share of the fill day's volume
    slippage_tiers: tuple = DEFAULT_SLIPPAGE_TIERS
    extra_slippage_bps: float = 0.0  # stress-test knob

    def to_dict(self) -> dict:
        d = asdict(self)
        d["slippage_tiers"] = [list(t) for t in self.slippage_tiers]
        return d

    def slippage_bps(self, avg_turnover_20d: float | None) -> float:
        v = avg_turnover_20d or 0.0
        for floor, bps in self.slippage_tiers:
            if v >= floor:
                return bps + self.extra_slippage_bps
        return self.slippage_tiers[-1][1] + self.extra_slippage_bps

    def fill_price(self, side: str, open_price: Decimal, avg_turnover_20d: float | None,
                   limit_up: Decimal | None, limit_down: Decimal | None, is_etf: bool = False) -> Decimal:
        """Open price moved against us by the slippage, rounded to a valid tick, never beyond the limits."""
        bps = Decimal(str(self.slippage_bps(avg_turnover_20d))) / Decimal(10000)
        if side == "BUY":
            p = tw_rules.round_to_tick(open_price * (1 + bps), ROUND_CEILING, is_etf)
            return min(p, limit_up) if limit_up else p
        p = tw_rules.round_to_tick(open_price * (1 - bps), ROUND_FLOOR, is_etf)
        return max(p, limit_down) if limit_down else p

    def commission(self, amount: Decimal) -> Decimal:
        fee = (amount * tw_rules.COMMISSION_RATE * Decimal(str(self.commission_discount))).to_integral_value(
            rounding=ROUND_FLOOR)
        return max(fee, Decimal(self.min_commission))

    def tax(self, amount: Decimal, is_etf: bool = False) -> Decimal:
        return tw_rules.transaction_tax(amount, is_etf=is_etf)

    def round_lots(self, shares: float) -> int:
        return int(shares // self.lot_size) * self.lot_size


@dataclass
class FillResult:
    filled: bool
    shares: int = 0
    price: Decimal | None = None
    commission: Decimal = Decimal(0)
    tax: Decimal = Decimal(0)
    slippage_cost: Decimal = Decimal(0)  # vs. the raw open price
    reason: str | None = None
    detail: dict = field(default_factory=dict)

    @property
    def costs(self) -> Decimal:
        return self.commission + self.tax
