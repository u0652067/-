"""Command-line entry point: `python -m aqt.cli <command>`."""
import argparse
import json
import logging
from datetime import date, datetime

from aqt.data.pit import TPE


def _d(s: str) -> date:
    return date.fromisoformat(s)


def main(argv=None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="aqt")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db", help="apply all migrations")
    u = sub.add_parser("update-daily", help="run the EOD pipeline for one date (default: today)")
    u.add_argument("--date", type=_d)
    b = sub.add_parser("backfill", help="date-by-date historical backfill")
    b.add_argument("--start", type=_d, required=True)
    b.add_argument("--end", type=_d, required=True)
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
    sub.add_parser("scheduler", help="run the background scheduler (blocking)")
    a = sub.add_parser("api")
    a.add_argument("--port", type=int, default=8000)
    args = p.parse_args(argv)

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

    from aqt.data import pipeline
    from aqt.db import get_engine, session_scope
    engine = get_engine()
    if args.cmd == "update-daily":
        out = pipeline.run_daily_update(engine, args.date or datetime.now(TPE).date())
    elif args.cmd == "backfill":
        out = pipeline.backfill(engine, args.start, args.end)
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
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
