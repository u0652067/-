"""Establish what a job that never reported an end state actually did (`python -m aqt.cli reconcile-job`).

A daily_update killed mid-run stays RUNNING. job_runs is history, so that row is never changed. Instead:
`verify_daily_job` rebuilds the facts from the job's own raw payloads and the stored rows, read-only, and
`record` appends the result to job_reconciliations with reason, evidence, time and executor.

Only a DATA_COMPLETE record for that exact job clears the day in integrity-check.
"""
import gzip
import hashlib
import json
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from aqt.data.pipeline import DAILY

STALE_AFTER = timedelta(hours=2)
SAME_RUN_WINDOW = timedelta(minutes=30)
TABLES = {"quotes": "daily_prices", "institutional": "institutional_trades", "margin": "margin_trades",
          "valuation": "valuations"}
NOT_COMPARED = {"code", "name", "market", "trade_date", "source", "note"}


class NotReconcilable(RuntimeError):
    pass


def _same(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, (bool, str)) or isinstance(b, (bool, str)):
        return str(a) == str(b)
    return Decimal(str(a)) == Decimal(str(b))


def _dataset(c: Connection, market: str, dataset: str, d: date, job_start: datetime) -> dict:
    tag = f"{market.lower()}_{dataset}"
    out = {"dataset": tag, "ok": False}
    raw = c.execute(text(
        "SELECT id, sha256, content_gz, http_status, parse_status, fetched_at FROM raw_payloads "
        "WHERE dataset=:t AND data_date=:d AND fetched_at>=:a AND fetched_at<:b ORDER BY fetched_at DESC LIMIT 1"),
        {"t": tag, "d": d, "a": job_start, "b": job_start + SAME_RUN_WINDOW}).mappings().first()
    if raw is None:
        out["problem"] = "the job stored no payload for this dataset"
        return out
    body = gzip.decompress(raw["content_gz"])
    sha_ok = hashlib.sha256(body).hexdigest() == raw["sha256"]
    previous = c.execute(text("SELECT sha256 FROM raw_payloads WHERE dataset=:t AND data_date=:d AND fetched_at<:a "
                              "ORDER BY fetched_at DESC LIMIT 1"), {"t": tag, "d": d, "a": raw["fetched_at"]}).scalar()
    recs = {r.code: asdict(r) for r in DAILY[(market, dataset)][2](json.loads(body), d).records}
    table = TABLES[dataset]
    rows = {r["code"]: dict(r) for r in c.execute(text(
        f"SELECT s.code, t.* FROM {table} t JOIN securities s ON s.id=t.security_id "
        "WHERE t.trade_date=:d AND s.market=:m"), {"d": d, "m": market}).mappings()}
    from_payload = sum(1 for r in rows.values() if r["raw_payload_id"] == raw["id"])
    mismatched = []
    for code, rec in recs.items():
        row = rows.get(code)
        if row is None:
            mismatched.append(f"{code}: not stored")
            continue
        fields = [k for k in rec if k in row and k not in NOT_COMPARED]
        if any(not _same(rec[k], row[k]) for k in fields):
            mismatched.append(code)
    out.update(raw_payload_id=raw["id"], fetched_at=str(raw["fetched_at"]), http_status=raw["http_status"],
               parse_status=raw["parse_status"], sha256=raw["sha256"], sha256_verified=sha_ok,
               same_as_previous_payload=previous == raw["sha256"], payload_records=len(recs), stored_rows=len(rows),
               stored_rows_from_this_payload=from_payload, value_mismatches=len(mismatched),
               mismatch_sample=mismatched[:10])
    out["ok"] = bool(recs) and sha_ok and raw["http_status"] == 200 and raw["parse_status"] == "OK" \
        and from_payload == len(recs) == len(rows) and not mismatched
    return out


def verify_daily_job(engine: Engine, job_id: int, now: datetime | None = None) -> dict:
    """Read-only: facts about a stale daily_update, and the outcome they support."""
    now = now or datetime.now(timezone.utc)
    with engine.connect() as c:
        c.execute(text("SET TRANSACTION READ ONLY"))
        job = c.execute(text("SELECT id, job_name, status, started_at, finished_at, params, result "
                             "FROM job_runs WHERE id=:i"), {"i": job_id}).mappings().first()
        if job is None:
            raise NotReconcilable(f"job {job_id} does not exist")
        if job["job_name"] != "daily_update" or job["status"] != "RUNNING":
            raise NotReconcilable(f"job {job_id} is {job['job_name']}/{job['status']}: only a stuck daily_update")
        if now - job["started_at"] < STALE_AFTER:
            raise NotReconcilable(f"job {job_id} started less than {STALE_AFTER} ago: it may still be running")
        d = date.fromisoformat(job["params"]["date"])
        datasets = [_dataset(c, m, ds, d, job["started_at"]) for (m, ds) in DAILY]
        same_day = [dict(r) | {"started_at": str(r["started_at"]), "finished_at": str(r["finished_at"])} for r in
                    c.execute(text("SELECT id, status, started_at, finished_at FROM job_runs "
                                   "WHERE job_name='daily_update' AND params->>'date'=:d ORDER BY started_at"),
                              {"d": str(d)}).mappings()]
        earlier_success = [r["id"] for r in same_day if r["status"] == "SUCCESS" and r["id"] != job_id]
        n = lambda q: c.execute(text(q), {"d": d}).scalar()  # noqa: E731
        priced = n("SELECT count(*) FROM daily_prices WHERE trade_date=:d AND close IS NOT NULL")
        steps = {
            "priced_rows": priced,
            "indicator_rows": n("SELECT count(*) FROM technical_indicators WHERE trade_date=:d"),
            "indicators_computed_at": str(n("SELECT max(computed_at) FROM technical_indicators WHERE trade_date=:d")),
            "quality_issues": n("SELECT count(*) FROM data_quality_issues WHERE trade_date=:d"),
            "quality_issues_unclassified": n("SELECT count(*) FROM data_quality_issues WHERE trade_date=:d "
                                             "AND classification IS NULL"),
            "next_trading_day_success": n("SELECT count(*) FROM job_runs WHERE job_name='daily_update' "
                                          "AND status='SUCCESS' AND (params->>'date')::date > :d"),
        }
    quotes = [x for x in datasets if x["dataset"].endswith("_quotes")]
    prices_unchanged = all(x.get("same_as_previous_payload") for x in quotes)
    checks = {"every_dataset_matches_its_payload": all(x["ok"] for x in datasets),
              "earlier_success_on_the_same_day": bool(earlier_success),
              "prices_unchanged_since_that_success": prices_unchanged,
              "indicators_exist": steps["indicator_rows"] > 0,
              "quality_issues_classified": steps["quality_issues_unclassified"] == 0}
    return {"job_run_id": job_id, "job_status_in_job_runs": job["status"], "job_result_in_job_runs": job["result"],
            "data_date": str(d), "started_at": str(job["started_at"]), "verified_at": str(now),
            "same_day_jobs": same_day, "datasets": datasets, "downstream": steps, "checks": checks,
            "outcome": "DATA_COMPLETE" if all(checks.values()) else "DATA_INCOMPLETE"}


def record(engine: Engine, job_id: int, reason: str, executed_by: str) -> dict:
    """Append the reconciliation. job_runs is not touched."""
    if not reason.strip() or not executed_by.strip():
        raise NotReconcilable("reason and executor are required")
    evidence = verify_daily_job(engine, job_id)
    body = json.dumps(evidence, ensure_ascii=False, sort_keys=True, default=str)
    sha = hashlib.sha256(body.encode()).hexdigest()
    with engine.begin() as c:
        rid = c.execute(text(
            "INSERT INTO job_reconciliations (job_run_id, outcome, reason, evidence, evidence_sha256, executed_by) "
            "VALUES (:j, :o, :r, CAST(:e AS jsonb), :s, :b) RETURNING id"),
            {"j": job_id, "o": evidence["outcome"], "r": reason, "e": body, "s": sha, "b": executed_by}).scalar()
    return {"id": rid, "outcome": evidence["outcome"], "evidence_sha256": sha}
