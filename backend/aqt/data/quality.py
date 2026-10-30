"""Data-quality checks. Each check returns issues; ERROR-level issues block trading (safe mode)."""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, aliased

from aqt.data.calendar import previous_observed_trading_date
from aqt.models import CorporateAction, DailyPrice, DataQualityIssue, InstitutionalTrade, Security

LIMIT_TOLERANCE = Decimal("0.005")  # rounding head-room above the ±10% rule
MIN_ROW_RATIO = 0.8  # today's row count vs previous trading day


@dataclass
class Issue:
    check_name: str
    severity: str
    dataset: str
    security_id: int | None = None
    trade_date: date | None = None
    detail: dict = field(default_factory=dict)


def check_prices(session: Session, d: date, market: str) -> list[Issue]:
    issues: list[Issue] = []
    rows = session.execute(
        select(DailyPrice, Security.code, Security.security_type)
        .join(Security, Security.id == DailyPrice.security_id)
        .where(DailyPrice.trade_date == d, Security.market == market)).all()
    if not rows:
        return [Issue("no_price_rows", "ERROR", "daily_prices", None, d, {"market": market})]

    ex_dates = {sid for (sid,) in session.execute(
        select(CorporateAction.security_id).where(CorporateAction.ex_date == d))}

    for p, code, stype in rows:
        o, h, l, c = p.open, p.high, p.low, p.close
        if c is None:
            if p.volume > 0:
                issues.append(Issue("volume_without_close", "ERROR", "daily_prices", p.security_id, d,
                                    {"code": code, "volume": p.volume}))
            continue
        if any(x is not None and x <= 0 for x in (o, h, l, c)):
            issues.append(Issue("non_positive_price", "ERROR", "daily_prices", p.security_id, d, {"code": code}))
            continue
        if None not in (o, h, l) and not (l <= min(o, c) and h >= max(o, c) and l <= h):
            issues.append(Issue("ohlc_inconsistent", "ERROR", "daily_prices", p.security_id, d,
                                {"code": code, "o": str(o), "h": str(h), "l": str(l), "c": str(c)}))
        if p.volume == 0:
            issues.append(Issue("zero_volume_with_price", "WARN", "daily_prices", p.security_id, d, {"code": code}))
        if p.prev_close and stype in ("STOCK", "ETF") and p.security_id not in ex_dates:
            move = abs(c - p.prev_close) / p.prev_close
            if move > Decimal("0.10") + LIMIT_TOLERANCE:
                issues.append(Issue("move_exceeds_limit", "WARN", "daily_prices", p.security_id, d,
                                    {"code": code, "prev_close": str(p.prev_close), "close": str(c),
                                     "move": f"{move:.4f}",
                                     "hint": "unrecorded corporate action, first trading days, or bad data"}))

    prev = previous_observed_trading_date(session, market, d)
    if prev is not None:
        prev_n = session.scalar(select(func.count()).select_from(DailyPrice)
                                .join(Security, Security.id == DailyPrice.security_id)
                                .where(DailyPrice.trade_date == prev, Security.market == market))
        if prev_n and len(rows) < prev_n * MIN_ROW_RATIO:
            issues.append(Issue("row_count_drop", "ERROR", "daily_prices", None, d,
                                {"market": market, "today": len(rows), "previous": prev_n, "previous_date": str(prev)}))
    return issues


def check_institutional(session: Session, d: date) -> list[Issue]:
    issues = []
    for r in session.scalars(select(InstitutionalTrade).where(InstitutionalTrade.trade_date == d)):
        parts = [r.foreign_net, r.foreign_dealer_net, r.trust_net, r.dealer_net]
        if r.total_net is None or any(x is None for x in parts):
            continue
        if abs(sum(parts) - r.total_net) > 1000:
            issues.append(Issue("institutional_sum_mismatch", "WARN", "institutional_trades", r.security_id, d,
                                {"sum_parts": sum(parts), "total": r.total_net}))
    return issues


def save_issues(session: Session, issues: list[Issue]) -> int:
    for i in issues:
        session.add(DataQualityIssue(check_name=i.check_name, severity=i.severity, dataset=i.dataset,
                                     security_id=i.security_id, trade_date=i.trade_date, detail=i.detail))
    return len(issues)


def summarize(issues: list[Issue]) -> dict:
    out: dict[str, dict[str, int]] = {}
    for i in issues:
        out.setdefault(i.severity, {}).setdefault(i.check_name, 0)
        out[i.severity][i.check_name] += 1
    return out


def fill_prev_close_and_limits(session: Session, d: date, market: str) -> int:
    """Set prev_close (adjusted by any ex-rights reference price) and ±10% limits for date d."""
    from aqt.data.tw_rules import price_limits

    prev = previous_observed_trading_date(session, market, d)
    if prev is None:
        return 0
    P = aliased(DailyPrice)
    rows = session.execute(
        select(DailyPrice, P.close, Security.security_type, CorporateAction.ref_price)
        .join(Security, Security.id == DailyPrice.security_id)
        .join(P, and_(P.security_id == DailyPrice.security_id, P.trade_date == prev), isouter=True)
        .join(CorporateAction, and_(CorporateAction.security_id == DailyPrice.security_id,
                                    CorporateAction.ex_date == d), isouter=True)
        .where(DailyPrice.trade_date == d, Security.market == market)).all()
    n = 0
    for p, prev_close, stype, ref in rows:
        base = ref if ref is not None else prev_close
        if base is None or base <= 0:
            continue
        p.prev_close = base
        if stype in ("STOCK", "ETF"):
            p.limit_up, p.limit_down = price_limits(base, is_etf=(stype == "ETF"))
        n += 1
    return n
