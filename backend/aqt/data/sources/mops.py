"""Fundamentals from MOPS (公開資訊觀測站) open data, served via the TWSE / TPEx OpenAPI mirrors.

* Monthly revenue: latest month (t187ap05_L / mopsfin_t187ap05_O) and historical HTML
  pages (t21sc03) for backfill.
* Income statement (t187ap06_*_ci) and balance sheet (t187ap07_*_ci): latest quarter, general
  industry template. Values are year-to-date cumulative, thousand TWD.

Point-in-time: open-data `出表日期` is the date the file was generated, so anything in it was
public on that date — we use it as an upper bound of the announcement date.
"""
import re
from datetime import date

from aqt.data.parsing import roc_to_date, strip_html, to_decimal, to_int
from aqt.data.records import FinancialRecord, ParseResult, RevenueRecord

SOURCE = "mops"
TWSE_OPENAPI = "https://openapi.twse.com.tw/v1/opendata"
TPEX_OPENAPI = "https://www.tpex.org.tw/openapi/v1"

REVENUE_URL = {"TWSE": f"{TWSE_OPENAPI}/t187ap05_L", "TPEX": f"{TPEX_OPENAPI}/mopsfin_t187ap05_O"}
INCOME_URL = {"TWSE": f"{TWSE_OPENAPI}/t187ap06_L_ci", "TPEX": f"{TPEX_OPENAPI}/mopsfin_t187ap06_O_ci"}
BALANCE_URL = {"TWSE": f"{TWSE_OPENAPI}/t187ap07_L_ci", "TPEX": f"{TPEX_OPENAPI}/mopsfin_t187ap07_O_ci"}


def historical_revenue_url(market: str, year: int, month: int) -> str:
    seg = "sii" if market == "TWSE" else "otc"
    return f"https://mopsov.twse.com.tw/nas/t21/{seg}/t21sc03_{year - 1911}_{month}_0.html"


def _get(row: dict, *keywords: str, exclude: tuple[str, ...] = ()):
    for k, v in row.items():
        k2 = k.replace("（", "(").replace("）", ")")
        if all(w in k2 for w in keywords) and not any(x in k2 for x in exclude):
            return v
    return None


def _roc_ym(s: str) -> date:
    s = str(s).strip()
    return date(int(s[:-2]) + 1911, int(s[-2:]), 1)


def _roc_date_or_none(s) -> date | None:
    try:
        return roc_to_date(str(s)) if s else None
    except ValueError:
        return None


def parse_revenue_openapi(rows: list[dict]) -> ParseResult:
    res = ParseResult()
    for r in rows:
        code = str(_get(r, "公司代號") or "").strip()
        ym = _get(r, "資料年月")
        if not code or not ym:
            continue
        res.records.append(RevenueRecord(
            code=code, name=str(_get(r, "公司名稱") or "").strip(), period=_roc_ym(ym),
            revenue=to_int(_get(r, "當月營收")),
            revenue_prev_month=to_int(_get(r, "上月營收")),
            revenue_last_year=to_int(_get(r, "去年當月營收")),
            mom_pct=to_decimal(_get(r, "上月比較增減")),
            yoy_pct=to_decimal(_get(r, "去年同月增減")),
            cum_revenue=to_int(_get(r, "當月累計營收")),
            cum_revenue_last_year=to_int(_get(r, "去年累計營收")),
            cum_yoy_pct=to_decimal(_get(r, "前期比較增減")),
            published_date=_roc_date_or_none(_get(r, "出表日期")),
            industry=_get(r, "產業別"),
        ))
    return res


_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_TD = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)


def parse_revenue_history_html(html: str, year: int, month: int) -> ParseResult:
    """Rows: 代號, 名稱, 當月, 上月, 去年當月, 上月增減%, 去年同月增減%, 當月累計, 去年累計, 前期增減%, 備註.

    Historical pages carry no announcement date -> published_date=None (deadline rule applies).
    """
    res = ParseResult()
    period = date(year, month, 1)
    for row in _TR.findall(html):
        c = [strip_html(x).replace("&nbsp;", "").strip() for x in _TD.findall(row)]
        if len(c) < 10 or not re.fullmatch(r"\d{4,6}[A-Z]?", c[0]):
            continue
        res.records.append(RevenueRecord(
            code=c[0], name=c[1], period=period, revenue=to_int(c[2]), revenue_prev_month=to_int(c[3]),
            revenue_last_year=to_int(c[4]), mom_pct=to_decimal(c[5]), yoy_pct=to_decimal(c[6]),
            cum_revenue=to_int(c[7]), cum_revenue_last_year=to_int(c[8]), cum_yoy_pct=to_decimal(c[9]),
        ))
    return res


def parse_income_openapi(rows: list[dict]) -> ParseResult:
    res = ParseResult()
    for r in rows:
        code = str(_get(r, "公司代號") or "").strip()
        y, q = _get(r, "年度"), _get(r, "季別")
        if not code or y is None or q is None:
            continue
        res.records.append(FinancialRecord(
            code=code, name=str(_get(r, "公司名稱") or "").strip(),
            fiscal_year=int(str(y).strip()) + 1911, fiscal_quarter=int(str(q).strip()),
            revenue=to_int(_get(r, "營業收入", exclude=("營業外",))),
            # prefer "營業毛利（毛損）淨額" (after unrealised inter-company profit) when present
            gross_profit=to_int(_get(r, "營業毛利", "淨額") if _get(r, "營業毛利", "淨額") is not None
                                else _get(r, "營業毛利", exclude=("已實現", "未實現"))),
            operating_income=to_int(_get(r, "營業利益")),
            net_income=to_int(_get(r, "本期淨利", exclude=("歸屬",))),
            eps=to_decimal(_get(r, "基本每股盈餘")),
            published_date=_roc_date_or_none(_get(r, "出表日期")),
        ))
    return res


def parse_balance_openapi(rows: list[dict]) -> ParseResult:
    res = ParseResult()
    for r in rows:
        code = str(_get(r, "公司代號") or "").strip()
        y, q = _get(r, "年度"), _get(r, "季別")
        if not code or y is None or q is None:
            continue
        res.records.append(FinancialRecord(
            code=code, name=str(_get(r, "公司名稱") or "").strip(),
            fiscal_year=int(str(y).strip()) + 1911, fiscal_quarter=int(str(q).strip()),
            total_assets=to_int(_get(r, "資產總")),
            total_liabilities=to_int(_get(r, "負債總")),
            equity=to_int(_get(r, "權益總", exclude=("歸屬", "非控制"))),
            published_date=_roc_date_or_none(_get(r, "出表日期")),
        ))
    return res
