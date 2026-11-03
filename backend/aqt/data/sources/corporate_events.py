"""Price-changing corporate events beyond TWSE ex-rights (TWT49U), all official range queries.

| event                         | TWSE                                   | TPEx                                   |
|-------------------------------|----------------------------------------|----------------------------------------|
| ex-rights / ex-dividend       | (twse.py TWT49U)                       | /www/zh-tw/bulletin/exDailyQ           |
| capital reduction (減資)      | /rwd/zh/reducation/TWTAUU              | /www/zh-tw/bulletin/revivt             |
| par-value change (變更面額)   | /rwd/zh/change/TWTB8U                  | /www/zh-tw/bulletin/pvChgRslt          |
| ETF split / reverse split     | /rwd/zh/split/TWTCAU                   | not connected (ETFs are not in the stock universe) |

Every event becomes a CorporateActionRecord whose `ex_date` is the first trading day at the new
basis and whose (prev_close, ref_price) pair is the exchange's own reference price — so the
backward adjustment factor is simply ref_price / prev_close for every event type.
"""
from datetime import date

from aqt.data.parsing import find_col, parse_any_date, roc_to_date, strip_html, to_decimal
from aqt.data.records import CorporateActionRecord, ParseResult

TWSE = "https://www.twse.com.tw/rwd/zh"
TPEX = "https://www.tpex.org.tw/www/zh-tw"
SOURCE_TWSE, SOURCE_TPEX = "twse", "tpex"


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _slash(d: date) -> str:
    return d.strftime("%Y/%m/%d")


def _rows(payload: dict) -> list[tuple[list[str], list]]:
    """(fields, data) of every table, for both the TWSE (`fields`/`data`) and TPEx (`tables`) layouts."""
    if not isinstance(payload, dict):
        return []
    out = []
    if isinstance(payload.get("tables"), list):
        for t in payload["tables"]:
            if isinstance(t, dict):
                out.append(([strip_html(f) for f in t.get("fields", [])], t.get("data") or []))
    elif "fields" in payload:
        out.append(([strip_html(f) for f in payload["fields"]], payload.get("data") or []))
    return out


def _date(s) -> date:
    s = strip_html(str(s)).strip()
    return parse_any_date(s) if s[:2] in ("19", "20") and len(s.replace("/", "")) == 8 else roc_to_date(s)


def _code(s) -> str:
    return strip_html(str(s)).replace("*", "").strip()


# ------------------------------------------------------------------ requests
def tpex_ex_rights_request(start: date, end: date):
    return f"{TPEX}/bulletin/exDailyQ", {"startDate": _slash(start), "endDate": _slash(end), "response": "json"}


def twse_capital_reduction_request(start: date, end: date):
    return f"{TWSE}/reducation/TWTAUU", {"startDate": _ymd(start), "endDate": _ymd(end), "response": "json"}


def tpex_capital_reduction_request(start: date, end: date):
    return f"{TPEX}/bulletin/revivt", {"startDate": _slash(start), "endDate": _slash(end), "response": "json"}


def twse_par_change_request(start: date, end: date):
    return f"{TWSE}/change/TWTB8U", {"startDate": _ymd(start), "endDate": _ymd(end), "response": "json"}


def tpex_par_change_request(start: date, end: date):
    return f"{TPEX}/bulletin/pvChgRslt", {"startDate": _slash(start), "endDate": _slash(end), "response": "json"}


def twse_etf_split_request(start: date, end: date):
    return f"{TWSE}/split/TWTCAU", {"startDate": _ymd(start), "endDate": _ymd(end), "response": "json"}


# ------------------------------------------------------------------ parsers
def parse_tpex_ex_rights(payload: dict) -> ParseResult:
    """除權息計算結果表: 除權息日期, 代號, 名稱, 除權息前收盤價, 除權息參考價, ..., 權/息, ..., 現金股利, 每仟股無償配股."""
    res = ParseResult()
    for f, data in _rows(payload):
        idt, ic, iname = find_col(f, "除權息日期"), find_col(f, "代號"), find_col(f, "名稱")
        iprev, iref, itype = find_col(f, "除權息前收盤價"), find_col(f, "除權息參考價"), find_col(f, "權/息")
        icash, istock = find_col(f, "現金股利"), find_col(f, "無償配股")
        if None in (idt, ic, iprev, iref):
            continue
        for r in data:
            kind = strip_html(str(r[itype])).strip() if itype is not None else ""
            action = {"息": "DIV", "除息": "DIV", "權": "RIGHTS", "除權": "RIGHTS", "權息": "DIV_RIGHTS",
                      "除權息": "DIV_RIGHTS"}.get(kind, "OTHER")
            stock = to_decimal(r[istock]) if istock is not None else None
            res.records.append(CorporateActionRecord(
                code=_code(r[ic]), name=strip_html(str(r[iname])).strip() if iname is not None else "",
                ex_date=_date(r[idt]), action_type=action, prev_close=to_decimal(r[iprev]),
                ref_price=to_decimal(r[iref]),
                cash_dividend=to_decimal(r[icash]) if icash is not None else None,
                stock_dividend_ratio=(stock / 1000 if stock else None),
            ))
    return res


def _parse_resume(payload: dict, action: str, date_kw: str, prev_kw: str, ref_kw: str,
                  code_kw: str = "代號", name_kw: str = "名稱", kind_kw: str | None = None) -> ParseResult:
    res = ParseResult()
    for f, data in _rows(payload):
        idt, ic, iname = find_col(f, date_kw), find_col(f, code_kw), find_col(f, name_kw)
        iprev, iref = find_col(f, prev_kw), find_col(f, ref_kw)
        ikind = find_col(f, kind_kw) if kind_kw else None
        if None in (idt, ic, iprev, iref):
            continue
        for r in data:
            act = action
            if ikind is not None:
                act = "REVERSE_SPLIT" if "反" in str(r[ikind]) else "SPLIT"
            res.records.append(CorporateActionRecord(
                code=_code(r[ic]), name=strip_html(str(r[iname])).strip() if iname is not None else "",
                ex_date=_date(r[idt]), action_type=act, prev_close=to_decimal(r[iprev]),
                ref_price=to_decimal(r[iref])))
    return res


def parse_capital_reduction(payload: dict) -> ParseResult:
    """TWSE TWTAUU / TPEx revivt: 恢復買賣日期, 股票代號, 名稱, 停止買賣前(最後交易日之)收盤價格, 恢復買賣(開始日)參考價."""
    return _parse_resume(payload, "CAPITAL_REDUCTION", "恢復買賣日期", "收盤價格", "參考價", code_kw="股票代號")


def parse_par_change(payload: dict) -> ParseResult:
    """TWSE TWTB8U / TPEx pvChgRslt: 恢復買賣日期, (股票|證券)代號, 名稱, 停止買賣前/最後交易日之收盤價格, 恢復買賣(開始)參考價."""
    return _parse_resume(payload, "PAR_CHANGE", "恢復買賣日期", "收盤價格", "參考價")


def parse_etf_split(payload: dict) -> ParseResult:
    """TWSE TWTCAU: 恢復買賣日期, ETF代號, 名稱, 分割(反分割), 停止買賣前收盤價格, 恢復買賣參考價."""
    return _parse_resume(payload, "SPLIT", "恢復買賣日期", "收盤價格", "恢復買賣參考價", code_kw="代號",
                         kind_kw="分割")


EVENTS = {
    # name: (market, source, request(start, end), parser)
    "tpex_ex_rights": ("TPEX", SOURCE_TPEX, tpex_ex_rights_request, parse_tpex_ex_rights),
    "twse_capital_reduction": ("TWSE", SOURCE_TWSE, twse_capital_reduction_request, parse_capital_reduction),
    "tpex_capital_reduction": ("TPEX", SOURCE_TPEX, tpex_capital_reduction_request, parse_capital_reduction),
    "twse_par_change": ("TWSE", SOURCE_TWSE, twse_par_change_request, parse_par_change),
    "tpex_par_change": ("TPEX", SOURCE_TPEX, tpex_par_change_request, parse_par_change),
    "twse_etf_split": ("TWSE", SOURCE_TWSE, twse_etf_split_request, parse_etf_split),
}
