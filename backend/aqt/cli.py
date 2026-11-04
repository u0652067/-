"""Command-line entry point: `python -m aqt.cli <command>`."""
import argparse
import json
import logging
import os
import signal
from datetime import date, datetime, timedelta

from aqt.config import get_settings
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
    rq = sub.add_parser("review-quality", help="G13: classify price anomalies + independent sample verification")
    rq.add_argument("--sample", type=int, default=5, help="verified samples per classification (0 = none)")
    rq.add_argument("--out")
    bt = sub.add_parser("backtest", help="PHASE 2: technical backtest (research only, saved append-only)")
    bt.add_argument("--strategy", required=True)
    bt.add_argument("--start", type=_d, required=True)
    bt.add_argument("--end", type=_d, required=True)
    bt.add_argument("--params", default="{}", help='JSON, e.g. {"top_n": 20}')
    bt.add_argument("--cash", type=float, default=10_000_000)
    ev = sub.add_parser("evaluate", help="PHASE 2: chronological out-of-sample or walk-forward evaluation")
    ev.add_argument("--mode", choices=("oos", "walk-forward"), required=True)
    ev.add_argument("--strategy", required=True)
    ev.add_argument("--start", type=_d, required=True)
    ev.add_argument("--end", type=_d, required=True)
    ev.add_argument("--grid", default="[{}]", help="JSON list of parameter sets")
    ev.add_argument("--train-days", type=int, default=500)
    ev.add_argument("--test-days", type=int, default=120)
    ev.add_argument("--anchored", action="store_true")
    fs = sub.add_parser("factor-study", help="PHASE 2: factor / score predictive-power study (before costs)")
    fs.add_argument("--start", type=_d, required=True)
    fs.add_argument("--end", type=_d, required=True)
    fs.add_argument("--horizon", type=int, default=20)
    fs.add_argument("--markets", default="TWSE,TPEX")
    fs.add_argument("--group", default="technical_v1")
    sub.add_parser("research-registry", help="PHASE 2: registered candidates and their review stage")
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
    elif args.cmd == "review-quality":
        from aqt.data import quality_review as qr
        from aqt.data.http import Fetcher
        f = Fetcher(engine)
        try:
            out = {"classified": qr.classify_move_issues(engine, f), "known_checks": qr.classify_known_checks(engine)}
            out["sample_verification"] = qr.sample_verification(engine, f, args.sample) if args.sample else []
            out["summary"] = qr.summary(engine)
        finally:
            f.close()
        out_dir = args.out or (os.path.join(get_settings().report_dir, "quality") if get_settings().report_dir else None)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
            path = os.path.join(out_dir, f"quality_review_{datetime.now(TPE):%Y%m%dT%H%M%S}.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(out, fh, ensure_ascii=False, indent=1, default=str)
            out["saved_to"] = path
    elif args.cmd in ("backtest", "evaluate"):
        from aqt.research import DISCLAIMER
        from aqt.research.engine import BacktestConfig
        from aqt.research import evaluation as ev_mod
        with session_scope(engine) as s:
            if args.cmd == "backtest":
                cfg = BacktestConfig(args.start, args.end, initial_cash=args.cash)
                rid, res = ev_mod.run_and_save(s, args.strategy, json.loads(args.params), cfg, "IN_SAMPLE",
                                               {"role": "manual"})
                out = {"run_id": rid, "results_hash": res.results_hash(), "metrics": res.metrics,
                       "rejections": len(res.rejections)}
            else:
                cfg = BacktestConfig(args.start, args.end)
                grid = json.loads(args.grid)
                out = (ev_mod.out_of_sample(s, args.strategy, grid, cfg) if args.mode == "oos" else
                       ev_mod.walk_forward(s, args.strategy, grid, cfg, args.train_days, args.test_days,
                                           anchored=args.anchored))
        out["disclaimer"] = DISCLAIMER
        rd = get_settings().report_dir
        if rd:
            os.makedirs(os.path.join(rd, "backtests"), exist_ok=True)
            path = os.path.join(rd, "backtests", f"{args.cmd}_{args.strategy}_{datetime.now(TPE):%Y%m%dT%H%M%S}.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(out, fh, ensure_ascii=False, indent=1, default=str)
            out["saved_to"] = path
    elif args.cmd == "factor-study":
        from aqt.research import DISCLAIMER
        from aqt.research import factors as fm
        from aqt.research.engine import check_readiness
        from aqt.research.records import code_fingerprint
        cfg = fm.StudyConfig(args.start, args.end, horizon=args.horizon, markets=tuple(args.markets.split(",")),
                             study_group=args.group)
        with session_scope(engine) as s:
            check_readiness(s)
            results = fm.run_study(s, cfg)
            ids = fm.save_studies(s, cfg, results, code_fingerprint()[:40])
        mt = fm.multiple_testing(results)
        out = {"study_ids": ids, "multiple_testing": mt, "disclaimer": DISCLAIMER, "factors": {
            f: {**{k: v for k, v in r.items() if k != "per_date"},
                "evidence_label": fm.evidence_label(r, mt["factors"].get(f, {}).get("holm_p"))}
            for f, r in results.items()}}
        rd = get_settings().report_dir
        if rd:
            os.makedirs(os.path.join(rd, "research"), exist_ok=True)
            path = os.path.join(rd, "research", f"factor_study_{args.group}_h{args.horizon}_{args.markets.replace(',', '-')}.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(out, fh, ensure_ascii=False, indent=1, default=str)
            out = {"saved_to": path, "multiple_testing": mt, "study_ids": ids}
    elif args.cmd == "research-registry":
        from aqt.research.registry import registry_listing
        with session_scope(engine) as s:
            out = registry_listing(s)
    elif args.cmd == "integrity-check":
        from aqt.data.integrity import run_integrity_check
        out = run_integrity_check(engine, args.start, args.end, args.out)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
