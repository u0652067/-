"""Command-line entry point: `python -m aqt.cli <command>`."""
import argparse
import json
import logging
import os
import signal
from datetime import date, datetime, timedelta

from aqt.data.pit import TPE


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _setup_logging(cmd: str) -> None:
    fmt = "%(asctime)s %(levelname)s %(name)s %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    from aqt.config import get_settings
    log_dir = get_settings().log_dir
    if log_dir:
        from logging.handlers import TimedRotatingFileHandler
        os.makedirs(log_dir, exist_ok=True)
        handlers.append(TimedRotatingFileHandler(os.path.join(log_dir, f"{cmd}.log"), when="midnight",
                                                 backupCount=90, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _sigterm_as_exit() -> None:
    """`docker stop` sends SIGTERM: turn it into SystemExit so progress is saved in finally-blocks."""
    def handler(signum, frame):
        raise SystemExit(f"signal {signum}")
    signal.signal(signal.SIGTERM, handler)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="aqt")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db", help="apply all migrations")
    u = sub.add_parser("update-daily", help="run the EOD pipeline for one date (default: today)")
    u.add_argument("--date", type=_d)
    b = sub.add_parser("backfill", help="date-by-date historical backfill")
    b.add_argument("--start", type=_d, required=True)
    b.add_argument("--end", type=_d, required=True)
    b.add_argument("--only-missing", action="store_true", help="skip datasets already stored (resume)")
    sub.add_parser("sync-securities")
    c = sub.add_parser("sync-calendar")
    c.add_argument("--year", type=int, default=datetime.now(TPE).year)
    sub.add_parser("sync-fundamentals")
    r = sub.add_parser("backfill-revenue")
    r.add_argument("--year", type=int, required=True)
    r.add_argument("--month", type=int, required=True)
    i = sub.add_parser("compute-indicators")
    i.add_argument("--start", type=_d, required=True)
    i.add_argument("--end", type=_d, required=True)
    sub.add_parser("check-sources", help="live smoke test: fetch + parse one sample of every source")
    h = sub.add_parser("health-check", help="transport-level reachability + format evidence for every source")
    h.add_argument("--out", help="report directory (default: $AQT_REPORT_DIR/health)")
    f = sub.add_parser("backfill-full", help="resumable full backfill with persisted progress")
    f.add_argument("--start", type=_d, default=date(2019, 1, 1))
    f.add_argument("--end", type=_d, help="default: yesterday (Asia/Taipei)")
    f.add_argument("--no-revenue", action="store_true")
    f.add_argument("--rerun-done", action="store_true", help="re-check months already marked DONE")
    f.add_argument("--phases", default="corporate_actions,daily,revenue,adjust")
    f.add_argument("--state-key", default="backfill_full", help="separate progress record (e.g. for a smoke run)")
    bs = sub.add_parser("backfill-status", help="progress of backfill-full")
    bs.add_argument("--state-key", default="backfill_full")
    ic = sub.add_parser("integrity-check", help="row counts, gaps, failed sources, re-runnable days")
    ic.add_argument("--start", type=_d, default=date(2019, 1, 1))
    ic.add_argument("--end", type=_d)
    ic.add_argument("--out")
    sub.add_parser("scheduler", help="run the background scheduler (blocking)")
    a = sub.add_parser("api")
    a.add_argument("--port", type=int, default=8000)
    args = p.parse_args(argv)
    _setup_logging(args.cmd)
    _sigterm_as_exit()

    if args.cmd == "init-db":
        from alembic import command
        from alembic.config import Config
        command.upgrade(Config("alembic.ini"), "head")
        return
    if args.cmd == "scheduler":
        from aqt.jobs.scheduler import main as sched_main
        sched_main()
        return
    if args.cmd == "api":
        import uvicorn
        uvicorn.run("aqt.api.main:app", host="0.0.0.0", port=args.port)
        return
    if args.cmd == "health-check":
        from aqt.data.health import run_health_check
        out = run_health_check(args.out)
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return

    from aqt.data import pipeline
    from aqt.db import get_engine, session_scope
    engine = get_engine()
    if args.cmd == "update-daily":
        out = pipeline.run_daily_update(engine, args.date or datetime.now(TPE).date())
    elif args.cmd == "backfill":
        out = pipeline.backfill(engine, args.start, args.end, only_missing=args.only_missing)
    elif args.cmd == "sync-securities":
        out = pipeline.sync_securities(engine)
    elif args.cmd == "sync-calendar":
        out = pipeline.sync_calendar(engine, args.year)
    elif args.cmd == "sync-fundamentals":
        out = pipeline.sync_fundamentals(engine)
    elif args.cmd == "backfill-revenue":
        out = {m: pipeline.backfill_revenue(engine, m, args.year, args.month) for m in pipeline.MARKETS}
    elif args.cmd == "compute-indicators":
        from aqt.indicators.compute import compute_for_dates
        with session_scope(engine) as s:
            out = compute_for_dates(s, args.start, args.end)
    elif args.cmd == "check-sources":
        from aqt.data.smoke import check_sources
        out = check_sources()
    elif args.cmd == "backfill-full":
        from aqt.data.backfill import FullBackfill
        end = args.end or datetime.now(TPE).date() - timedelta(days=1)
        out = FullBackfill(engine, args.start, end, revenue=not args.no_revenue,
                           rerun_done=args.rerun_done, state_key=args.state_key).run(tuple(args.phases.split(",")))
    elif args.cmd == "backfill-status":
        from aqt.data.backfill import status
        out = status(engine, args.state_key)
    elif args.cmd == "integrity-check":
        from aqt.data.integrity import run_integrity_check
        out = run_integrity_check(engine, args.start, args.end, args.out)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
