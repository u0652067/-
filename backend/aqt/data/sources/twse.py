"""TWSE (臺灣證券交易所) public after-hours reports.

All endpoints are the exchange's own public JSON reports. Terms: data is public; TWSE
throttles aggressive clients, so the HTTP layer enforces >= 3 s between requests.
Parsers locate columns by header name (they have changed format several times) and fall
back to positions only where headers are ambiguous.
"""
from datetime import date

from aqt.data.parsing import (
    find_col, parse_any_date, roc_to_date, signed_change, strip_html, to_decimal, to_int,
)
from aqt.data.records import (
    CorporateActionRecord, HolidayRecord, IndexRecord, InstitutionalRecord, MarginRecord,
    ParseResult, PriceRecord, ValuationRecord,
)

SOURCE = "twse"
BASE = "https://www.twse.com.tw/rwd/zh"


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _tables(payload: dict) -> list[dict]:
    """Normalise both the current `tables: [...]` layout and the legacy `fieldsN/dataN` layout."""
    if isinstance(payload.get("tables"), list):
        return [t for t in payload["tables"] if isinstance(t, dict)]
    out = []
    if "fields" in payload and "data" in payload:
        out.append({"title": payload.get("title", ""), "fields": payload["fields"], "data": payload["data"]})
    for k in payload:
        if k.startswith("fields") and k != "fields":
            suffix = k[len("fields"):]
            if f"data{suffix}" in payload:
                out.append({"title": payload.get(f"subtitle{suffix}", ""), "fields": payload[k],
                            "data": payload[f"data{suffix}"]})
    return out


def _is_no_data(payload: dict) -> bool:
    """TWSE answers non-trading days / future dates with stat='很抱歉，沒有符合條件的資料!' etc."""
    return str(payload.get("stat", "")).strip().upper() != "OK"


def classify_code(code: str) -> str:
    if code.startswith("00"):
        return "ETF"
    if len(code) == 4 and code.isdigit():
        return "STOCK"
    return "OTHER"


# ---------------------------------------------------------------- daily quotes (MI_INDEX)
def daily_quotes_request(d: date) -> tuple[str, dict]:
    return f"{BASE}/afterTrading/MI_INDEX", {"date": _ymd(d), "type": "ALLBUT0999", "response": "json"}


def parse_daily_quotes(payload: dict, d: date) -> ParseResult:
    if _is_no_data(payload):
        return ParseResult(is_trading_day=False, note=str(payload.get("stat")))
    res = ParseResult(is_trading_day=True)
    for t in _tables(payload):
        fields = [strip_html(f) for f in t.get("fields", [])]
        rows = t.get("data", []) or []
        i_code, i_close = find_col(fields, "證券代號"), find_col(fields, "收盤價")
        if i_code is not None and i_close is not None:
            ix = {
                "name": find_col(fields, "證券名稱"), "vol": find_col(fields, "成交股數"),
                "n": find_col(fields, "成交筆數"), "val": find_col(fields, "成交金額"),
                "open": find_col(fields, "開盤價"), "high": find_col(fields, "最高價"),
                "low": find_col(fields, "最低價"), "sign": find_col(fields, "漲跌(+/-)"),
                "chg": find_col(fields, "漲跌價差"),
            }
            for r in rows:
                code = str(r[i_code]).strip()
                res.records.append(PriceRecord(
                    code=code, name=str(r[ix["name"]]).strip(), market="TWSE", trade_date=d,
                    open=to_decimal(r[ix["open"]]), high=to_decimal(r[ix["high"]]),
                    low=to_decimal(r[ix["low"]]), close=to_decimal(r[i_close]),
                    change=signed_change(r[ix["sign"]], r[ix["chg"]]) if ix["sign"] is not None else None,
                    volume=to_int(r[ix["vol"]]) or 0, turnover=to_int(r[ix["val"]]) or 0,
                    transactions=to_int(r[ix["n"]]),
                ))
            continue
        i_idx, i_iclose = find_col(fields, "指數"), find_col(fields, "收盤指數")
        if i_idx is not None and i_iclose is not None:
            i_sign, i_pts, i_pct = find_col(fields, "漲跌(+/-)"), find_col(fields, "漲跌點數"), find_col(fields, "漲跌百分比")
            for r in rows:
                name = strip_html(str(r[i_idx]))
                code = "TAIEX" if name == "發行量加權股價指數" else f"TWSE:{name}"
                chg = signed_change(r[i_sign], r[i_pts]) if i_sign is not None else to_decimal(r[i_pts])
                pct = to_decimal(r[i_pct]) if i_pct is not None else None
                if pct is not None and chg is not None and chg < 0 and pct > 0:
                    pct = -pct
                res.indices.append(IndexRecord(code, name, d, to_decimal(r[i_iclose]), chg, pct))
    if not res.records:
        res.is_trading_day = None
        res.note = "payload OK but no quote table recognised (format change?)"
    return res


# ---------------------------------------------------------------- institutional (T86)
def institutional_request(d: date) -> tuple[str, dict]:
    return f"{BASE}/fund/T86", {"date": _ymd(d), "selectType": "ALLBUT0999", "response": "json"}


def parse_institutional(payload: dict, d: date) -> ParseResult:
    if _is_no_data(payload):
        return ParseResult(is_trading_day=None, note=str(payload.get("stat")))
    res = ParseResult(is_trading_day=True)
    for t in _tables(payload):
        f = [strip_html(x) for x in t.get("fields", [])]
        if find_col(f, "證券代號") is None or find_col(f, "三大法人") is None:
            continue
        c = {
            "code": find_col(f, "證券代號"), "name": find_col(f, "證券名稱"),
            # "外陸資買進股數(不含外資自營商)" (since 2017) or legacy "外資買進股數"; neither substring
            # matches the separate "外資自營商買進股數" column.
            "fb": find_col(f, "外陸資買進") or find_col(f, "外資買進"),
            "fs": find_col(f, "外陸資賣出") or find_col(f, "外資賣出"),
            "fn": find_col(f, "外陸資買賣超") or find_col(f, "外資買賣超"),
            "fdn": find_col(f, "外資自營商買賣超"),
            "tb": find_col(f, "投信買進"), "ts": find_col(f, "投信賣出"), "tn": find_col(f, "投信買賣超"),
            "dn": find_col(f, "自營商買賣超", exclude=("自行", "避險", "外資")),
            "dsn": find_col(f, "自營商買賣超", "自行"), "dhn": find_col(f, "自營商買賣超", "避險"),
            "tot": find_col(f, "三大法人買賣超"),
        }

        def g(r, k):
            return to_int(r[c[k]]) if c[k] is not None else None

        for r in t.get("data", []) or []:
            res.records.append(InstitutionalRecord(
                code=str(r[c["code"]]).strip(), name=str(r[c["name"]]).strip(), trade_date=d,
                foreign_buy=g(r, "fb"), foreign_sell=g(r, "fs"), foreign_net=g(r, "fn"),
                foreign_dealer_net=g(r, "fdn"), trust_buy=g(r, "tb"), trust_sell=g(r, "ts"),
                trust_net=g(r, "tn"), dealer_net=g(r, "dn"), dealer_self_net=g(r, "dsn"),
                dealer_hedge_net=g(r, "dhn"), total_net=g(r, "tot"),
            ))
    return res


# ---------------------------------------------------------------- margin (MI_MARGN)
def margin_request(d: date) -> tuple[str, dict]:
    return f"{BASE}/marginTrading/MI_MARGN", {"date": _ymd(d), "selectType": "ALL", "response": "json"}


def parse_margin(payload: dict, d: date) -> ParseResult:
    if _is_no_data(payload):
        return ParseResult(is_trading_day=None, note=str(payload.get("stat")))
    tables = _tables(payload)
    if "creditFields" in payload:  # legacy layout
        tables.append({"fields": payload["creditFields"], "data": payload.get("creditList", [])})
    res = ParseResult(is_trading_day=True)
    for t in tables:
        f = [strip_html(x) for x in t.get("fields", [])]
        # detail table: 代號, 名稱, then 6 margin cols, 6 short cols, 資券互抵, 註記
        if len(f) < 15 or find_col(f, "代號") != 0:
            continue
        for r in t.get("data", []) or []:
            v = [to_int(x) for x in r]
            res.records.append(MarginRecord(
                code=str(r[0]).strip(), name=str(r[1]).strip(), trade_date=d,
                margin_buy=v[2], margin_sell=v[3], margin_redeem=v[4], margin_balance_prev=v[5],
                margin_balance=v[6], margin_limit=v[7], short_cover=v[8], short_sell=v[9],
                short_redeem=v[10], short_balance_prev=v[11], short_balance=v[12], short_limit=v[13],
                offset=v[14], note=(strip_html(str(r[15])) or None) if len(r) > 15 else None,
            ))
    return res


# ---------------------------------------------------------------- valuation (BWIBBU_d)
def valuation_request(d: date) -> tuple[str, dict]:
    return f"{BASE}/afterTrading/BWIBBU_d", {"date": _ymd(d), "selectType": "ALL", "response": "json"}


def parse_valuation(payload: dict, d: date) -> ParseResult:
    if _is_no_data(payload):
        return ParseResult(is_trading_day=None, note=str(payload.get("stat")))
    res = ParseResult(is_trading_day=True)
    for t in _tables(payload):
        f = [strip_html(x) for x in t.get("fields", [])]
        ic, ipe = find_col(f, "證券代號"), find_col(f, "本益比")
        if ic is None or ipe is None:
            continue
        iname, iyld, ipb = find_col(f, "證券名稱"), find_col(f, "殖利率"), find_col(f, "股價淨值比")
        for r in t.get("data", []) or []:
            res.records.append(ValuationRecord(
                code=str(r[ic]).strip(), name=str(r[iname]).strip(), trade_date=d,
                pe_ratio=to_decimal(r[ipe]), pb_ratio=to_decimal(r[ipb]) if ipb is not None else None,
                dividend_yield=to_decimal(r[iyld]) if iyld is not None else None,
            ))
    return res


# ---------------------------------------------------------------- ex-rights / dividends (TWT49U)
def corporate_actions_request(start: date, end: date) -> tuple[str, dict]:
    return f"{BASE}/exRight/TWT49U", {"startDate": _ymd(start), "endDate": _ymd(end), "response": "json"}


def parse_corporate_actions(payload: dict) -> ParseResult:
    if _is_no_data(payload):
        return ParseResult(note=str(payload.get("stat")))
    res = ParseResult()
    for t in _tables(payload):
        f = [strip_html(x) for x in t.get("fields", [])]
        idt, ic = find_col(f, "資料日期"), find_col(f, "股票代號")
        if idt is None or ic is None:
            continue
        iname, iprev, iref = find_col(f, "股票名稱"), find_col(f, "除權息前收盤價"), find_col(f, "除權息參考價")
        ival, itype = find_col(f, "權值+息值") or find_col(f, "權值"), find_col(f, "權/息")
        for r in t.get("data", []) or []:
            kind = strip_html(str(r[itype])) if itype is not None else ""
            action = {"息": "DIV", "權": "RIGHTS", "權息": "DIV_RIGHTS"}.get(kind, "OTHER")
            res.records.append(CorporateActionRecord(
                code=str(r[ic]).strip(), name=str(r[iname]).strip(), ex_date=roc_to_date(str(r[idt])),
                action_type=action, prev_close=to_decimal(r[iprev]), ref_price=to_decimal(r[iref]),
                cash_dividend=to_decimal(r[ival]) if action == "DIV" and ival is not None else None,
            ))
    return res


# ---------------------------------------------------------------- holidays
def holidays_request(year: int) -> tuple[str, dict]:
    return f"{BASE}/holidaySchedule/holidaySchedule", {"queryYear": str(year - 1911), "response": "json"}


def parse_holidays(payload: dict) -> ParseResult:
    res = ParseResult()
    for t in _tables(payload):
        for r in t.get("data", []) or []:
            try:
                d = parse_any_date(str(r[0]))
            except ValueError:
                continue
            name = strip_html(str(r[1]))
            # "XX開始交易" / "XX最後交易" rows mark trading days around long holidays
            trading = ("開始交易" in name or "最後交易" in name)
            res.records.append(HolidayRecord(d, name, trading))
    return res
