"""Point-in-time tradable universe with explicit exclusion reasons (daily step 7)."""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from aqt.config import get_settings
from aqt.data.pit import DECISION_TIME, at_tpe
from aqt.data.tradability import blocked_securities
from aqt.models import DailyPrice, DataQualityIssue, Security

MIN_HISTORY_DAYS = 60
LOOKBACK = 20


@dataclass
class Universe:
    as_of: date
    eligible: list[int] = field(default_factory=list)
    excluded: dict[int, str] = field(default_factory=dict)

    def exclusion_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for reason in self.excluded.values():
            out[reason] = out.get(reason, 0) + 1
        return out


def build_universe(session: Session, as_of: date, min_turnover: float | None = None,
                   min_price: float | None = None, known_at: datetime | None = None) -> Universe:
    """Uses only rows with trade_date <= as_of known at `known_at` (default: as_of 18:00, the decision
    time). Includes later-delisted stocks."""
    s = get_settings()
    known_at = known_at or at_tpe(as_of, DECISION_TIME)
    min_turnover = s.min_avg_turnover_twd if min_turnover is None else min_turnover
    min_price = s.min_price_twd if min_price is None else min_price

    q = (select(DailyPrice.security_id, DailyPrice.trade_date, DailyPrice.close, DailyPrice.turnover,
                DailyPrice.volume, Security.security_type)
         .join(Security, Security.id == DailyPrice.security_id)
         .where(DailyPrice.trade_date <= as_of, DailyPrice.trade_date > as_of - timedelta(days=130),
                DailyPrice.available_at <= known_at))
    df = pd.DataFrame(session.execute(q).all(),
                      columns=["sid", "d", "close", "turnover", "volume", "stype"])
    u = Universe(as_of)
    if df.empty:
        return u
    bad = {sid for (sid,) in session.execute(
        select(DataQualityIssue.security_id).where(DataQualityIssue.trade_date == as_of,
                                                   DataQualityIssue.severity == "ERROR",
                                                   DataQualityIssue.effective_at <= known_at,
                                                   DataQualityIssue.security_id.is_not(None)))}
    restricted = blocked_securities(session, as_of, known_at)
    for sid, g in df.groupby("sid"):
        g = g.sort_values("d")
        last = g.iloc[-1]
        if last.stype != "STOCK":
            u.excluded[sid] = "not_common_stock"
        elif last.d != as_of or last.close is None or pd.isna(last.close) or last.volume == 0:
            u.excluded[sid] = "no_trade_today"
        elif sid in restricted:
            u.excluded[sid] = restricted[sid]
        elif sid in bad:
            u.excluded[sid] = "data_quality_error"
        elif len(g) < MIN_HISTORY_DAYS:
            u.excluded[sid] = "insufficient_history"
        elif float(last.close) < min_price:
            u.excluded[sid] = "price_below_min"
        elif g.tail(LOOKBACK).turnover.astype(float).mean() < min_turnover:
            u.excluded[sid] = "illiquid"
        else:
            u.eligible.append(int(sid))
    return u
