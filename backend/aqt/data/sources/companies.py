"""Listed / OTC company registry from the TWSE / TPEx OpenAPI (上市/上櫃公司基本資料).

Fallback for the security master when the ISIN registry (isin.twse.com.tw) is unavailable —
that site answers "FOR SECURITY REASONS, THIS PAGE CAN NOT BE ACCESSED" to some hosts.
It lists companies only (common stock issuers), so ETFs / preferred shares are not covered;
those are created from the daily quotes (`ensure_securities`) and are never marked delisted
from this list.
"""
from datetime import date

from aqt.data.records import ParseResult, SecurityRecord

SOURCE = "company_registry"
URL = {"TWSE": "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
       "TPEX": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"}

# 證交所 / 櫃買中心 產業類別代碼
INDUSTRY = {
    "01": "水泥工業", "02": "食品工業", "03": "塑膠工業", "04": "紡織纖維", "05": "電機機械",
    "06": "電器電纜", "08": "玻璃陶瓷", "09": "造紙工業", "10": "鋼鐵工業", "11": "橡膠工業",
    "12": "汽車工業", "14": "建材營造業", "15": "航運業", "16": "觀光餐旅", "17": "金融保險業",
    "18": "貿易百貨業", "19": "綜合", "20": "其他業", "21": "化學工業", "22": "生技醫療業",
    "23": "油電燃氣業", "24": "半導體業", "25": "電腦及週邊設備業", "26": "光電業",
    "27": "通信網路業", "28": "電子零組件業", "29": "電子通路業", "30": "資訊服務業",
    "31": "其他電子業", "32": "文化創意業", "33": "農業科技業", "34": "電子商務",
    "35": "綠能環保", "36": "數位雲端", "37": "運動休閒", "38": "居家生活", "80": "管理股票",
    "91": "存託憑證",
}

# TWSE uses Chinese keys, TPEx English keys
_KEYS = {
    "code": ("公司代號", "SecuritiesCompanyCode"),
    "name": ("公司簡稱", "CompanyAbbreviation"),
    "industry": ("產業別", "SecuritiesIndustryCode"),
    "listed": ("上市日期", "上櫃日期", "DateOfListing"),
}


def _pick(row: dict, field: str):
    for k in _KEYS[field]:
        if row.get(k) not in (None, ""):
            return str(row[k]).strip()
    return None


def _ymd(s: str | None) -> date | None:
    if not s or len(s) != 8 or not s.isdigit():
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:]))
    except ValueError:
        return None


def parse(rows: list[dict], market: str) -> ParseResult:
    res = ParseResult()
    for r in rows if isinstance(rows, list) else []:
        code, name = _pick(r, "code"), _pick(r, "name")
        if not code or not name:
            continue
        ind = _pick(r, "industry")
        res.records.append(SecurityRecord(
            code=code, name=name, market=market, security_type="STOCK", isin=None,
            listed_date=_ymd(_pick(r, "listed")), industry=INDUSTRY.get(ind, ind) if ind else None,
        ))
    return res
