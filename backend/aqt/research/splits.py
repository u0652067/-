"""Time-ordered evaluation: chronological train / validation / test split and walk-forward testing.

Rules: windows never overlap, every test window starts after its training window ends, parameters are
chosen on the training window only, and each test window is evaluated exactly once with a fresh portfolio.
Nothing is shuffled.
"""
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from aqt.models import DailyPrice


@dataclass(frozen=True)
class Window:
    train_start: date
    train_end: date
    test_start: date
    test_end: date

    def to_dict(self) -> dict:
        return {k: str(v) for k, v in self.__dict__.items()}


def trading_days(session: Session, start: date, end: date) -> list[date]:
    return list(session.scalars(select(DailyPrice.trade_date).distinct()
                                .where(DailyPrice.trade_date.between(start, end)).order_by(DailyPrice.trade_date)))


def chronological_split(days: list[date], train: float = 0.6, validation: float = 0.2) -> dict[str, tuple[date, date]]:
    if not 0 < train < 1 or not 0 <= validation < 1 or train + validation >= 1:
        raise ValueError("fractions must leave a non-empty test period")
    n = len(days)
    a, b = int(n * train), int(n * (train + validation))
    if a < 2 or b - a < (1 if validation else 0) or n - b < 2:
        raise ValueError(f"not enough trading days ({n}) for the requested split")
    out = {"train": (days[0], days[a - 1]), "test": (days[b], days[-1])}
    if validation:
        out["validation"] = (days[a], days[b - 1])
    return out


def walk_forward_windows(days: list[date], train_days: int, test_days: int, step: int | None = None,
                         anchored: bool = False) -> list[Window]:
    """Rolling (or anchored/expanding) windows over trading days. step defaults to test_days, so the
    test windows tile the period without overlap."""
    step = step or test_days
    if step < test_days:
        raise ValueError("step < test_days would evaluate some days twice")
    out, i = [], 0
    while i + train_days + test_days <= len(days):
        tr0 = 0 if anchored else i
        out.append(Window(days[tr0], days[i + train_days - 1], days[i + train_days],
                          days[i + train_days + test_days - 1]))
        i += step
    return out
