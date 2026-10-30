"""Read-only data-center API (PHASE 1). Trading / settings endpoints arrive in PHASE 3/5."""
from datetime import date, datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from aqt.config import get_settings
from aqt.db import get_engine, session_scope
from aqt.models import (
    DailyPrice, DataQualityIssue, DataSourceStatus, InstitutionalTrade, JobRun, MarketIndex,
    MonthlyRevenue, Notification, Security, SystemState, TechnicalIndicator,
)

app = FastAPI(title="AI Quant Trader — Taiwan (SIMULATION ONLY)", version="0.1.0")


def db():
    with session_scope(get_engine()) as s:
        yield s


def _sec(s: Session, code: str, market: str | None) -> Security:
    q = select(Security).where(Security.code == code)
    if market:
        q = q.where(Security.market == market.upper())
    secs = s.scalars(q).all()
    if not secs:
        raise HTTPException(404, f"unknown security {code}")
    if len(secs) > 1:
        raise HTTPException(409, f"{code} exists in several markets; pass ?market=")
    return secs[0]


@app.get("/api/health")
def health(s: Session = Depends(db)):
    s.execute(text("select 1"))
    safe = s.get(SystemState, "safe_mode")
    last = s.execute(select(JobRun.job_name, JobRun.status, JobRun.finished_at)
                     .where(JobRun.job_name == "daily_update").order_by(JobRun.started_at.desc()).limit(1)).first()
    latest = s.scalar(select(func.max(DailyPrice.trade_date)))
    sources = s.scalars(select(DataSourceStatus)).all()
    stale_hours = None
    if latest:
        stale_hours = (datetime.now(timezone.utc) - datetime.combine(latest, datetime.min.time(), timezone.utc)
                       ).total_seconds() / 3600
    return {
        "status": "ok", "trading_mode": get_settings().trading_mode,
        "safe_mode": safe.value if safe else {"on": False},
        "latest_price_date": latest, "hours_since_latest_price_date": stale_hours,
        "last_daily_update": dict(last._mapping) if last else None,
        "sources_down": [f"{x.source}/{x.dataset}" for x in sources if x.status == "DOWN"],
        "realtime_quotes": {"available": False,
                            "note": "未接入合法授權即時行情；盤後資料為交易所公開資料，盤中僅有延遲快照"},
    }


@app.get("/api/data/sources")
def sources(s: Session = Depends(db)):
    return [dict(source=x.source, dataset=x.dataset, status=x.status, last_success_at=x.last_success_at,
                 last_failure_at=x.last_failure_at, last_error=x.last_error,
                 consecutive_failures=x.consecutive_failures, latest_data_date=x.latest_data_date)
            for x in s.scalars(select(DataSourceStatus).order_by(DataSourceStatus.source, DataSourceStatus.dataset))]


@app.get("/api/data/coverage")
def coverage(s: Session = Depends(db)):
    def span(model, col):
        lo, hi, n = s.execute(select(func.min(col), func.max(col), func.count()).select_from(model)).one()
        return {"from": lo, "to": hi, "rows": n}
    return {
        "securities": s.scalar(select(func.count()).select_from(Security)),
        "securities_delisted": s.scalar(select(func.count()).select_from(Security).where(Security.status == "DELISTED")),
        "daily_prices": span(DailyPrice, DailyPrice.trade_date),
        "technical_indicators": span(TechnicalIndicator, TechnicalIndicator.trade_date),
        "institutional_trades": span(InstitutionalTrade, InstitutionalTrade.trade_date),
        "monthly_revenues": span(MonthlyRevenue, MonthlyRevenue.period),
        "market_indices": span(MarketIndex, MarketIndex.trade_date),
    }


@app.get("/api/data/quality")
def quality_issues(trade_date: date | None = None, severity: str | None = None, limit: int = 200,
                   s: Session = Depends(db)):
    q = select(DataQualityIssue).order_by(DataQualityIssue.id.desc()).limit(min(limit, 1000))
    if trade_date:
        q = q.where(DataQualityIssue.trade_date == trade_date)
    if severity:
        q = q.where(DataQualityIssue.severity == severity.upper())
    return [dict(id=i.id, check=i.check_name, severity=i.severity, dataset=i.dataset, security_id=i.security_id,
                 trade_date=i.trade_date, detail=i.detail, created_at=i.created_at) for i in s.scalars(q)]


@app.get("/api/jobs")
def jobs(limit: int = 50, s: Session = Depends(db)):
    q = select(JobRun).order_by(JobRun.started_at.desc()).limit(min(limit, 500))
    return [dict(id=j.id, job=j.job_name, status=j.status, started_at=j.started_at, finished_at=j.finished_at,
                 params=j.params, result=j.result, error=(j.error or "")[-500:] or None) for j in s.scalars(q)]


@app.get("/api/notifications")
def notifications(limit: int = 50, s: Session = Depends(db)):
    q = select(Notification).order_by(Notification.id.desc()).limit(min(limit, 500))
    return [dict(id=n.id, level=n.level, title=n.title, body=n.body, created_at=n.created_at) for n in s.scalars(q)]


@app.get("/api/stocks")
def stocks(q: str | None = None, market: str | None = None, status: str | None = None,
           limit: int = Query(100, le=5000), s: Session = Depends(db)):
    stmt = select(Security).order_by(Security.market, Security.code).limit(limit)
    if q:
        stmt = stmt.where((Security.code.like(f"{q}%")) | (Security.name.like(f"%{q}%")))
    if market:
        stmt = stmt.where(Security.market == market.upper())
    if status:
        stmt = stmt.where(Security.status == status.upper())
    return [dict(id=x.id, code=x.code, name=x.name, market=x.market, type=x.security_type, industry=x.industry,
                 status=x.status, listed_date=x.listed_date, delisted_date=x.delisted_date,
                 first_seen_date=x.first_seen_date, last_seen_date=x.last_seen_date) for x in s.scalars(stmt)]


@app.get("/api/stocks/{code}/prices")
def prices(code: str, market: str | None = None, start: date | None = None, end: date | None = None,
           adjusted: bool = False, s: Session = Depends(db)):
    sec = _sec(s, code, market)
    end = end or date.today()
    start = start or end - timedelta(days=365)
    rows = s.scalars(select(DailyPrice).where(DailyPrice.security_id == sec.id,
                                              DailyPrice.trade_date.between(start, end))
                     .order_by(DailyPrice.trade_date)).all()

    def px(v, f):
        return None if v is None else float(v) * (float(f) if adjusted else 1.0)
    return {"code": sec.code, "name": sec.name, "market": sec.market, "adjusted": adjusted,
            "data": [dict(date=r.trade_date, open=px(r.open, r.adj_factor), high=px(r.high, r.adj_factor),
                          low=px(r.low, r.adj_factor), close=px(r.close, r.adj_factor), volume=r.volume,
                          turnover=r.turnover, prev_close=r.prev_close, limit_up=r.limit_up, limit_down=r.limit_down,
                          source=r.source, available_at=r.available_at) for r in rows]}


@app.get("/api/stocks/{code}/indicators")
def indicators(code: str, market: str | None = None, start: date | None = None, end: date | None = None,
               s: Session = Depends(db)):
    sec = _sec(s, code, market)
    end = end or date.today()
    start = start or end - timedelta(days=180)
    rows = s.scalars(select(TechnicalIndicator).where(TechnicalIndicator.security_id == sec.id,
                                                      TechnicalIndicator.trade_date.between(start, end))
                     .order_by(TechnicalIndicator.trade_date)).all()
    cols = [c.name for c in TechnicalIndicator.__table__.columns if c.name not in ("security_id",)]
    return {"code": sec.code, "market": sec.market, "data": [{c: getattr(r, c) for c in cols} for r in rows]}


@app.get("/api/stocks/{code}/institutional")
def institutional(code: str, market: str | None = None, days: int = 60, s: Session = Depends(db)):
    sec = _sec(s, code, market)
    rows = s.scalars(select(InstitutionalTrade).where(InstitutionalTrade.security_id == sec.id)
                     .order_by(InstitutionalTrade.trade_date.desc()).limit(days)).all()
    return {"code": sec.code, "data": [dict(date=r.trade_date, foreign_net=r.foreign_net, trust_net=r.trust_net,
                                            dealer_net=r.dealer_net, total_net=r.total_net) for r in rows]}
