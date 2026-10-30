"""Taiwan stock-market trading rules (TWSE/TPEx, regular common stocks).

References: TWSE 營業細則 (tick sizes since 2005, ±10% daily limit since 2015-06-01),
fee schedule: commission cap 0.1425% (min NT$20 typical), securities transaction tax
0.3% on sells (0.15% for qualifying day trades; 0.1% for ETFs).
"""
from datetime import time
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

LOT_SIZE = 1000
PRICE_LIMIT_PCT = Decimal("0.10")
SESSION_OPEN = time(9, 0)
SESSION_CLOSE = time(13, 30)
CLOSING_AUCTION_START = time(13, 25)
PREOPEN_START = time(8, 30)

COMMISSION_RATE = Decimal("0.001425")
MIN_COMMISSION = Decimal("20")
TAX_RATE_STOCK = Decimal("0.003")
TAX_RATE_DAYTRADE = Decimal("0.0015")
TAX_RATE_ETF = Decimal("0.001")

_STOCK_TICKS = [
    (Decimal("10"), Decimal("0.01")),
    (Decimal("50"), Decimal("0.05")),
    (Decimal("100"), Decimal("0.1")),
    (Decimal("500"), Decimal("0.5")),
    (Decimal("1000"), Decimal("1")),
]
_STOCK_TICK_MAX = Decimal("5")


def tick_size(price: Decimal, is_etf: bool = False) -> Decimal:
    price = Decimal(price)
    if is_etf:
        return Decimal("0.01") if price < 50 else Decimal("0.05")
    for upper, tick in _STOCK_TICKS:
        if price < upper:
            return tick
    return _STOCK_TICK_MAX


def round_to_tick(price: Decimal, rounding=ROUND_HALF_UP, is_etf: bool = False) -> Decimal:
    price = Decimal(price)
    t = tick_size(price, is_etf)
    return ((price / t).to_integral_value(rounding=rounding) * t).quantize(t)


def price_limits(reference: Decimal, is_etf: bool = False) -> tuple[Decimal, Decimal]:
    """(limit_up, limit_down) for a ±10% stock, rounded inward to a valid tick."""
    reference = Decimal(reference)
    up = round_to_tick(reference * (1 + PRICE_LIMIT_PCT), ROUND_FLOOR, is_etf)
    down = round_to_tick(reference * (1 - PRICE_LIMIT_PCT), ROUND_CEILING, is_etf)
    return up, down


def is_valid_tick(price: Decimal, is_etf: bool = False) -> bool:
    price = Decimal(price)
    return price % tick_size(price, is_etf) == 0


def commission(amount: Decimal, discount: Decimal = Decimal("1")) -> Decimal:
    fee = (Decimal(amount) * COMMISSION_RATE * discount).to_integral_value(rounding=ROUND_FLOOR)
    return max(fee, MIN_COMMISSION)


def transaction_tax(amount: Decimal, is_etf: bool = False, day_trade: bool = False) -> Decimal:
    rate = TAX_RATE_ETF if is_etf else (TAX_RATE_DAYTRADE if day_trade else TAX_RATE_STOCK)
    return (Decimal(amount) * rate).to_integral_value(rounding=ROUND_FLOOR)
