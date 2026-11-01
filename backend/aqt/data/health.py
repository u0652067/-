"""Source health check for the local deployment host (`python -m aqt.cli health-check`).

Unlike `check-sources` (parser smoke test), this records *transport-level evidence* for every
official source: HTTP status, final URL after redirects, content type, size, latency, whether the
TWSE firewall notice page was served, whether the body has the expected format, and how many
records the production parser extracts. One request per probe, no retries — a refusal must show up
as a refusal, not be hidden by a retry. Requests to the same host are spaced like the fetcher.

Verdicts: OK | EMPTY (reachable, parsed 0 records) | BLOCKED (403/407/security notice) |
FORMAT_ERROR (reachable but body is not the expected JSON/HTML or the parser failed) |
UNREACHABLE (DNS / TCP / TLS / timeout) | HTTP_ERROR (other status).
"""
import hashlib
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx

from aqt.config import get_settings
from aqt.data.pit import TPE
from aqt.data.sources import companies, isin, mops, tpex, twse, twse_mis

HISTORY_PROBE = date(2019, 1, 2)  # first trading day of the backfill window


def _recent_weekday() -> date:
    d = datetime.now(TPE).date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _json(b: bytes):
    text = b.decode("utf-8-sig", errors="replace").strip()
    if not text or text[0] not in "[{":
        raise ValueError(f"expected JSON, got {text[:80]!r}")
    return json.loads(text)


def probes(d: date) -> list[tuple[str, str, str, dict | None, object]]:
    """(group, name, url, params, parse(bytes) -> ParseResult)."""
    h = HISTORY_PROBE
    return [
        ("TWSE", "twse_quotes", *twse.daily_quotes_request(d), lambda b: twse.parse_daily_quotes(_json(b), d)),
        ("TWSE", "twse_institutional", *twse.institutional_request(d), lambda b: twse.parse_institutional(_json(b), d)),
        ("TWSE", "twse_margin", *twse.margin_request(d), lambda b: twse.parse_margin(_json(b), d)),
        ("TWSE", "twse_valuation", *twse.valuation_request(d), lambda b: twse.parse_valuation(_json(b), d)),
        ("TWSE", "twse_ex_rights", *twse.corporate_actions_request(d - timedelta(days=30), d),
         lambda b: twse.parse_corporate_actions(_json(b))),
        ("TWSE", "twse_holidays", *twse.holidays_request(d.year), lambda b: twse.parse_holidays(_json(b))),
        ("TWSE", "twse_quotes_2019", *twse.daily_quotes_request(h), lambda b: twse.parse_daily_quotes(_json(b), h)),
        ("TWSE", "twse_institutional_2019", *twse.institutional_request(h),
         lambda b: twse.parse_institutional(_json(b), h)),
        ("TWSE", "twse_margin_2019", *twse.margin_request(h), lambda b: twse.parse_margin(_json(b), h)),
        ("TWSE", "twse_ex_rights_2019", *twse.corporate_actions_request(h, h + timedelta(days=90)),
         lambda b: twse.parse_corporate_actions(_json(b))),
        ("TPEx", "tpex_quotes", *tpex.daily_quotes_request(d), lambda b: tpex.parse_daily_quotes(_json(b), d)),
        ("TPEx", "tpex_institutional", *tpex.institutional_request(d), lambda b: tpex.parse_institutional(_json(b), d)),
        ("TPEx", "tpex_margin", *tpex.margin_request(d), lambda b: tpex.parse_margin(_json(b), d)),
        ("TPEx", "tpex_valuation", *tpex.valuation_request(d), lambda b: tpex.parse_valuation(_json(b), d)),
        ("TPEx", "tpex_quotes_2019", *tpex.daily_quotes_request(h), lambda b: tpex.parse_daily_quotes(_json(b), h)),
        ("TPEx", "tpex_institutional_2019", *tpex.institutional_request(h),
         lambda b: tpex.parse_institutional(_json(b), h)),
        ("OpenAPI", "companies_twse", companies.URL["TWSE"], None, lambda b: companies.parse(_json(b), "TWSE")),
        ("OpenAPI", "companies_tpex", companies.URL["TPEX"], None, lambda b: companies.parse(_json(b), "TPEX")),
        ("OpenAPI", "revenue_twse", mops.REVENUE_URL["TWSE"], None, lambda b: mops.parse_revenue_openapi(_json(b))),
        ("OpenAPI", "revenue_tpex", mops.REVENUE_URL["TPEX"], None, lambda b: mops.parse_revenue_openapi(_json(b))),
        ("OpenAPI", "income_twse", mops.INCOME_URL["TWSE"], None, lambda b: mops.parse_income_openapi(_json(b))),
        ("OpenAPI", "balance_tpex", mops.BALANCE_URL["TPEX"], None, lambda b: mops.parse_balance_openapi(_json(b))),
        ("ISIN", "isin_twse", *isin.request("TWSE"), lambda b: isin.parse(isin.decode(b), "TWSE")),
        ("ISIN", "isin_tpex", *isin.request("TPEX"), lambda b: isin.parse(isin.decode(b), "TPEX")),
        ("MOPS", "revenue_history_twse_201901", mops.historical_revenue_url("TWSE", 2019, 1), None,
         lambda b: mops.parse_revenue_history_html(isin.decode(b), 2019, 1)),
        ("MOPS", "revenue_history_tpex_201901", mops.historical_revenue_url("TPEX", 2019, 1), None,
         lambda b: mops.parse_revenue_history_html(isin.decode(b), 2019, 1)),
        ("MOPS", "revenue_history_twse_latest", mops.historical_revenue_url("TWSE", *_last_month(d)), None,
         lambda b: mops.parse_revenue_history_html(isin.decode(b), *_last_month(d))),
        ("MIS", "mis_intraday", *twse_mis.request([("2330", "TWSE")]), lambda b: twse_mis.parse(_json(b))),
    ]


def _last_month(d: date) -> tuple[int, int]:
    first = d.replace(day=1) - timedelta(days=1)
    return first.year, first.month


def _probe(client: httpx.Client, url: str, params, parse) -> dict:
    r: dict = {"url": url, "params": params}
    t0 = time.monotonic()
    try:
        resp = client.get(url, params=params)
    except httpx.HTTPError as e:
        r.update(verdict="UNREACHABLE", error=f"{type(e).__name__}: {e}"[:300],
                 latency_ms=int((time.monotonic() - t0) * 1000))
        return r
    body = resp.content
    r.update(status=resp.status_code, final_url=str(resp.url),
             redirects=[f"{x.status_code} {x.headers.get('location', '')}" for x in resp.history],
             content_type=resp.headers.get("content-type"), bytes=len(body),
             sha256=hashlib.sha256(body).hexdigest(), latency_ms=int((time.monotonic() - t0) * 1000),
             security_notice=b"FOR SECURITY REASONS" in body[:4000])
    if resp.status_code in (403, 407) or r["security_notice"]:
        r["verdict"], r["head"] = "BLOCKED", body[:200].decode("utf-8", errors="replace")
        return r
    if resp.status_code != 200:
        r["verdict"], r["head"] = "HTTP_ERROR", body[:200].decode("utf-8", errors="replace")
        return r
    try:
        parsed = parse(body)
    except Exception as e:
        r.update(verdict="FORMAT_ERROR", error=f"{type(e).__name__}: {e}"[:300],
                 head=body[:200].decode("utf-8", errors="replace"))
        return r
    r.update(records=len(parsed.records), note=parsed.note,
             sample=repr(parsed.records[0])[:200] if parsed.records else None,
             verdict="OK" if parsed.records else "EMPTY")
    return r


def run_health_check(out_dir: str | None = None, transport: httpx.BaseTransport | None = None,
                     sleep=time.sleep) -> dict:
    st = get_settings()
    d = _recent_weekday()
    started = datetime.now(timezone.utc)
    report = {"started_at": started.isoformat(), "probe_date": str(d), "history_probe_date": str(HISTORY_PROBE),
              "user_agent": st.http_user_agent, "results": {}, "groups": {}}
    last_hit: dict[str, float] = {}
    with httpx.Client(timeout=st.http_timeout_seconds, follow_redirects=True, transport=transport,
                      headers={"User-Agent": st.http_user_agent, "Accept": "application/json,text/html,*/*"}) as c:
        for group, name, url, params, parse in probes(d):
            host = urlparse(url).netloc
            if host in last_hit:
                wait = st.http_min_interval_seconds - (time.monotonic() - last_hit[host])
                if wait > 0:
                    sleep(wait)
            res = _probe(c, url, params, parse)
            last_hit[host] = time.monotonic()
            res["group"] = group
            report["results"][name] = res
    for name, res in report["results"].items():
        g = report["groups"].setdefault(res["group"], {"ok": 0, "total": 0, "problems": []})
        g["total"] += 1
        if res["verdict"] == "OK":
            g["ok"] += 1
        else:
            g["problems"].append(f"{name}: {res['verdict']}")
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["all_ok"] = all(r["verdict"] == "OK" for r in report["results"].values())
    out_dir = out_dir or (os.path.join(st.report_dir, "health") if st.report_dir else None)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"health_{started:%Y%m%dT%H%M%SZ}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1, default=str)
        report["saved_to"] = path
    return report
