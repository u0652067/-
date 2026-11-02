"""Database integrity / completeness report (`python -m aqt.cli integrity-check`).

Run before a full backfill (baseline, together with a backup) and after it. Reports:
  * row counts of every data table;
  * per market and year: trading days with quotes vs. the union of known trading days;
  * trading days whose quotes exist but a secondary dataset (institutional / margin / valuation) is absent;
  * monthly-revenue months with no (or too few) rows per market;
  * raw payloads that failed to parse, non-200 archives, sha256 mismatches in a sample;
  * data-quality issue counts; latest status of each source;
  * daily_update days whose latest run is FAILED / PARTIAL (re-runnable with `backfill-full`).
"""
import gzip
import hashlib
import json
import os
from datetime import date, datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Engine

from aqt.config import get_settings
from aqt.data.backfill import latest_published_revenue_month, status as backfill_status

TABLES = ("securities", "trading_calendar", "raw_payloads", "daily_prices", "market_indices", "corporate_actions",
          "technical_indicators", "institutional_trades", "margin_trades", "valuations", "monthly_revenues",
          "financial_statements", "trading_restrictions", "data_quality_issues", "data_source_status", "job_runs",
          "system_logs", "notifications")
SECONDARY = {"institutional": "institutional_trades", "margin": "margin_trades", "valuation": "valuations"}


def _rows(c, sql: str, **kw) -> list[dict]:
    return [dict(r._mapping) for r in c.execute(text(sql), kw)]


def run_integrity_check(engine: Engine, start: date = date(2019, 1, 1), end: date | None = None,
                        out_dir: str | None = None, sha_sample: int = 200) -> dict:
    end = end or date.today()
    rep: dict = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "window": f"{start}..{end}"}
    with engine.connect() as c:
        rep["table_rows"] = {t: c.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in TABLES}
        rep["securities"] = _rows(c, "SELECT market, security_type, status, count(*) n FROM securities "
                                     "GROUP BY 1,2,3 ORDER BY 1,2,3")
        # union of days on which either market traded (observed data or holiday schedule)
        known = """
          WITH k AS (
            SELECT DISTINCT p.trade_date d FROM daily_prices p WHERE p.trade_date BETWEEN :s AND :e
            UNION SELECT cal_date FROM trading_calendar WHERE is_trading_day AND cal_date BETWEEN :s AND :e)
        """
        rep["quotes_by_year"] = _rows(c, known + """
          , m AS (SELECT DISTINCT s.market, p.trade_date d FROM daily_prices p JOIN securities s ON s.id=p.security_id
                  WHERE p.trade_date BETWEEN :s AND :e)
          SELECT mk.market, extract(year FROM k.d)::int AS year, count(*) AS known_trading_days,
                 count(m.d) AS days_with_quotes, count(*) - count(m.d) AS days_missing_quotes
          FROM k CROSS JOIN (VALUES ('TWSE'), ('TPEX')) mk(market)
          LEFT JOIN m ON m.d = k.d AND m.market = mk.market
          GROUP BY 1, 2 ORDER BY 1, 2""", s=start, e=end)
        rep["missing_quote_days"] = _rows(c, known + """
          , m AS (SELECT DISTINCT s.market, p.trade_date d FROM daily_prices p JOIN securities s ON s.id=p.security_id
                  WHERE p.trade_date BETWEEN :s AND :e)
          SELECT mk.market, k.d::text AS day FROM k CROSS JOIN (VALUES ('TWSE'), ('TPEX')) mk(market)
          LEFT JOIN m ON m.d = k.d AND m.market = mk.market WHERE m.d IS NULL ORDER BY 1, 2""", s=start, e=end)
        rep["unknown_weekdays"] = [r["day"] for r in _rows(c, """
          SELECT g::date::text AS day FROM generate_series(CAST(:s AS date), CAST(:e AS date), '1 day') g
          WHERE extract(isodow FROM g) < 6
            AND NOT EXISTS (SELECT 1 FROM trading_calendar t WHERE t.cal_date = g::date)
            AND NOT EXISTS (SELECT 1 FROM daily_prices p WHERE p.trade_date = g::date)
          ORDER BY 1""", s=start, e=min(end, date.today()))]
        gaps = {}
        for ds, table in SECONDARY.items():
            gaps[ds] = _rows(c, f"""
              WITH q AS (SELECT DISTINCT s.market, p.trade_date d FROM daily_prices p
                         JOIN securities s ON s.id=p.security_id WHERE p.trade_date BETWEEN :s AND :e),
                   x AS (SELECT DISTINCT s.market, t.trade_date d FROM {table} t
                         JOIN securities s ON s.id=t.security_id WHERE t.trade_date BETWEEN :s AND :e)
              SELECT q.market, count(*) AS days_missing, min(q.d)::text AS first, max(q.d)::text AS last,
                     (array_agg(q.d::text ORDER BY q.d))[1:30] AS sample
              FROM q LEFT JOIN x ON x.market=q.market AND x.d=q.d WHERE x.d IS NULL GROUP BY 1""", s=start, e=end)
        rep["secondary_gaps"] = gaps
        ly, lm = latest_published_revenue_month(date.today())
        rev_end = min(date(ly, lm, 1), end)
        rep["revenue_months"] = _rows(c, """
          WITH months AS (SELECT g::date m FROM generate_series(date_trunc('month', CAST(:s AS date)),
                                                                 CAST(:e AS date), '1 month') g),
               r AS (SELECT s.market, r.period m, count(*) n FROM monthly_revenues r
                     JOIN securities s ON s.id=r.security_id GROUP BY 1, 2)
          SELECT mk.market, count(*) AS months_expected, count(*) FILTER (WHERE coalesce(r.n,0) >= 100) AS months_ok,
                 (array_agg(to_char(months.m,'YYYY-MM') ORDER BY months.m)
                    FILTER (WHERE coalesce(r.n,0) < 100))[1:200] AS months_missing_or_few
          FROM months CROSS JOIN (VALUES ('TWSE'), ('TPEX')) mk(market)
          LEFT JOIN r ON r.m = months.m AND r.market = mk.market GROUP BY 1""", s=start, e=rev_end)
        rep["raw_payloads"] = {
            "by_parse_status": _rows(c, "SELECT source, dataset, parse_status, count(*) n FROM raw_payloads "
                                        "GROUP BY 1,2,3 ORDER BY 1,2,3"),
            "non_200": c.execute(text("SELECT count(*) FROM raw_payloads WHERE http_status <> 200")).scalar(),
            "parse_errors_sample": _rows(c, "SELECT id, dataset, data_date::text, left(parse_error, 200) err "
                                            "FROM raw_payloads WHERE parse_status='ERROR' ORDER BY id DESC LIMIT 20"),
        }
        bad = 0
        sample = c.execute(text("SELECT id, sha256, content_gz FROM raw_payloads ORDER BY random() LIMIT :n"),
                           {"n": sha_sample}).all()
        for rid, sha, gz in sample:
            if hashlib.sha256(gzip.decompress(gz)).hexdigest() != sha:
                bad += 1
        rep["raw_payloads"]["sha256_sample"] = {"checked": len(sample), "mismatch": bad}
        rep["quality_issues"] = _rows(c, "SELECT check_name, severity, count(*) n FROM data_quality_issues "
                                         "GROUP BY 1,2 ORDER BY 3 DESC")
        rep["source_status"] = _rows(c, "SELECT source, dataset, status, consecutive_failures, "
                                        "latest_data_date::text, left(last_error,160) last_error "
                                        "FROM data_source_status ORDER BY 1,2")
        rep["rerunnable_days"] = _rows(c, """
          SELECT DISTINCT ON (params->>'date') params->>'date' AS day, status,
                 left(coalesce(result->>'warnings', error, ''), 200) AS why
          FROM job_runs WHERE job_name='daily_update' AND params->>'date' BETWEEN :s AND :e
          ORDER BY params->>'date', started_at DESC""", s=str(start), e=str(end))
        rep["rerunnable_days"] = [r for r in rep["rerunnable_days"] if r["status"] in ("FAILED", "PARTIAL", "RUNNING")]
        rep["safe_mode"] = c.execute(text("SELECT value FROM system_state WHERE key='safe_mode'")).scalar()
        rep["corporate_actions_by_type"] = _rows(c, "SELECT s.market, a.action_type, count(*) n FROM corporate_actions a "
                                                    "JOIN securities s ON s.id=a.security_id GROUP BY 1,2 ORDER BY 1,2")
        rep["restrictions_by_type"] = _rows(c, "SELECT s.market, r.restriction_type, count(*) n, "
                                               "count(DISTINCT r.security_id) securities, min(r.start_date)::text first, "
                                               "max(coalesce(r.end_date, r.start_date))::text last FROM trading_restrictions r "
                                               "JOIN securities s ON s.id=r.security_id GROUP BY 1,2 ORDER BY 1,2")
        rep["financials_pit"] = _rows(c, "SELECT fiscal_year, published_basis, pit_reliable, count(*) n "
                                         "FROM financial_statements GROUP BY 1,2,3 ORDER BY 1,2,3")
        rep["quality_review"] = _rows(c, "SELECT check_name, severity, coalesce(classification,'UNCLASSIFIED') "
                                         "classification, blocks_backtest, count(*) n FROM data_quality_issues "
                                         "GROUP BY 1,2,3,4 ORDER BY 1,2,3")
    rep["backfill"] = backfill_status(engine)
    rep["verdict"] = _verdict(rep)
    out_dir = out_dir or (os.path.join(get_settings().report_dir, "integrity") if get_settings().report_dir else None)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        stem = os.path.join(out_dir, f"integrity_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}")
        with open(stem + ".json", "w", encoding="utf-8") as fh:
            json.dump(rep, fh, ensure_ascii=False, indent=1, default=str)
        with open(stem + ".md", "w", encoding="utf-8") as fh:
            fh.write(to_markdown(rep))
        rep["saved_to"] = stem + ".json"
    return rep


def _verdict(rep: dict) -> dict:
    """Coverage (is every expected row there?) and quality (can what is there be trusted?) are reported
    separately: complete coverage with unexplained anomalies is not backtest-ready."""
    gaps = []
    for r in rep["quotes_by_year"]:
        if r["days_missing_quotes"]:
            gaps.append(f"{r['market']} {r['year']}: {r['days_missing_quotes']} trading days without quotes")
    for ds, rows in rep["secondary_gaps"].items():
        for r in rows:
            gaps.append(f"{r['market']} {ds}: {r['days_missing']} days missing ({r['first']}..{r['last']})")
    for r in rep["revenue_months"]:
        if r["months_ok"] < r["months_expected"]:
            gaps.append(f"{r['market']} revenue: {r['months_expected'] - r['months_ok']} months missing/few")
    if rep["unknown_weekdays"]:
        gaps.append(f"{len(rep['unknown_weekdays'])} weekdays never resolved (no data, not known holiday)")
    if rep["rerunnable_days"]:
        gaps.append(f"{len(rep['rerunnable_days'])} daily_update days FAILED/PARTIAL (re-runnable)")
    if rep["raw_payloads"]["sha256_sample"]["mismatch"]:
        gaps.append("raw payload sha256 mismatch")

    q = rep.get("quality_review", [])
    unclassified = sum(r["n"] for r in q if r["classification"] == "UNCLASSIFIED" and r["severity"] != "INFO")
    errors = sum(r["n"] for r in q if r["severity"] == "ERROR")
    blocking = sum(r["n"] for r in q if r["blocks_backtest"])
    notes = []
    if unclassified:
        notes.append(f"{unclassified} WARN/ERROR issues not yet classified")
    if errors:
        notes.append(f"{errors} ERROR issues (the security-day is excluded from the universe)")
    if blocking:
        notes.append(f"{blocking} issues block the security from backtests for "
                     "tradability.QUALITY_BLOCK_DAYS calendar days")
    fin_unreliable = sum(r["n"] for r in rep.get("financials_pit", []) if not r["pit_reliable"])
    if fin_unreliable:
        notes.append(f"{fin_unreliable} financial statements without reliable publication time "
                     "(never returned by fundamentals.latest_statements_as_of)")
    status = "CLEAN" if not (errors or unclassified or blocking) else (
        "UNREVIEWED" if unclassified else "REVIEWED_WITH_EXCLUSIONS")
    return {
        "coverage": {"complete": not gaps, "gaps": gaps},
        "quality": {"status": status, "errors": errors, "unclassified": unclassified,
                    "blocking_backtest": blocking, "notes": notes},
        # usable only when coverage is complete and every anomaly is classified; blocked items are kept
        # out by tradability.blocked_securities, so their presence does not make the dataset unusable
        "backtest_ready": not gaps and unclassified == 0,
    }


def to_markdown(rep: dict) -> str:
    v = rep["verdict"]
    L = [f"# Integrity report {rep['generated_at']}", f"Window: {rep['window']}", "",
         "| dimension | result |", "|---|---|",
         f"| 覆蓋完整度 coverage | {'COMPLETE' if v['coverage']['complete'] else 'GAPS'} |",
         f"| 資料品質狀態 quality | {v['quality']['status']} (ERROR {v['quality']['errors']}, "
         f"unclassified {v['quality']['unclassified']}, blocking {v['quality']['blocking_backtest']}) |",
         f"| 可用於回測 backtest_ready | {v['backtest_ready']} |", "", "### Coverage gaps"]
    L += [f"- {p}" for p in v["coverage"]["gaps"]] or ["- none"]
    L += ["", "### Quality notes"]
    L += [f"- {p}" for p in v["quality"]["notes"]] or ["- none"]
    L += ["", "## Data-quality review", "| check | severity | classification | blocks backtest | n |",
          "|---|---|---|---|---:|"]
    L += [f"| {r['check_name']} | {r['severity']} | {r['classification']} | {r['blocks_backtest']} | {r['n']:,} |"
          for r in rep.get("quality_review", [])]
    L += ["", "## Corporate actions", "| market | type | n |", "|---|---|---:|"]
    L += [f"| {r['market']} | {r['action_type']} | {r['n']:,} |" for r in rep.get("corporate_actions_by_type", [])]
    L += ["", "## Trading restrictions", "| market | type | rows | securities | first | last |",
          "|---|---|---:|---:|---|---|"]
    L += [f"| {r['market']} | {r['restriction_type']} | {r['n']:,} | {r['securities']} | {r['first']} | {r['last']} |"
          for r in rep.get("restrictions_by_type", [])]
    L += ["", "## Financial statements (point-in-time)", "| fiscal year | basis | pit_reliable | n |",
          "|---|---|---|---:|"]
    L += [f"| {r['fiscal_year']} | {r['published_basis']} | {r['pit_reliable']} | {r['n']:,} |"
          for r in rep.get("financials_pit", [])]
    L += ["", "## Table rows", "| table | rows |", "|---|---:|"]
    L += [f"| {t} | {n:,} |" for t, n in rep["table_rows"].items()]
    L += ["", "## Quotes by market / year", "| market | year | known trading days | with quotes | missing |",
          "|---|---|---:|---:|---:|"]
    L += [f"| {r['market']} | {r['year']} | {r['known_trading_days']} | {r['days_with_quotes']} | "
          f"{r['days_missing_quotes']} |" for r in rep["quotes_by_year"]]
    L += ["", "## Secondary dataset gaps (quote day without the dataset)", "| dataset | market | days | first | last |",
          "|---|---|---:|---|---|"]
    for ds, rows in rep["secondary_gaps"].items():
        L += [f"| {ds} | {r['market']} | {r['days_missing']} | {r['first']} | {r['last']} |" for r in rows]
    L += ["", "## Monthly revenue", "| market | expected | ok | missing/few |", "|---|---:|---:|---|"]
    L += [f"| {r['market']} | {r['months_expected']} | {r['months_ok']} | "
          f"{', '.join(r['months_missing_or_few'] or [])[:300]} |" for r in rep["revenue_months"]]
    L += ["", "## Source status", "| source | dataset | status | fails | latest | last error |", "|---|---|---|---:|---|---|"]
    L += [f"| {r['source']} | {r['dataset']} | {r['status']} | {r['consecutive_failures']} | {r['latest_data_date']} | "
          f"{(r['last_error'] or '').replace('|', '/')} |" for r in rep["source_status"]]
    L += ["", "## Re-runnable days", f"{len(rep['rerunnable_days'])} days; rerun with "
          "`scripts\\backfill.ps1 start` (resumes, only missing datasets are fetched)."]
    L += [f"- {r['day']} {r['status']}: {r['why']}" for r in rep["rerunnable_days"][:50]]
    L += ["", "## Backfill progress", "```", json.dumps(rep["backfill"], ensure_ascii=False, indent=1), "```"]
    return "\n".join(L) + "\n"
