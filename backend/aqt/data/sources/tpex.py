"""TPEx (證券櫃檯買賣中心) public after-hours reports.

TPEx moved to a new site in 2024 (`/www/zh-tw/...` returning `tables[{fields,data}]`).
The legacy `/web/...php` endpoints returned positional `aaData`. Parsers accept both.
"""
from datetime import date

from aqt.data.parsing import find_col, parse_any_date, strip_html, to_decimal, to_int
from aqt.data.records import (
    InstitutionalRecord, MarginRecord, ParseResult, PriceRecord, ValuationRecord,
)

SOURCE = "tpex"
BASE = "https://www.tpex.org.tw/www/zh-tw"


def _slash(d: date) -> str:
    return d.strftime("%Y/%m/%d")


def _tables(payload: dict) -> list[dict]:
    if isinstance(payload.get("tables"), list):
        return [t for t in payload["tables"] if isinstance(t, dict)]
    if "aaData" in payload:
        return [{"fields": None, "data": payload["aaData"]}]
    return []


def _rows_total(payload: dict) -> int:
    return sum(len(t.get("data") or []) for t in _tables(payload))


def _is_stock_code(code: str) -> bool:
    return code[:1].isdigit() and code.isalnum() and 4 <= len(code) <= 6


# ---------------------------------------------------------------- daily quotes
def daily_quotes_request(d: date) -> tuple[str, dict]:
    # 上櫃股票每日收盤行情 (all securities except warrants); `dailyQ` now redirects to /errors
    return f"{BASE}/afterTrading/otc", {"date": _slash(d), "type": "EW", "response": "json"}


# legacy aaData positions
_Q_POS = dict(code=0, name=1, close=2, chg=3, open=4, high=5, low=6, vol=8, val=9, n=10, nref=16)


def parse_daily_quotes(payload: dict, d: date) -> ParseResult:
    if _rows_total(payload) == 0:
        # TPEx returns an empty table (not an error) on non-trading days
        return ParseResult(is_trading_day=False, note="empty table")
    res = ParseResult(is_trading_day=True)
    for t in _tables(payload):
        f = [strip_html(x) for x in t["fields"]] if t.get("fields") else None
        if f is not None:
            if find_col(f, "代號") is None or find_col(f, "收盤") is None:
                continue
            p = dict(code=find_col(f, "代號"), name=find_col(f, "名稱"), close=find_col(f, "收盤"),
                     chg=find_col(f, "漲跌"), open=find_col(f, "開盤"), high=find_col(f, "最高"),
                     low=find_col(f, "最低"), vol=find_col(f, "成交股數"), val=find_col(f, "成交金額"),
                     n=find_col(f, "成交筆數"), nref=find_col(f, "次日參考價"))
        else:
            p = _Q_POS
        for r in t.get("data") or []:
            code = str(r[p["code"]]).strip()
            if not _is_stock_code(code):
                continue
            res.records.append(PriceRecord(
                code=code, name=str(r[p["name"]]).strip(), market="TPEX", trade_date=d,
                open=to_decimal(r[p["open"]]), high=to_decimal(r[p["high"]]), low=to_decimal(r[p["low"]]),
                close=to_decimal(r[p["close"]]), change=to_decimal(r[p["chg"]]),
                volume=to_int(r[p["vol"]]) or 0, turnover=to_int(r[p["val"]]) or 0,
                transactions=to_int(r[p["n"]]),
                next_ref_price=to_decimal(r[p["nref"]]) if p.get("nref") is not None and len(r) > p["nref"] else None,
            ))
    return res


# ---------------------------------------------------------------- institutional
def institutional_request(d: date) -> tuple[str, dict]:
    return f"{BASE}/insti/dailyTrade", {"type": "Daily", "sect": "EW", "date": _slash(d), "response": "json"}


# legacy 24-column aaData: code,name, 外資(不含自營)買/賣/超, 外資自營商買/賣/超, 外資合計買/賣/超,
# 投信買/賣/超, 自營(自行)買/賣/超, 自營(避險)買/賣/超, 自營合計買/賣/超, 三大法人合計
_I_POS = dict(code=0, name=1, fb=2, fs=3, fn=4, fdn=7, tb=11, ts=12, tn=13, dsn=16, dhn=19, dn=22, tot=23)


def parse_institutional(payload: dict, d: date) -> ParseResult:
    if _rows_total(payload) == 0:
        return ParseResult(is_trading_day=None, note="empty table")
    res = ParseResult(is_trading_day=True)
    for t in _tables(payload):
        f = [strip_html(x) for x in t["fields"]] if t.get("fields") else None
        if f is not None and len(f) >= 24:
            # new site uses grouped headers with generic sub-headers; columns keep legacy order
            p = _I_POS
        elif f is not None:
            p = dict(code=find_col(f, "代號"), name=find_col(f, "名稱"),
                     fb=find_col(f, "外資", "買進", exclude=("自營",)), fs=find_col(f, "外資", "賣出", exclude=("自營",)),
                     fn=find_col(f, "外資", "買賣超", exclude=("自營",)), fdn=find_col(f, "外資自營商", "買賣超"),
                     tb=find_col(f, "投信", "買進"), ts=find_col(f, "投信", "賣出"), tn=find_col(f, "投信", "買賣超"),
                     dsn=find_col(f, "自營商", "自行", "買賣超"), dhn=find_col(f, "自營商", "避險", "買賣超"),
                     dn=find_col(f, "自營商", "買賣超", exclude=("自行", "避險", "外資")),
                     tot=find_col(f, "三大法人"))
            if p["code"] is None or p["tot"] is None:
                continue
        else:
            p = _I_POS

        def g(r, k):
            i = p.get(k)
            return to_int(r[i]) if i is not None and i < len(r) else None

        for r in t.get("data") or []:
            code = str(r[p["code"]]).strip()
            if not _is_stock_code(code):
                continue
            res.records.append(InstitutionalRecord(
                code=code, name=str(r[p["name"]]).strip(), trade_date=d,
                foreign_buy=g(r, "fb"), foreign_sell=g(r, "fs"), foreign_net=g(r, "fn"),
                foreign_dealer_net=g(r, "fdn"), trust_buy=g(r, "tb"), trust_sell=g(r, "ts"),
                trust_net=g(r, "tn"), dealer_net=g(r, "dn"), dealer_self_net=g(r, "dsn"),
                dealer_hedge_net=g(r, "dhn"), total_net=g(r, "tot"),
            ))
    return res


# ---------------------------------------------------------------- margin
def margin_request(d: date) -> tuple[str, dict]:
    return f"{BASE}/margin/balance", {"date": _slash(d), "response": "json"}


# 代號,名稱,前資餘額,資買,資賣,現償,資餘額,資屬證金,資使用率,資限額,
# 前券餘額,券賣,券買,券償,券餘額,券屬證金,券使用率,券限額,資券相抵,備註
_M_POS = dict(code=0, name=1, mbp=2, mb=3, ms=4, mr=5, mbal=6, mlim=9,
              sbp=10, ss=11, sc=12, sr=13, sbal=14, slim=17, off=18, note=19)


def parse_margin(payload: dict, d: date) -> ParseResult:
    if _rows_total(payload) == 0:
        return ParseResult(is_trading_day=None, note="empty table")
    res = ParseResult(is_trading_day=True)
    for t in _tables(payload):
        rows = t.get("data") or []
        if rows and len(rows[0]) < 19:
            continue
        for r in rows:
            code = str(r[0]).strip()
            if not _is_stock_code(code):
                continue
            v = [to_int(x) for x in r]
            p = _M_POS
            res.records.append(MarginRecord(
                code=code, name=str(r[1]).strip(), trade_date=d,
                margin_buy=v[p["mb"]], margin_sell=v[p["ms"]], margin_redeem=v[p["mr"]],
                margin_balance_prev=v[p["mbp"]], margin_balance=v[p["mbal"]], margin_limit=v[p["mlim"]],
                short_sell=v[p["ss"]], short_cover=v[p["sc"]], short_redeem=v[p["sr"]],
                short_balance_prev=v[p["sbp"]], short_balance=v[p["sbal"]], short_limit=v[p["slim"]],
                offset=v[p["off"]], note=(strip_html(str(r[p["note"]])) or None) if len(r) > p["note"] else None,
            ))
    return res


# ---------------------------------------------------------------- valuation
def valuation_request(d: date) -> tuple[str, dict]:
    return f"{BASE}/afterTrading/peQryDate", {"date": _slash(d), "response": "json"}


def parse_valuation(payload: dict, d: date) -> ParseResult:
    res = ParseResult(is_trading_day=True if _rows_total(payload) else None)
    for t in _tables(payload):
        f = [strip_html(x) for x in t["fields"]] if t.get("fields") else None
        if f is None:
            continue
        ic, ipe = find_col(f, "代號"), find_col(f, "本益比")
        if ic is None or ipe is None:
            continue
        iname, iyld, ipb = find_col(f, "名稱"), find_col(f, "殖利率"), find_col(f, "淨值比")
        for r in t.get("data") or []:
            res.records.append(ValuationRecord(
                code=str(r[ic]).strip(), name=str(r[iname]).strip(), trade_date=d,
                pe_ratio=to_decimal(r[ipe]), pb_ratio=to_decimal(r[ipb]) if ipb is not None else None,
                dividend_yield=to_decimal(r[iyld]) if iyld is not None else None,
            ))
    return res


def payload_date(payload: dict) -> date | None:
    """The date TPEx says the payload is for (guards against it silently returning another day)."""
    for k in ("date", "reportDate"):
        if payload.get(k):
            try:
                return parse_any_date(str(payload[k]))
            except ValueError:
                return None
    return None
