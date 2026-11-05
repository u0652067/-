"""A job killed mid-run: job_runs stays as it was, evidence decides, and only an audit event clears the day."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from aqt.data import pipeline, reconcile
from aqt.data.integrity import _verdict, run_integrity_check
from aqt.db import session_scope
from aqt.jobs.runtime import set_state
from aqt.models import JobRun
from aqt.research.engine import DataNotReady, check_readiness


class Killed(BaseException):
    """Stands in for the container being stopped: `except Exception` in job_run cannot see it."""


def _stuck_day(engine, fetcher, monkeypatch, d):
    pipeline.run_daily_update(engine, d, fetcher)

    def die(*a, **k):
        raise Killed()
    with monkeypatch.context() as mp:
        mp.setattr(pipeline, "compute_for_dates", die)
        with pytest.raises(Killed):
            pipeline.run_daily_update(engine, d, fetcher)
    with session_scope(engine) as s:
        job = s.query(JobRun).filter(JobRun.status == "RUNNING").one()
        return job.id


@pytest.fixture()
def stuck(engine, fetcher, synthetic_market, monkeypatch):
    m = synthetic_market
    monkeypatch.setattr(reconcile, "STALE_AFTER", timedelta(0))
    for d in m.dates[:3]:
        pipeline.run_daily_update(engine, d, fetcher)
    job = _stuck_day(engine, fetcher, monkeypatch, m.dates[3])
    pipeline.run_daily_update(engine, m.dates[4], fetcher)
    return job, m


def _job(engine, job):
    with engine.connect() as c:
        return tuple(c.execute(text("SELECT status, finished_at, result::text, error FROM job_runs WHERE id=:i"),
                               {"i": job}).one())


def test_killed_job_blocks_the_window_until_reconciled_and_job_runs_is_never_changed(engine, stuck, tmp_path):
    job, m = stuck
    before = _job(engine, job)
    assert before[0] == "RUNNING"
    rep = run_integrity_check(engine, m.dates[0], m.dates[4], str(tmp_path), sha_sample=5)
    assert [r["day"] for r in rep["rerunnable_days"]] == [str(m.dates[3])]
    assert rep["verdict"]["technical_backtest_ready"] is False

    out = reconcile.record(engine, job, "killed by a deployment; rows verified against payloads", "tester")
    assert out["outcome"] == "DATA_COMPLETE" and len(out["evidence_sha256"]) == 64
    assert _job(engine, job) == before  # no status was rewritten

    rep = run_integrity_check(engine, m.dates[0], m.dates[4], str(tmp_path), sha_sample=5)
    assert rep["rerunnable_days"] == [] and rep["reconciled_days"][0]["job_run_id"] == job
    assert not any("FAILED/PARTIAL" in g for g in rep["verdict"]["coverage"]["gaps"])
    assert rep["reconciled_days"][0]["executed_by"] == "tester"


def test_evidence_lists_every_dataset_and_is_read_only(engine, stuck):
    job, m = stuck
    ev = reconcile.verify_daily_job(engine, job)
    assert len(ev["datasets"]) == 8 and all(d["ok"] for d in ev["datasets"])
    assert all(d["sha256_verified"] and d["value_mismatches"] == 0 for d in ev["datasets"])
    assert ev["checks"]["earlier_success_on_the_same_day"] and ev["outcome"] == "DATA_COMPLETE"
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM job_reconciliations")).scalar() == 0


def test_a_changed_row_makes_the_outcome_incomplete_and_does_not_clear_the_day(engine, stuck, tmp_path):
    job, m = stuck
    with engine.begin() as c:
        c.execute(text("UPDATE daily_prices SET close = close + 1 WHERE trade_date=:d "
                       "AND security_id=(SELECT min(security_id) FROM daily_prices)"), {"d": m.dates[3]})
    ev = reconcile.verify_daily_job(engine, job)
    assert ev["outcome"] == "DATA_INCOMPLETE" and sum(d["value_mismatches"] for d in ev["datasets"]) == 1
    assert reconcile.record(engine, job, "checked", "tester")["outcome"] == "DATA_INCOMPLETE"
    rep = run_integrity_check(engine, m.dates[0], m.dates[4], str(tmp_path), sha_sample=5)
    assert [r["job_run_id"] for r in rep["rerunnable_days"]] == [job]


def test_a_missing_payload_is_incomplete(engine, stuck):
    job, m = stuck
    with engine.begin() as c:
        c.execute(text("DELETE FROM margin_trades WHERE trade_date=:d"), {"d": m.dates[3]})
    assert reconcile.verify_daily_job(engine, job)["outcome"] == "DATA_INCOMPLETE"


def test_reconciliation_is_append_only(engine, stuck):
    job, _ = stuck
    reconcile.record(engine, job, "checked", "tester")
    for stmt in ("UPDATE job_reconciliations SET outcome='DATA_INCOMPLETE'", "DELETE FROM job_reconciliations",
                 "TRUNCATE job_reconciliations"):
        with pytest.raises(DBAPIError), engine.begin() as c:
            c.execute(text(stmt))


def test_only_stale_stuck_daily_updates_can_be_reconciled(engine, stuck, monkeypatch):
    job, _ = stuck
    with pytest.raises(reconcile.NotReconcilable, match="reason"):
        reconcile.record(engine, job, " ", "tester")
    monkeypatch.setattr(reconcile, "STALE_AFTER", timedelta(hours=2))
    with pytest.raises(reconcile.NotReconcilable, match="may still be running"):
        reconcile.verify_daily_job(engine, job)
    assert reconcile.verify_daily_job(engine, job, datetime.now(timezone.utc) + timedelta(hours=3))
    with engine.connect() as c:
        done = c.execute(text("SELECT id FROM job_runs WHERE status='SUCCESS' LIMIT 1")).scalar()
    with pytest.raises(reconcile.NotReconcilable, match="only a stuck"):
        reconcile.verify_daily_job(engine, done)


def test_a_new_stuck_job_on_a_reconciled_day_is_a_gap_again(engine, stuck, fetcher, monkeypatch, tmp_path):
    job, m = stuck
    reconcile.record(engine, job, "checked", "tester")
    second = None
    with monkeypatch.context() as mp:
        mp.setattr(pipeline, "compute_for_dates", lambda *a, **k: (_ for _ in ()).throw(Killed()))
        with pytest.raises(Killed):
            pipeline.run_daily_update(engine, m.dates[3], fetcher)
    with engine.connect() as c:
        second = c.execute(text("SELECT max(id) FROM job_runs WHERE status='RUNNING'")).scalar()
    rep = run_integrity_check(engine, m.dates[0], m.dates[4], str(tmp_path), sha_sample=5)
    assert second != job and [r["job_run_id"] for r in rep["rerunnable_days"]] == [second]


def _report(window, days=(), other_gap=False):
    return {"window": window, "quotes_by_year": [], "secondary_gaps": {}, "revenue_months": [],
            "unknown_weekdays": ["x"] if other_gap else [], "rerunnable_days": [{"day": d} for d in days],
            "raw_payloads": {"sha256_sample": {"mismatch": 0}}, "quality_review": [], "financials_pit": []}


def test_ready_through_stops_before_the_first_problem_day():
    v = _verdict(_report("2019-01-01..2026-11-04", ["2026-11-02"]))
    assert v["technical_backtest_ready"] is False and v["technical_ready_through"] == "2026-11-01"
    assert _verdict(_report("2019-01-01..2026-11-04"))["technical_ready_through"] == "2026-11-04"
    assert _verdict(_report("2026-11-02..2026-11-04", ["2026-11-02"]))["technical_ready_through"] is None
    assert _verdict(_report("2019-01-01..2026-11-04", other_gap=True))["technical_ready_through"] is None


def _readiness(engine, **kw):
    set_state(engine, "data_readiness", {"quality_status": "REVIEWED_WITH_EXCLUSIONS",
                                         "fundamental_backtest_ready": False, **kw})


def test_moving_the_check_forward_does_not_take_readiness_from_an_earlier_window(engine):
    """Regression: a stuck job on 2026-11-02 turned every backtest off once the check's end date passed it."""
    from datetime import date
    _readiness(engine, technical_backtest_ready=False, coverage_complete=False,
               technical_ready_through="2026-11-01", window="2019-01-01..2026-11-04")
    with session_scope(engine) as s:
        assert check_readiness(s, False, date(2026, 10, 31))
        assert check_readiness(s, False, date(2026, 11, 1))
        for end in (date(2026, 11, 2), date(2026, 11, 4), None):
            with pytest.raises(DataNotReady):
                check_readiness(s, False, end)


def test_readiness_stays_fail_closed_past_the_checked_window(engine):
    from datetime import date
    _readiness(engine, technical_backtest_ready=True, coverage_complete=True,
               technical_ready_through="2026-10-31", window="2019-01-01..2026-10-31")
    with session_scope(engine) as s:
        assert check_readiness(s, False, date(2026, 10, 31))
        with pytest.raises(DataNotReady, match="after the checked window"):
            check_readiness(s, False, date(2026, 11, 3))
    _readiness(engine, technical_backtest_ready=False, coverage_complete=False, window="2019-01-01..2026-10-31")
    with session_scope(engine) as s, pytest.raises(DataNotReady):
        check_readiness(s, False, date(2026, 1, 1))
