"""Interruptible, resumable full historical backfill with persisted progress (`backfill-full`).

Progress lives in `system_state['backfill_full']` (PostgreSQL volume), written after every day, so
stopping the container at any point loses at most the day in flight. On restart:
  * months marked DONE are skipped;
  * inside other months each day runs with `only_missing=True` (datasets already stored are skipped);
  * corporate-action chunks and revenue months already loaded are skipped.

When a source answers with its security-notice page the run pauses (cooldown) and retries that day
instead of racing through the calendar recording failures. Days that still miss data are listed
per month (status PARTIAL) and re-tried by the next run.

Phases: corporate_actions -> daily -> revenue -> adjust (adj factors + indicators, per quarter).
"""
import logging
import time
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from aqt.config import get_settings
from aqt.data import pipeline
from aqt.data.http import Fetcher
from aqt.db import session_scope
from aqt.indicators.compute import compute_for_dates, recompute_adj_factors
from aqt.jobs.runtime import get_state, log_event, set_state
from aqt.models import CorporateAction, DailyPrice, MonthlyRevenue, Security

log = logging.getLogger(__name__)
STATE_KEY = "backfill_full"
_BLOCK_MARKERS = ("security notice", "proxy refused", "HTTP 403", "HTTP 407")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _months(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _month_days(y: int, m: int, start: date, end: date):
    d = max(date(y, m, 1), start)
    while d <= end and d.month == m:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def latest_published_revenue_month(today: date) -> tuple[int, int]:
    """Monthly revenue is due by the 10th of the next month; before the 11th use the month before."""
    first = today.replace(day=1)
    back = 2 if today.day <= 10 else 1
    y, m = first.year, first.month - back
    while m <= 0:
        y, m = y - 1, m + 12
    return y, m


def _blocked(result: dict) -> bool:
    return any(any(k in w for k in _BLOCK_MARKERS) for w in result.get("warnings", []))


def _revenue_stored(engine: Engine, market: str, y: int, m: int) -> int:
    with session_scope(engine) as s:
        return s.scalar(select(func.count()).select_from(MonthlyRevenue)
                        .join(Security, Security.id == MonthlyRevenue.security_id)
                        .where(Security.market == market, MonthlyRevenue.period == date(y, m, 1))) or 0


class FullBackfill:
    def __init__(self, engine: Engine, start: date, end: date, fetcher: Fetcher | None = None,
                 sleep=time.sleep, revenue: bool = True, rerun_done: bool = False, state_key: str = STATE_KEY):
        self.engine, self.start, self.end = engine, start, end
        self.state_key = state_key
        self.settings = get_settings()
        self.fetcher = fetcher
        self.sleep = sleep
        self.revenue = revenue
        prev = get_state(engine, state_key) or {}
        # progress is kept when only the end moves (the service defaults to "yesterday")
        self.state = prev if prev.get("start") == str(start) else {}
        self.state.update(start=str(start), end=str(end))
        self.state.setdefault("created_at", _now())
        for k in ("months", "corporate_actions", "revenue"):
            self.state.setdefault(k, {})
        if rerun_done:
            for v in self.state["months"].values():
                v["status"] = "PENDING"
        self.state["days_total"] = sum(1 for y, m in _months(start, end) for _ in _month_days(y, m, start, end))

    # -------------------------------------------------------------- bookkeeping
    def save(self, **kw) -> None:
        self.state.update(kw, updated_at=_now())
        set_state(self.engine, self.state_key, self.state)

    def _eta(self, done_this_run: int, t0: float) -> str | None:
        left = self.state["days_total"] - self.state.get("days_done", 0)
        if done_this_run == 0 or left <= 0:
            return None
        per_day = (time.monotonic() - t0) / done_this_run
        return (datetime.now(timezone.utc) + timedelta(seconds=per_day * left)).isoformat(timespec="minutes")

    def _cooldown(self, why: str, attempt: int) -> None:
        wait = self.settings.backfill_block_cooldown_seconds * attempt
        self.state["blocked_events"] = self.state.get("blocked_events", 0) + 1
        self.save(phase_note=f"source blocked ({why}); cooling down {int(wait)}s, attempt {attempt}")
        log_event(self.engine, "WARN", "backfill_full", f"source blocked: {why}; sleeping {int(wait)}s")
        self.sleep(wait)

    # -------------------------------------------------------------- phases
    def corporate_actions(self) -> None:
        self.save(phase="corporate_actions", status="RUNNING")
        s0 = self.start
        while s0 <= self.end:
            e0 = min(self.end, s0 + timedelta(days=90))
            key = f"{s0}..{e0}"
            if self.state["corporate_actions"].get(key) != "DONE":
                for attempt in range(1, self.settings.backfill_block_max_retries + 2):
                    try:
                        n = pipeline.ingest_corporate_actions(self.engine, self.fetcher, s0, e0)
                        self.state["corporate_actions"][key] = "DONE"
                        log.info("corporate actions %s: %d rows", key, n)
                        break
                    except Exception as e:
                        self.state["corporate_actions"][key] = f"FAILED: {e}"[:300]
                        if any(k in str(e) for k in _BLOCK_MARKERS) and \
                                attempt <= self.settings.backfill_block_max_retries:
                            self._cooldown(str(e)[:120], attempt)
                            continue
                        break
                self.save()
            s0 = e0 + timedelta(days=1)

    def daily(self) -> None:
        self.save(phase="daily", status="RUNNING")
        t0, done_now = time.monotonic(), 0
        for y, m in _months(self.start, self.end):
            key = f"{y}-{m:02d}"
            mon = self.state["months"].get(key, {})
            last_day = max((d for d in _month_days(y, m, self.start, self.end)), default=None)
            if last_day is None or (mon.get("status") == "DONE" and mon.get("through", "") >= str(last_day)):
                continue
            # a month restarted after an interruption / with gaps is processed again from its first day
            self.state["days_done"] = max(0, self.state.get("days_done", 0) - mon.get("days_processed", 0))
            mon = {"status": "RUNNING", "trading_days": 0, "days_processed": 0, "missing": {}, "failed": [],
                   "started_at": _now()}
            self.state["months"][key] = mon
            for d in _month_days(y, m, self.start, self.end):
                r: dict = {}
                for attempt in range(1, self.settings.backfill_block_max_retries + 2):
                    try:
                        r = pipeline.run_daily_update(self.engine, d, self.fetcher, compute_indicators=False,
                                                      manage_safe_mode=False, corporate_actions=False,
                                                      only_missing=True)
                    except Exception as e:
                        r = {"error": str(e)[:300]}
                    if (_blocked(r) or any(k in r.get("error", "") for k in _BLOCK_MARKERS)) and \
                            attempt <= self.settings.backfill_block_max_retries:
                        self._cooldown(f"{d}", attempt)
                        continue
                    break
                if "error" in r:
                    mon["failed"].append(f"{d}: {r['error']}")
                elif not r.get("skipped"):
                    mon["trading_days"] += 1
                for ds in r.get("missing", []):
                    mon["missing"].setdefault(ds, []).append(str(d))
                done_now += 1
                mon["days_processed"] += 1
                self.state["adjust_dirty"] = True
                self.state["days_done"] = self.state.get("days_done", 0) + 1
                self.save(current_day=str(d), eta=self._eta(done_now, t0), phase_note=None)
            mon["status"] = "PARTIAL" if (mon["missing"] or mon["failed"]) else "DONE"
            mon["through"], mon["finished_at"] = str(last_day), _now()
            self.save()
            log.info("month %s %s: %d trading days, missing=%s", key, mon["status"], mon["trading_days"],
                     {k: len(v) for k, v in mon["missing"].items()})

    def revenue_history(self) -> None:
        self.save(phase="revenue", status="RUNNING")
        ly, lm = latest_published_revenue_month(min(self.end + timedelta(days=40), date.today()))
        last = min(date(ly, lm, 1), self.end.replace(day=1))
        for y, m in _months(self.start, last):
            for market in pipeline.MARKETS:
                key = f"{market}:{y}-{m:02d}"
                if self.state["revenue"].get(key, "").startswith("DONE"):
                    continue
                have = _revenue_stored(self.engine, market, y, m)
                if have >= 100:  # a full month is ~750-1000 companies per market
                    self.state["revenue"][key] = f"DONE ({have} stored)"
                    continue
                for attempt in range(1, self.settings.backfill_block_max_retries + 2):
                    try:
                        n = pipeline.backfill_revenue(self.engine, market, y, m, self.fetcher)
                        self.state["revenue"][key] = f"DONE ({n})" if n >= 100 else f"FEW ({n})"
                        break
                    except Exception as e:
                        self.state["revenue"][key] = f"FAILED: {e}"[:300]
                        if any(k in str(e) for k in _BLOCK_MARKERS) and \
                                attempt <= self.settings.backfill_block_max_retries:
                            self._cooldown(key, attempt)
                            continue
                        break
                self.save()

    def adjust(self) -> None:
        if self.state.get("adjust_through") == str(self.end) and not self.state.get("adjust_dirty"):
            return
        self.save(phase="adjust", status="RUNNING")
        with session_scope(self.engine) as s:
            sids = [sid for (sid,) in s.execute(select(CorporateAction.security_id).distinct())]
        for i in range(0, len(sids), 200):
            with session_scope(self.engine) as s:
                for sid in sids[i:i + 200]:
                    recompute_adj_factors(s, sid)
        total = 0
        q0 = date(self.start.year, ((self.start.month - 1) // 3) * 3 + 1, 1)
        while q0 <= self.end:
            q1 = (q0.replace(month=q0.month + 3) if q0.month < 10 else date(q0.year + 1, 1, 1)) - timedelta(days=1)
            with session_scope(self.engine) as s:
                n = compute_for_dates(s, max(q0, self.start), min(q1, self.end))
            total += n
            self.save(phase_note=f"indicators through {min(q1, self.end)} ({total} rows)")
            q0 = q1 + timedelta(days=1)
        self.state.update(indicators_rows=total, adjust_through=str(self.end), adjust_dirty=False)

    # -------------------------------------------------------------- PHASE 1 gap datasets
    def _step(self, bucket: str, key: str, fn) -> None:
        """Run one resumable unit of work; result kept in state[bucket][key] ('DONE ...' = skip next time)."""
        book = self.state.setdefault(bucket, {})
        if str(book.get(key, "")).startswith("DONE"):
            return
        for attempt in range(1, self.settings.backfill_block_max_retries + 2):
            try:
                book[key] = f"DONE {fn()}"[:300]
                break
            except Exception as e:
                book[key] = f"FAILED: {e}"[:300]
                if any(k in str(e) for k in _BLOCK_MARKERS) and attempt <= self.settings.backfill_block_max_retries:
                    self._cooldown(key, attempt)
                    continue
                break
        self.save()

    def corporate_events(self) -> None:
        """G4/G8: TPEx ex-rights, capital reductions, par-value changes, ETF splits; then re-derive the
        reference prev_close on event days and mark adjustments for recomputation."""
        from aqt.data import events
        from aqt.data.sources.corporate_events import EVENTS
        self.save(phase="corporate_events", status="RUNNING")
        for name in EVENTS:
            for y in range(self.start.year, self.end.year + 1):
                s0, e0 = max(self.start, date(y, 1, 1)), min(self.end, date(y, 12, 31))
                self._step("corporate_events", f"{name}:{y}",
                           lambda: events.ingest_corporate_events(self.engine, self.fetcher, s0, e0, (name,),
                                                                  recompute=False)[name])
        self._step("corporate_events", f"prev_close_refresh:{self.end}",
                   lambda: events.refresh_prev_close_on_event_days(self.engine, self.start, self.end))
        self.state["adjust_dirty"] = True
        self.save()

    def restrictions(self) -> None:
        """G6: disposition and halt ranges per year, then the daily altered-trading lists per trading day."""
        from aqt.data import events
        self.save(phase="restrictions", status="RUNNING")
        for y in range(self.start.year, self.end.year + 1):
            s0, e0 = max(self.start, date(y, 1, 1)), min(self.end, date(y, 12, 31))
            self._step("restrictions", f"ranges:{s0}..{e0}",
                       lambda: events.ingest_restriction_ranges(self.engine, self.fetcher, s0, e0))
        with session_scope(self.engine) as s:
            days = [d for (d,) in s.execute(select(DailyPrice.trade_date).distinct()
                                            .where(DailyPrice.trade_date.between(self.start, self.end))
                                            .order_by(DailyPrice.trade_date))]
        t0 = time.monotonic()
        for i, d in enumerate(days):
            self._step("restriction_snapshots", str(d),
                       lambda: sum(v["rows"] for v in events.ingest_restriction_snapshot(
                           self.engine, self.fetcher, d).values()))
            if i % 20 == 0:
                left = (time.monotonic() - t0) / (i + 1) * (len(days) - i - 1)
                self.save(phase_note=f"restriction snapshots {i + 1}/{len(days)} ({d}); ~{int(left / 60)} min left")

    def financials(self) -> None:
        """G7 values: MOPS summary income statement + balance sheet per market and quarter."""
        from aqt.data import events
        self.save(phase="financials", status="RUNNING")
        for y in range(self.start.year, self.end.year + 1):
            for q in (1, 2, 3, 4):
                from aqt.data.pit import financial_statement_deadline
                if financial_statement_deadline(y, q) > self.end + timedelta(days=1):
                    continue  # not due yet
                for m in pipeline.MARKETS:
                    self._step("financials", f"{m}:{y}Q{q}",
                               lambda: events.ingest_financial_quarter(self.engine, self.fetcher, m, y, q))

    def financial_pubdates(self) -> None:
        """G7 publication time: one TWSE e-document query per company and fiscal year."""
        from aqt.data import events
        self.save(phase="financial_pubdates", status="RUNNING")
        todo = events.companies_needing_pubdates(self.engine, self.start.year, self.end.year)
        t0 = time.monotonic()
        for i, (sid, code, y) in enumerate(todo):
            self._step("financial_pubdates", f"{code}:{y}",
                       lambda: events.ingest_publication_times(self.engine, self.fetcher, sid, code, y))
            if i % 25 == 0:
                left = (time.monotonic() - t0) / (i + 1) * (len(todo) - i - 1)
                self.save(phase_note=f"publication times {i + 1}/{len(todo)} ({code} {y}); "
                                     f"~{int(left / 60)} min left")

    def quality_review(self) -> None:
        """G13: classify every price anomaly (with independent verification of the unexplained ones)."""
        from aqt.data import quality_review as qr
        self.save(phase="quality_review", status="RUNNING")
        counts = qr.classify_move_issues(self.engine, self.fetcher)
        qr.classify_known_checks(self.engine)
        self.state["quality_review"] = counts
        self.save()

    # -------------------------------------------------------------- driver
    def run(self, phases=("corporate_actions", "daily", "revenue", "adjust")) -> dict:
        own = self.fetcher is None
        self.fetcher = self.fetcher or Fetcher(self.engine)
        self.save(status="RUNNING", run_started_at=_now(), finished_at=None)
        log_event(self.engine, "INFO", "backfill_full", f"start {self.start}..{self.end} phases={phases}")
        try:
            for p in phases:
                if p == "revenue" and not self.revenue:
                    continue
                {"corporate_actions": self.corporate_actions, "daily": self.daily,
                 "revenue": self.revenue_history, "adjust": self.adjust,
                 "corporate_events": self.corporate_events, "restrictions": self.restrictions,
                 "financials": self.financials, "financial_pubdates": self.financial_pubdates,
                 "quality_review": self.quality_review}[p]()
            incomplete = [k for k, v in self.state["months"].items() if v.get("status") != "DONE"]
            incomplete += [k for k, v in self.state["revenue"].items() if not v.startswith("DONE")]
            incomplete += [k for k, v in self.state["corporate_actions"].items() if v != "DONE"]
            for bucket in ("corporate_events", "restrictions", "restriction_snapshots", "financials",
                           "financial_pubdates"):
                incomplete += [f"{bucket}/{k}" for k, v in self.state.get(bucket, {}).items()
                               if not str(v).startswith("DONE")]
            self.save(status="DONE" if not incomplete else "DONE_WITH_GAPS", phase=None,
                      incomplete=incomplete, finished_at=_now())
        except BaseException as e:  # KeyboardInterrupt / SystemExit from SIGTERM included
            self.save(status="INTERRUPTED", interrupted_reason=f"{type(e).__name__}: {e}"[:300])
            log_event(self.engine, "WARN", "backfill_full", f"interrupted: {type(e).__name__}: {e}")
            raise
        finally:
            if own:
                self.fetcher.close()
        log_event(self.engine, "INFO", "backfill_full", f"finished: {self.state['status']}")
        return summary(self.state)


def summary(state: dict | None) -> dict:
    if not state:
        return {"status": "NEVER_RUN"}
    months = state.get("months", {})
    by_status: dict[str, int] = {}
    for v in months.values():
        by_status[v.get("status")] = by_status.get(v.get("status"), 0) + 1
    missing: dict[str, int] = {}
    for v in months.values():
        for ds, days in v.get("missing", {}).items():
            missing[ds] = missing.get(ds, 0) + len(days)
    rev = state.get("revenue", {})
    return {
        "status": state.get("status"), "phase": state.get("phase"), "window": f"{state.get('start')}..{state.get('end')}",
        "current_day": state.get("current_day"), "days_done": state.get("days_done", 0),
        "days_total": state.get("days_total"), "eta_utc": state.get("eta"), "note": state.get("phase_note"),
        "months_by_status": by_status, "missing_day_counts": missing,
        "failed_days": sum(len(v.get("failed", [])) for v in months.values()),
        "revenue_months_done": sum(1 for v in rev.values() if v.startswith("DONE")),
        "revenue_months_not_done": {k: v for k, v in rev.items() if not v.startswith("DONE")},
        "corporate_action_chunks_failed": {k: v for k, v in state.get("corporate_actions", {}).items() if v != "DONE"},
        "gap_datasets": {b: {"done": sum(1 for v in state.get(b, {}).values() if str(v).startswith("DONE")),
                             "not_done": sum(1 for v in state.get(b, {}).values() if not str(v).startswith("DONE"))}
                         for b in ("corporate_events", "restrictions", "restriction_snapshots", "financials",
                                   "financial_pubdates") if state.get(b)},
        "quality_review": state.get("quality_review"),
        "blocked_events": state.get("blocked_events", 0), "updated_at": state.get("updated_at"),
        "interrupted_reason": state.get("interrupted_reason") if state.get("status") == "INTERRUPTED" else None,
    }


def status(engine: Engine, state_key: str = STATE_KEY) -> dict:
    return summary(get_state(engine, state_key))
