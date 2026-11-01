"""Local deployment operations: resumable full backfill, raw mirror, health check, integrity report.

All offline (httpx.MockTransport + SYNTHETIC fixtures)."""
import gzip
import hashlib
import os
from datetime import date

import httpx
from sqlalchemy import func, select

from aqt.config import Settings
from aqt.data.backfill import FullBackfill, latest_published_revenue_month, status
from aqt.data.health import run_health_check
from aqt.data.http import Fetcher
from aqt.data.integrity import run_integrity_check
from aqt.db import session_scope
from aqt.jobs.runtime import get_state
from aqt.models import DailyPrice, InstitutionalTrade, TechnicalIndicator

START, END = date(2024, 1, 2), date(2024, 1, 12)
PHASES = ("corporate_actions", "daily", "adjust")


def _count(s, model, *where):
    return s.scalar(select(func.count()).select_from(model).where(*where))


def test_full_backfill_completes_and_persists_progress(engine, fetcher):
    out = FullBackfill(engine, START, END, fetcher, sleep=lambda s: None, revenue=False).run(PHASES)
    assert out["status"] == "DONE" and out["days_done"] == out["days_total"] == 9
    assert out["months_by_status"] == {"DONE": 1} and out["missing_day_counts"] == {}
    st = get_state(engine, "backfill_full")
    assert st["months"]["2024-01"]["trading_days"] == 9 and st["current_day"] == "2024-01-12"
    with session_scope(engine) as s:
        assert _count(s, DailyPrice) == 9 * 10
        assert _count(s, TechnicalIndicator) > 0


def test_full_backfill_resumes_only_missing_and_skips_done_months(engine, fetcher, router):
    router.fail["/fund/T86"] = 500
    out = FullBackfill(engine, START, END, fetcher, sleep=lambda s: None, revenue=False).run(PHASES)
    assert out["status"] == "DONE_WITH_GAPS" and out["months_by_status"] == {"PARTIAL": 1}
    assert out["missing_day_counts"] == {"TWSE_institutional": 9}
    del router.fail["/fund/T86"]
    router.calls.clear()
    out = FullBackfill(engine, START, END, fetcher, sleep=lambda s: None, revenue=False).run(("daily",))
    assert out["status"] == "DONE" and out["missing_day_counts"] == {}
    fetched = {c.split("?")[0].rsplit("/", 1)[-1] for c in router.calls}
    assert fetched == {"T86"}  # quotes / margin / valuation already stored -> not fetched again
    router.calls.clear()
    FullBackfill(engine, START, END, fetcher, sleep=lambda s: None, revenue=False).run(("daily",))
    assert router.calls == []  # month DONE -> skipped entirely
    with session_scope(engine) as s:
        assert _count(s, InstitutionalTrade) > 0


def test_interrupted_run_is_marked_and_resumes(engine, fetcher, router):
    calls = {"n": 0}
    real = router.__call__

    def flaky(request):
        calls["n"] += 1
        if calls["n"] == 12:  # stop in the middle of the window (like `docker stop`)
            raise KeyboardInterrupt
        return real(request)
    f = Fetcher(engine, transport=httpx.MockTransport(flaky), sleep=lambda s: None)
    try:
        FullBackfill(engine, START, END, f, sleep=lambda s: None, revenue=False).run(PHASES)
        raise AssertionError("expected interruption")
    except KeyboardInterrupt:
        pass
    st = status(engine)
    assert st["status"] == "INTERRUPTED" and 0 < st["days_done"] < 9
    out = FullBackfill(engine, START, END, fetcher, sleep=lambda s: None, revenue=False).run(PHASES)
    assert out["status"] == "DONE" and out["months_by_status"] == {"DONE": 1} and out["days_done"] == 9
    with session_scope(engine) as s:
        assert _count(s, DailyPrice) == 9 * 10


def test_security_notice_pauses_and_retries_the_day(engine, router):
    blocked = {"left": 2}
    real = router.__call__

    def waf(request):
        if request.url.path.endswith("/fund/T86") and blocked["left"]:
            blocked["left"] -= 1
            return httpx.Response(307, content=b"<html>THE PAGE CANNOT BE ACCESSED! FOR SECURITY REASONS</html>")
        return real(request)
    sleeps = []
    f = Fetcher(engine, transport=httpx.MockTransport(waf), sleep=lambda s: None)
    out = FullBackfill(engine, START, START, f, sleep=sleeps.append, revenue=False).run(("daily",))
    f.close()
    assert out["status"] == "DONE" and out["blocked_events"] == 2
    assert sleeps == [900.0, 1800.0]  # cooldown grows with each attempt


def test_raw_mirror_writes_gzip_copy(engine, router, tmp_path):
    st = Settings(raw_archive_dir=str(tmp_path), http_min_interval_seconds=0)
    f = Fetcher(engine, settings=st, transport=httpx.MockTransport(router), sleep=lambda s: None)
    res = f.get("twse", "twse_quotes", "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX",
                {"date": "20240102", "type": "ALLBUT0999", "response": "json"}, START)
    f.close()
    files = list((tmp_path / "twse" / "twse_quotes" / "2024").iterdir())
    assert len(files) == 1 and files[0].name.startswith("20240102_") and f"_{res.raw_id}_" in files[0].name
    body = gzip.decompress(files[0].read_bytes())
    assert body == res.content and hashlib.sha256(body).hexdigest()[:12] in files[0].name


def test_health_check_classifies_verdicts(router, synthetic_market, tmp_path):
    def handler(request):
        host = request.url.host
        if host == "isin.twse.com.tw":
            return httpx.Response(200, content=b"<html>FOR SECURITY REASONS, THIS PAGE CAN NOT BE ACCESSED</html>")
        if host == "mopsov.twse.com.tw":
            return httpx.Response(200, content=b"<html><body>no table</body></html>")
        if host == "mis.twse.com.tw":
            raise httpx.ConnectError("refused")
        return router(request)
    rep = run_health_check(str(tmp_path), transport=httpx.MockTransport(handler), sleep=lambda s: None)
    r = rep["results"]
    assert r["isin_twse"]["verdict"] == "BLOCKED" and r["isin_twse"]["security_notice"]
    assert r["revenue_history_twse_201901"]["verdict"] == "EMPTY"
    assert r["mis_intraday"]["verdict"] == "UNREACHABLE"
    assert r["companies_twse"]["verdict"] == "HTTP_ERROR"  # router has no OpenAPI route -> 404
    assert all(v["sha256"] for v in r.values() if "status" in v)
    assert rep["all_ok"] is False and os.path.exists(rep["saved_to"])
    assert rep["groups"]["ISIN"]["ok"] == 0


def test_integrity_report_counts_and_gaps(engine, fetcher, router, tmp_path):
    router.fail["/marginTrading/MI_MARGN"] = 500
    FullBackfill(engine, START, END, fetcher, sleep=lambda s: None, revenue=False).run(PHASES)
    rep = run_integrity_check(engine, START, END, str(tmp_path), sha_sample=50)
    assert rep["table_rows"]["daily_prices"] == 90
    by = {(r["market"], r["year"]): r for r in rep["quotes_by_year"]}
    assert by[("TWSE", 2024)]["days_missing_quotes"] == 0 and by[("TWSE", 2024)]["known_trading_days"] == 9
    gaps = {(ds, r["market"]): r["days_missing"] for ds, rows in rep["secondary_gaps"].items() for r in rows}
    assert gaps == {("margin", "TWSE"): 9}
    assert rep["raw_payloads"]["sha256_sample"]["mismatch"] == 0
    assert len(rep["rerunnable_days"]) == 9 and rep["verdict"]["complete"] is False
    assert os.path.exists(rep["saved_to"]) and os.path.exists(rep["saved_to"].replace(".json", ".md"))


def _revenue_html(n: int) -> bytes:
    """SYNTHETIC TEST FIXTURE in the t21sc03 layout (Big5 page)."""
    rows = "".join(f"<tr><td>{9000 + i}</td><td>測試{i}</td><td>1,000</td><td>900</td><td>800</td><td>11.11</td>"
                   f"<td>25.00</td><td>1,000</td><td>800</td><td>25.00</td><td>-</td></tr>" for i in range(n))
    return f"<html><!-- SYNTHETIC --><table>{rows}</table></html>".encode("cp950")


def test_revenue_phase_marks_months_and_retries_few(engine, router):
    pages = {"sii": 120, "otc": 3}

    def handler(request):
        if request.url.host == "mopsov.twse.com.tw":
            seg = request.url.path.split("/")[3]
            return httpx.Response(200, content=_revenue_html(pages[seg]))
        return router(request)
    f = Fetcher(engine, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    out = FullBackfill(engine, START, date(2024, 2, 29), f, sleep=lambda s: None).run(("revenue",))
    assert out["revenue_months_done"] == 2  # TWSE Jan + Feb
    assert out["revenue_months_not_done"] == {"TPEX:2024-01": "FEW (3)", "TPEX:2024-02": "FEW (3)"}
    assert out["status"] == "DONE_WITH_GAPS"
    pages["otc"] = 150
    out = FullBackfill(engine, START, date(2024, 2, 29), f, sleep=lambda s: None).run(("revenue",))
    f.close()
    assert out["revenue_months_done"] == 4 and out["status"] == "DONE"


def test_latest_published_revenue_month():
    assert latest_published_revenue_month(date(2026, 10, 31)) == (2026, 9)
    assert latest_published_revenue_month(date(2026, 10, 10)) == (2026, 8)
    assert latest_published_revenue_month(date(2026, 1, 5)) == (2025, 11)
