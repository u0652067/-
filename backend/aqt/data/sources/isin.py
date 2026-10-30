"""Security master from the TWSE ISIN registry (本國上市/上櫃證券國際證券辨識號碼一覽表).

strMode=2 -> 上市, strMode=4 -> 上櫃. The page is Big5/CP950 HTML.
"""
import re

from aqt.data.parsing import parse_any_date, strip_html
from aqt.data.records import ParseResult, SecurityRecord

SOURCE = "twse_isin"
URL = "https://isin.twse.com.tw/isin/C_public.jsp"
MODES = {"TWSE": "2", "TPEX": "4"}

_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)


def request(market: str) -> tuple[str, dict]:
    return URL, {"strMode": MODES[market]}


def decode(content: bytes) -> str:
    for enc in ("cp950", "big5", "utf-8"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("cp950", errors="replace")


def _type_from(section: str, cfi: str) -> str:
    if section.strip() == "股票" or cfi.startswith("ES"):
        return "STOCK"
    if "ETF" in section or cfi.startswith("CE"):
        return "ETF"
    return "OTHER"


def parse(html: str, market: str) -> ParseResult:
    res = ParseResult()
    section = ""
    for row in _ROW.findall(html):
        cells = [strip_html(c).replace("　", " ").strip() for c in _CELL.findall(row)]
        if len(cells) == 1 or (len(cells) >= 1 and all(not c for c in cells[1:])):
            if cells and cells[0]:
                section = cells[0]
            continue
        if len(cells) < 6 or cells[1] == "國際證券辨識號碼(ISIN Code)":
            continue
        code_name = cells[0].split(None, 1)
        if len(code_name) != 2:
            continue
        code, name = code_name
        try:
            listed = parse_any_date(cells[2]) if cells[2] else None
        except ValueError:
            listed = None
        res.records.append(SecurityRecord(
            code=code.strip(), name=name.strip(), market=market,
            security_type=_type_from(section, cells[5]), isin=cells[1] or None,
            listed_date=listed, industry=cells[4] or None,
        ))
    return res
