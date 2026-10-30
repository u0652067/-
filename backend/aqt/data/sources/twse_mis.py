"""Intraday snapshots from the TWSE MIS public quote page (基本市況報導網站).

IMPORTANT LIMITATIONS (shown in the UI):
* This is a public display service, not a licensed real-time market-data feed.
  Snapshots are delayed and may be throttled; `delay_seconds` is measured per quote
  (fetch time minus the exchange timestamp) and is persisted with every row.
* Redistribution of real-time data requires a TWSE information-vendor licence.
  The system therefore never labels these quotes "real-time".
"""
from datetime import datetime, timezone

from aqt.data.parsing import to_decimal, to_int
from aqt.data.records import IntradayRecord, ParseResult

SOURCE = "twse_mis"
URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"


def request(codes: list[tuple[str, str]]) -> tuple[str, dict]:
    """codes: [(code, 'TWSE'|'TPEX')] -> ex_ch=tse_2330.tw|otc_6488.tw"""
    ex = "|".join(f"{'tse' if m == 'TWSE' else 'otc'}_{c}.tw" for c, m in codes)
    return URL, {"ex_ch": ex, "json": "1", "delay": "0"}


def _first(levels: str | None):
    if not levels:
        return None
    return to_decimal(levels.strip("_").split("_")[0])


def parse(payload: dict, fetched_at: datetime | None = None) -> ParseResult:
    fetched_at = fetched_at or datetime.now(timezone.utc)
    res = ParseResult()
    for q in payload.get("msgArray", []) or []:
        tlong = q.get("tlong")
        if not tlong:
            continue
        qt = datetime.fromtimestamp(int(tlong) / 1000, tz=timezone.utc)
        res.records.append(IntradayRecord(
            code=q.get("c", ""), market="TWSE" if q.get("ex") == "tse" else "TPEX", quote_time=qt,
            price=to_decimal(q.get("z")), cum_volume=to_int(q.get("v")),
            best_bid=_first(q.get("b")), best_ask=_first(q.get("a")),
            delay_seconds=max(0.0, (fetched_at - qt).total_seconds()),
        ))
    return res
