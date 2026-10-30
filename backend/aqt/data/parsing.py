"""Small, well-tested helpers for the quirky number/date formats used by TWSE / TPEx / MOPS."""
import re
from datetime import date
from decimal import Decimal, InvalidOperation

_EMPTY = {"", "--", "---", "----", "-", "N/A", "X", "除權息", "除息", "除權", "null", "None"}
_TAG = re.compile(r"<[^>]+>")


def strip_html(s: str) -> str:
    return _TAG.sub("", s).strip()


def to_decimal(v) -> Decimal | None:
    if v is None:
        return None
    if isinstance(v, (int, float, Decimal)):
        return Decimal(str(v))
    s = strip_html(str(v)).replace(",", "").replace("%", "").strip()
    if s in _EMPTY:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def to_int(v) -> int | None:
    d = to_decimal(v)
    return None if d is None else int(d)


def signed_change(sign_cell, amount_cell) -> Decimal | None:
    """TWSE MI_INDEX splits the change into a sign cell (often HTML) and an absolute amount."""
    amt = to_decimal(amount_cell)
    if amt is None:
        return None
    sign = strip_html(str(sign_cell or ""))
    return -amt if "-" in sign else amt


def roc_to_date(s: str) -> date:
    """'113/01/02', '1130102', '113年01月02日' -> date(2024,1,2)."""
    s = str(s).strip()
    m = re.fullmatch(r"(\d{2,3})[/\-年](\d{1,2})[/\-月](\d{1,2})日?", s)
    if m:
        y, mo, d = (int(x) for x in m.groups())
    elif re.fullmatch(r"\d{7}", s):
        y, mo, d = int(s[:3]), int(s[3:5]), int(s[5:7])
    else:
        raise ValueError(f"not a ROC date: {s!r}")
    return date(y + 1911, mo, d)


def date_to_roc(d: date, sep: str = "/") -> str:
    return f"{d.year - 1911}{sep}{d.month:02d}{sep}{d.day:02d}"


def parse_any_date(s: str) -> date:
    s = str(s).strip()
    m = re.fullmatch(r"(\d{4})[/\-]?(\d{2})[/\-]?(\d{2})", s)
    if m:
        return date(*(int(x) for x in m.groups()))
    return roc_to_date(s)


def find_col(fields: list[str], *keywords: str, exclude: tuple[str, ...] = ()) -> int | None:
    """Index of the first header containing every keyword and none of `exclude`."""
    for i, f in enumerate(fields):
        f2 = strip_html(f)
        if all(k in f2 for k in keywords) and not any(x in f2 for x in exclude):
            return i
    return None
