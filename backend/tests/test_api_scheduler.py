from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from aqt.data import pipeline
from aqt.db import session_scope
from aqt.jobs import scheduler
from aqt.models import JobRun


@pytest.fixture()
def client(engine):
    from aqt.api.main import app
    return TestClient(app)


def test_api_end_to_end(engine, fetcher, synthetic_market, client):
    m = synthetic_market
    pipeline.backfill(engine, m.dates[0], m.dates[64], fetcher)
    pipeline.run_daily_update(engine, m.dates[65], fetcher)

    h = client.get("/api/health").json()
    assert h["trading_mode"] == "SIMULATION" and h["safe_mode"]["on"] is False
    assert h["latest_price_date"] == str(m.dates[65])
    assert h["realtime_quotes"]["available"] is False

    stocks = client.get("/api/stocks", params={"market": "TWSE"}).json()
    assert {s["code"] for s in stocks} >= {"2330", "0050"}

    px = client.get("/api/stocks/2330/prices", params={"start": str(m.dates[0]), "end": str(m.dates[65])}).json()
    assert len(px["data"]) == 66 and px["data"][-1]["limit_up"] is not None

    adj = client.get("/api/stocks/2317/prices", params={"start": str(m.dates[0]), "end": str(m.dates[65]),
                                                       "adjusted": True}).json()
    raw = client.get("/api/stocks/2317/prices", params={"start": str(m.dates[0]), "end": str(m.dates[65])}).json()
    assert adj["data"][0]["close"] == raw["data"][0]["close"]  # no ex-date before dates[65]

    ind = client.get("/api/stocks/2330/indicators", params={"start": str(m.dates[60]), "end": str(m.dates[65])}).json()
    assert ind["data"][-1]["ma60"] is not None

    assert client.get("/api/stocks/0000/prices").status_code == 404
    cov = client.get("/api/data/coverage").json()
    assert cov["daily_prices"]["rows"] == 10 * 66
    assert any(src["dataset"] == "twse_quotes" for src in client.get("/api/data/sources").json())
    assert client.get("/api/jobs").json()[0]["job"] == "daily_update"
    inst = client.get("/api/stocks/2330/institutional").json()
    assert inst["data"][0]["total_net"] == 232_000


def test_catch_up_after_downtime(engine, fetcher, synthetic_market):
    """Simulate a restart: days without a successful daily_update are re-run from DB state."""
    m = synthetic_market
    pipeline.run_daily_update(engine, m.dates[0], fetcher)
    today = m.dates[4]
    ran = scheduler.catch_up(engine, today=today, fetcher=fetcher)
    assert set(m.dates[1:5]) <= set(ran) and m.dates[0] not in ran  # earlier weekdays resolve to SKIPPED
    with session_scope(engine) as s:
        done = {p["date"] for (p,) in s.execute(select(JobRun.params).where(JobRun.status == "SUCCESS"))}
    assert {str(d) for d in m.dates[:5]} <= done
    assert scheduler.catch_up(engine, today=today, fetcher=fetcher) == []  # nothing left


def test_scheduler_jobs_registered():
    sched = scheduler.build_scheduler()
    ids = {j.id for j in sched.get_jobs()}
    assert {"daily_eod", "late_datasets", "fundamentals", "securities", "calendar", "heartbeat"} <= ids
