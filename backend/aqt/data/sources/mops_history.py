"""Historical financial statements and their actual publication time (G7).

Values: MOPS summary reports (公開資訊觀測站 彙總報表), one POST per market and quarter, every
industry template on one page:
  * ajax_t163sb04 — 綜合損益表 (year-to-date cumulative, thousand TWD)
  * ajax_t163sb05 — 資產負債表

Publication time: TWSE electronic-document service `doc.twse.com.tw/server-java/t57sb01`, one GET per
company and year, lists every filed quarterly report PDF with its upload timestamp
(e.g. 201901_2330_AI1.pdf  108/05/15 13:41:47). The earliest upload of a quarter's financial report is
used as `published_at`. Without it a statement has no reliable publication time and must not be used
by fundamental backtests (`pit_reliable = False`).
"""
import re
from datetime import datetime

from aqt.data.parsing import strip_html, to_decimal, to_int
from aqt.data.pit import TPE
from aqt.data.records import FinancialRecord, ParseResult

MOPS = "https://mopsov.twse.com.tw/mops/web"
DOC = "https://doc.twse.com.tw/server-java/t57sb01"
SOURCE, DOC_SOURCE = "mops", "twse_doc"
TYPEK = {"TWSE": "sii", "TPEX": "otc"}

_TABLE = re.compile(r"<table[^>]*>(.*?)</table>", re.S | re.I)
_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)


def summary_request(report: str, market: str, year: int, quarter: int) -> tuple[str, dict]:
    """report: 'income' (t163sb04) | 'balance' (t163sb05). Returns (url, POST form)."""
    page = {"income": "ajax_t163sb04", "balance": "ajax_t163sb05"}[report]
    return f"{MOPS}/{page}", {"encodeURIComponent": "1", "step": "1", "firstin": "1", "off": "1", "isQuery": "Y",
                              "TYPEK": TYPEK[market], "year": str(year - 1911), "season": f"{quarter:02d}"}


def _norm(h: str) -> str:
    return re.sub(r"\s+", "", strip_html(h)).replace("(", "（").replace(")", "）")


def _tables(html: str):
    for t in _TABLE.findall(html):
        rows = _TR.findall(t)
        head = None
        for r in rows:
            # header cells are split on the opening tag: some MOPS pages omit the last </th>
            ths = re.split(r"<th[^>]*>", r, flags=re.I)[1:]
            if ths and any("公司代號" in _norm(x) for x in ths):
                head = [_norm(x) for x in ths]
                continue
            tds = _TD.findall(r)
            if head and len(tds) == len(head):
                yield head, [strip_html(x).replace("&nbsp;", "").strip() for x in tds]


def _pick(head: list[str], row: list[str], *names: str):
    for n in names:
        if n in head:
            return row[head.index(n)]
    return None


def parse_income_summary(html: str, year: int, quarter: int) -> ParseResult:
    res = ParseResult()
    for head, row in _tables(html):
        code = row[head.index("公司代號")]
        if not re.fullmatch(r"\d{4,6}[A-Z]?", code):
            continue
        revenue = to_int(_pick(head, row, "營業收入", "淨收益", "收益"))
        if revenue is None and "利息淨收益" in head:  # banks: net interest + non-interest income
            a, b = to_int(_pick(head, row, "利息淨收益")), to_int(_pick(head, row, "利息以外淨損益"))
            revenue = (a or 0) + (b or 0) if a is not None or b is not None else None
        res.records.append(FinancialRecord(
            code=code, name=row[head.index("公司名稱")] if "公司名稱" in head else "", fiscal_year=year,
            fiscal_quarter=quarter, revenue=revenue,
            gross_profit=to_int(_pick(head, row, "營業毛利（毛損）淨額", "營業毛利（毛損）")),
            operating_income=to_int(_pick(head, row, "營業利益（損失）", "營業利益")),
            net_income=to_int(_pick(head, row, "本期淨利（淨損）", "本期稅後淨利（淨損）")),
            eps=to_decimal(_pick(head, row, "基本每股盈餘（元）")),
        ))
    return res


def parse_balance_summary(html: str, year: int, quarter: int) -> ParseResult:
    res = ParseResult()
    for head, row in _tables(html):
        code = row[head.index("公司代號")]
        if not re.fullmatch(r"\d{4,6}[A-Z]?", code):
            continue
        res.records.append(FinancialRecord(
            code=code, name=row[head.index("公司名稱")] if "公司名稱" in head else "", fiscal_year=year,
            fiscal_quarter=quarter,
            total_assets=to_int(_pick(head, row, "資產總計", "資產總額")),
            total_liabilities=to_int(_pick(head, row, "負債總計", "負債總額")),
            equity=to_int(_pick(head, row, "權益總計", "權益總額")),
        ))
    return res


# ------------------------------------------------------------------ publication times
def doc_request(code: str, year: int) -> tuple[str, dict]:
    return DOC, {"step": "1", "colorchg": "1", "co_id": code, "year": str(year - 1911), "seamon": "", "mtype": "A"}


_FILE = re.compile(r"(\d{4})0([1-4])_([0-9A-Z]+)_AI\w*\.pdf", re.I)
_TS = re.compile(r"(\d{2,3})/(\d{2})/(\d{2})\s+(\d{2}):(\d{2}):(\d{2})")


def parse_doc_uploads(html: str, code: str) -> dict[tuple[int, int], datetime]:
    """{(fiscal_year, quarter): earliest upload time} of the company's financial-report PDFs."""
    out: dict[tuple[int, int], datetime] = {}
    for row in _TR.findall(html):
        cells = [strip_html(x).replace("&nbsp;", " ").strip() for x in _TD.findall(row)]
        text = " ".join(cells)
        f, ts = _FILE.search(text), _TS.search(text)
        if not f or not ts or f.group(3).upper() != code.upper():
            continue
        y, mo, d, hh, mm, ss = (int(x) for x in ts.groups())
        when = datetime(y + 1911, mo, d, hh, mm, ss, tzinfo=TPE)
        key = (int(f.group(1)), int(f.group(2)))
        if key not in out or when < out[key]:
            out[key] = when
    return out


def decode_doc(content: bytes) -> str:
    for enc in ("big5", "cp950"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("cp950", errors="replace")
