"""Background scheduler (separate process from the API; does not need any chat window open).

State lives in PostgreSQL, so after a restart `catch_up()` re-runs any trading day whose
`daily_update` did not finish successfully.
"""
import logging
from datetime import date, datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from aqt.config import get_settings
from aqt.data import pipeline
from aqt.data.calendar import expected_trading_day
from aqt.data.pit import TPE
from aqt.db import get_engine, session_scope
from aqt.jobs.runtime import job_run, log_event
from aqt.models import JobRun

log = logging.getLogger(__name__)
CATCH_UP_DAYS = 10


def _done_dates(engine) -> set[str]:
    with session_scope(engine) as s:
        rows = s.execute(select(JobRun.params).where(JobRun.job_name == "daily_update",
                                                     JobRun.status.in_(("SUCCESS", "SKIPPED")))).all()
    return {p.get("date") for (p,) in rows if p}


def catch_up(engine, today: date | None = None, fetcher=None) -> list[date]:
    """Re-run daily_update for recent weekdays without a successful run (after downtime)."""
    now = datetime.now(TPE)
    today = today or now.date()
    done = _done_dates(engine)
    ran = []
    for i in range(CATCH_UP_DAYS, -1, -1):
        d = today - timedelta(days=i)
        if d.weekday() >= 5 or str(d) in done:
            continue
        if d == now.date() and now.hour < 15:
            continue  # today's data not published yet
        with session_scope(engine) as s:
            if not expected_trading_day(s, d):
                continue
        try:
            pipeline.run_daily_update(engine, d, fetcher)
            ran.append(d)
        except Exception as e:  # logged by job_run
            log.error("catch-up %s failed: %s", d, e)
    return ran


def job_daily_eod():
    engine = get_engine()
    today = datetime.now(TPE).date()
    pipeline.run_daily_update(engine, today)


def job_late_datasets():
    """Margin data is published ~21:00; re-run the day so it is picked up (idempotent upserts)."""
    engine = get_engine()
    today = datetime.now(TPE).date()
    with session_scope(engine) as s:
        if not expected_trading_day(s, today):
            return
    pipeline.run_daily_update(engine, today, compute_indicators=True)


def job_fundamentals():
    pipeline.sync_fundamentals(get_engine())


def job_securities():
    pipeline.sync_securities(get_engine())


def job_calendar():
    engine = get_engine()
    y = datetime.now(TPE).year
    with job_run(engine, "sync_calendar", {"year": y}) as j:
        j.result["rows"] = pipeline.sync_calendar(engine, y) + pipeline.sync_calendar(engine, y + 1)


def job_heartbeat():
    log_event(get_engine(), "INFO", "scheduler", "heartbeat")


def build_scheduler() -> BlockingScheduler:
    sched = BlockingScheduler(timezone=get_settings().timezone)
    cron = dict(day_of_week="mon-fri")
    sched.add_job(job_securities, CronTrigger(hour=7, minute=40, **cron), id="securities", coalesce=True)
    sched.add_job(job_daily_eod, CronTrigger(hour=17, minute=45, **cron), id="daily_eod", coalesce=True,
                  misfire_grace_time=3600 * 6)
    sched.add_job(job_late_datasets, CronTrigger(hour=22, minute=15, **cron), id="late_datasets", coalesce=True,
                  misfire_grace_time=3600 * 6)
    sched.add_job(job_fundamentals, CronTrigger(hour=19, minute=30), id="fundamentals", coalesce=True,
                  misfire_grace_time=3600 * 12)
    sched.add_job(job_calendar, CronTrigger(day=1, hour=6, minute=0), id="calendar", coalesce=True)
    sched.add_job(job_heartbeat, CronTrigger(minute="*/15"), id="heartbeat", coalesce=True)
    return sched


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    engine = get_engine()
    log_event(engine, "INFO", "scheduler", "scheduler starting; running catch-up")
    catch_up(engine)
    build_scheduler().start()


if __name__ == "__main__":
    main()
