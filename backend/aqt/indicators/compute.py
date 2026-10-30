"""Load adjusted price history, compute indicators, persist them; also adj_factor maintenance."""
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aqt.indicators.technical import CALC_VERSION, compute_all
from aqt.models import CorporateAction, DailyPrice, TechnicalIndicator
from aqt.models.base import utcnow

WARMUP_CALENDAR_DAYS = 200  # enough for MA60 + EMA warm-up


def recompute_adj_factors(session: Session, security_id: int) -> int:
    """Backward adjustment: rows before each ex-date are multiplied by ref_price / prev_close.

    Note: adjusted *levels* of old bars change when new corporate actions arrive, but returns and
    ratio-based indicators do not. Absolute-price rules (min price, tick, limits) always use raw prices.
    """
    acts = session.execute(select(CorporateAction.ex_date, CorporateAction.prev_close, CorporateAction.ref_price)
                           .where(CorporateAction.security_id == security_id)
                           .order_by(CorporateAction.ex_date.desc())).all()
    session.execute(update(DailyPrice).where(DailyPrice.security_id == security_id).values(adj_factor=1))
    factor = Decimal(1)
    for ex_date, prev_close, ref in acts:
        if not prev_close or not ref or prev_close <= 0:
            continue
        factor *= ref / prev_close
        session.execute(update(DailyPrice).where(DailyPrice.security_id == security_id,
                                                 DailyPrice.trade_date < ex_date)
                        .values(adj_factor=factor))
    return len(acts)


def load_history(session: Session, security_ids: list[int] | None, start: date, end: date) -> pd.DataFrame:
    q = (select(DailyPrice.security_id, DailyPrice.trade_date, DailyPrice.open, DailyPrice.high, DailyPrice.low,
                DailyPrice.close, DailyPrice.volume, DailyPrice.turnover, DailyPrice.adj_factor)
         .where(DailyPrice.trade_date.between(start, end), DailyPrice.close.is_not(None))
         .order_by(DailyPrice.security_id, DailyPrice.trade_date))
    if security_ids is not None:
        q = q.where(DailyPrice.security_id.in_(security_ids))
    df = pd.DataFrame(session.execute(q).all(),
                      columns=["sid", "d", "open", "high", "low", "close", "volume", "turnover", "adj"])
    if df.empty:
        return df
    adj = df["adj"].astype(float)
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].astype(float) * adj
    # keep share volume comparable across splits/stock dividends
    df["volume"] = df["volume"].astype(float) / adj
    return df


def compute_for_dates(session: Session, start: date, end: date, security_ids: list[int] | None = None) -> int:
    hist = load_history(session, security_ids, start - timedelta(days=WARMUP_CALENDAR_DAYS), end)
    if hist.empty:
        return 0
    rows = []
    now = utcnow()
    for sid, g in hist.groupby("sid", sort=False):
        g = g.reset_index(drop=True)
        ind = compute_all(g)
        ind["d"] = g["d"]
        ind = ind[(ind["d"] >= start) & (ind["d"] <= end)]
        for rec in ind.to_dict("records"):
            d = rec.pop("d")
            clean = {k: (None if v is None or (isinstance(v, float) and not np.isfinite(v)) else float(v))
                     for k, v in rec.items()}
            rows.append(dict(security_id=int(sid), trade_date=d, calc_version=CALC_VERSION, computed_at=now, **clean))
    for i in range(0, len(rows), 2000):
        chunk = rows[i:i + 2000]
        stmt = insert(TechnicalIndicator).values(chunk)
        stmt = stmt.on_conflict_do_update(index_elements=["security_id", "trade_date"],
                                          set_={c: stmt.excluded[c] for c in chunk[0]
                                                if c not in ("security_id", "trade_date")})
        session.execute(stmt)
    return len(rows)
