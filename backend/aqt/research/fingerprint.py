"""Immutable fingerprint of the exact data a backtest can read.

For every table the engine, the DataView or the universe reads, the rows of the period
[start - LOOKBACK, end] are serialised in a fixed order and hashed per calendar month in PostgreSQL
(sha256). The result is a manifest {table: {YYYY-MM: sha256}} plus one overall sha256 over the
manifest. Changing any value of any row in the period — one price, one limit, one restriction date,
one quality classification — changes the fingerprint; counts or max ids alone would not.
"""
import hashlib
import json
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

LOOKBACK_DAYS = 400  # universe history (130 d) + indicator windows + walk-forward warm-up, generously

# table -> (date column for the period filter / month bucket, ordered serialisation of the row)
_TABLES = {
    "daily_prices": ("trade_date", "security_id, trade_date",
                     "concat_ws('|', security_id, trade_date, open, high, low, close, change, volume, turnover, "
                     "transactions, prev_close, limit_up, limit_down, adj_factor, is_suspended, available_at)"),
    "corporate_actions": ("ex_date", "security_id, ex_date, action_type",
                          "concat_ws('|', id, security_id, ex_date, action_type, prev_close, ref_price, cash_dividend, "
                          "stock_dividend_ratio, available_at)"),
    "trading_restrictions": ("start_date", "security_id, restriction_type, start_date, source",
                             "concat_ws('|', security_id, restriction_type, start_date, end_date, announced_date, "
                             "source, available_at)"),
    "data_quality_issues": ("trade_date", "id",
                            "concat_ws('|', id, check_name, severity, security_id, trade_date, classification, "
                            "blocks_backtest)"),
    "market_indices": ("trade_date", "index_code, trade_date",
                       "concat_ws('|', index_code, trade_date, close, available_at)"),
}


def data_fingerprint(session: Session, start: date, end: date) -> dict:
    lo = start - timedelta(days=LOOKBACK_DAYS)
    manifest: dict[str, dict[str, str]] = {}
    for table, (dcol, order, row) in _TABLES.items():
        rows = session.execute(text(
            f"SELECT to_char({dcol}, 'YYYY-MM') AS m, count(*) AS n, "
            f"encode(sha256(convert_to(string_agg({row}, E'\\n' ORDER BY {order}), 'UTF8')), 'hex') AS h "
            f"FROM {table} WHERE {dcol} BETWEEN :lo AND :hi GROUP BY 1 ORDER BY 1"), {"lo": lo, "hi": end}).all()
        manifest[table] = {m: f"{n}:{h}" for m, n, h in rows}
    # restrictions that started before the window but are still open or overlap it
    rows = session.execute(text(
        "SELECT count(*), encode(sha256(convert_to(coalesce(string_agg(concat_ws('|', security_id, restriction_type, "
        "start_date, end_date, announced_date, source, available_at), E'\\n' ORDER BY security_id, restriction_type, "
        "start_date, source), ''), 'UTF8')), 'hex') FROM trading_restrictions "
        "WHERE start_date < :lo AND (end_date IS NULL OR end_date >= :lo)"), {"lo": lo}).one()
    manifest["trading_restrictions_overlapping"] = {"before": f"{rows[0]}:{rows[1]}"}
    sec = session.execute(text(
        "SELECT count(*), encode(sha256(convert_to(string_agg(concat_ws('|', id, code, market, security_type, "
        "status, listed_date, delisted_date), E'\\n' ORDER BY id), 'UTF8')), 'hex') FROM securities")).one()
    manifest["securities"] = {"all": f"{sec[0]}:{sec[1]}"}
    overall = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    return {"period": [str(lo), str(end)], "lookback_days": LOOKBACK_DAYS, "sha256": overall, "manifest": manifest}
