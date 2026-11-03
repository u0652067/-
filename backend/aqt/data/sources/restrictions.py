"""Official trading-restriction lists (G6): stocks that a strategy must not trade.

| restriction                                  | TWSE                                        | TPEx                                   |
|----------------------------------------------|---------------------------------------------|----------------------------------------|
| disposition (處置), with start/end           | /rwd/zh/announcement/punish (range)         | /www/zh-tw/bulletin/disposal (range)   |
| altered trading / full delivery (變更交易)   | /rwd/zh/fullDelivery/TWT85U?date= (daily)   | /www/zh-tw/afterTrading/chtm?date= (daily: 變更交易, 分盤, 管理股票, 停止交易) |
| trading halt (暫停交易)                      | /rwd/zh/afterTrading/TWTAWU (range)         | chtm 停止交易 column (daily)           |

Point in time: dispositions are announced after the close of `announced_date` and take effect the
next trading day; daily lists describe the state on their own date (published before the open).
"""
import re
from datetime import date, timedelta

from aqt.data.parsing import find_col, roc_to_date, strip_html
from aqt.data.records import ParseResult, RestrictionRecord

TWSE = "https://www.twse.com.tw/rwd/zh"
TPEX = "https://www.tpex.org.tw/www/zh-tw"
_RANGE = re.compile(r"(\d{2,3}/\d{1,2}/\d{1,2})\s*[～~\-至]\s*(\d{2,3}/\d{1,2}/\d{1,2})")
_YES = ("Y", "Ｙ")


def _rows(payload):
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("tables"), list):
        return [([strip_html(f) for f in t.get("fields", [])], t.get("data") or [])
                for t in payload["tables"] if isinstance(t, dict)]
    if "fields" in payload:
        return [([strip_html(f) for f in payload["fields"]], payload.get("data") or [])]
    return []


def _code(cell) -> str:
    # TPEx wraps codes like "6535(../../mainboard/listed/company-detail.html?code=6535)"
    s = strip_html(str(cell)).strip()
    return re.split(r"[(\s]", s, maxsplit=1)[0] if s else ""


def _name(cell) -> str:
    return re.split(r"\(", strip_html(str(cell)).strip(), maxsplit=1)[0].strip()


# ------------------------------------------------------------------ requests
def twse_disposition_request(start: date, end: date):
    return f"{TWSE}/announcement/punish", {"startDate": start.strftime("%Y%m%d"), "endDate": end.strftime("%Y%m%d"),
                                           "response": "json"}


def tpex_disposition_request(start: date, end: date):
    return f"{TPEX}/bulletin/disposal", {"startDate": start.strftime("%Y/%m/%d"), "endDate": end.strftime("%Y/%m/%d"),
                                         "response": "json"}


def twse_full_delivery_request(d: date):
    return f"{TWSE}/fullDelivery/TWT85U", {"date": d.strftime("%Y%m%d"), "response": "json"}


def tpex_altered_request(d: date):
    return f"{TPEX}/afterTrading/chtm", {"date": d.strftime("%Y/%m/%d"), "response": "json"}


def twse_halt_request(start: date, end: date):
    return f"{TWSE}/afterTrading/TWTAWU", {"startDate": start.strftime("%Y%m%d"), "endDate": end.strftime("%Y%m%d"),
                                           "response": "json"}


# ------------------------------------------------------------------ parsers
def parse_disposition(payload: dict) -> ParseResult:
    """TWSE punish / TPEx disposal: 公布日期, 證券代號, 證券名稱, 處置起迄(訖)時間, 處置措施 ..."""
    res = ParseResult()
    for f, data in _rows(payload):
        iann, ic, iname = find_col(f, "公布日期"), find_col(f, "證券代號"), find_col(f, "證券名稱")
        iper, imeasure = find_col(f, "處置起"), find_col(f, "處置措施")
        if None in (iann, ic, iper):
            continue
        for r in data:
            code = _code(r[ic])
            m = _RANGE.search(strip_html(str(r[iper])))
            if not code or not m:
                continue  # TPEx inserts "本日無處置資料" placeholder rows
            res.records.append(RestrictionRecord(
                code=code, name=_name(r[iname]) if iname is not None else "", restriction_type="DISPOSITION",
                start_date=roc_to_date(m.group(1)), end_date=roc_to_date(m.group(2)),
                announced_date=roc_to_date(strip_html(str(r[iann]))),
                detail={"measure": strip_html(str(r[imeasure])) if imeasure is not None else None}))
    return res


def parse_twse_full_delivery(payload: dict, d: date) -> ParseResult:
    """TWT85U for date d: every listed security under altered trading (全額交割) that day; '**' = periodic auction."""
    res = ParseResult(is_trading_day=None)
    for f, data in _rows(payload):
        ic, iname, ipa = find_col(f, "證券代號"), find_col(f, "證券名稱"), find_col(f, "分盤")
        if ic is None:
            continue
        for r in data:
            res.records.append(RestrictionRecord(
                code=_code(r[ic]), name=_name(r[iname]) if iname is not None else "", restriction_type="ALTERED_TRADING",
                start_date=d, end_date=d, announced_date=d,
                detail={"periodic_auction": ipa is not None and "*" in str(r[ipa])}))
    return res


def parse_tpex_altered(payload: dict, d: date) -> ParseResult:
    """chtm for date d: 變更交易 / 分盤交易 / 屬管理股票 / 停止交易 flags (Ｙ)."""
    res = ParseResult()
    flags = (("變更交易", "ALTERED_TRADING"), ("分盤交易", "PERIODIC_AUCTION"), ("管理股票", "MANAGED"),
             ("停止交易", "STOP_TRADING"))
    for f, data in _rows(payload):
        ic, iname = find_col(f, "證券代號"), find_col(f, "證券名稱")
        if ic is None:
            continue
        cols = [(find_col(f, kw, exclude=("時間",)), kind) for kw, kind in flags]
        for r in data:
            for i, kind in cols:
                if i is not None and strip_html(str(r[i])).strip() in _YES:
                    res.records.append(RestrictionRecord(
                        code=_code(r[ic]), name=_name(r[iname]) if iname is not None else "", restriction_type=kind,
                        start_date=d, end_date=d, announced_date=d))
    return res


def parse_twse_halt(payload: dict) -> ParseResult:
    """TWTAWU: 證券代號, 證券名稱, 暫停交易日期, 暫停交易時間, 恢復交易日期, 恢復交易時間.
    Halted from the halt date until the day before resumption (open-ended if not resumed yet)."""
    res = ParseResult()
    for f, data in _rows(payload):
        ic, iname = find_col(f, "證券代號"), find_col(f, "證券名稱")
        ih, ir = find_col(f, "暫停交易日期"), find_col(f, "恢復交易日期")
        if None in (ic, ih):
            continue
        for r in data:
            try:
                start = roc_to_date(strip_html(str(r[ih])))
            except ValueError:
                continue
            end = None
            if ir is not None:
                try:
                    end = roc_to_date(strip_html(str(r[ir]))) - timedelta(days=1)
                except ValueError:
                    end = None
            res.records.append(RestrictionRecord(
                code=_code(r[ic]), name=_name(r[iname]) if iname is not None else "", restriction_type="SUSPENDED",
                start_date=start, end_date=max(end, start) if end else None, announced_date=start))
    return res
