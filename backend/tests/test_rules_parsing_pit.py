from datetime import date, datetime
from decimal import Decimal as D

import pytest

from aqt.data import pit, tw_rules
from aqt.data.parsing import date_to_roc, find_col, parse_any_date, roc_to_date, signed_change, to_decimal, to_int


@pytest.mark.parametrize("raw,expected", [
    ("1,234.50", D("1234.50")), ("--", None), ("", None), (" 12 ", D("12")), ("-3.5", D("-3.5")),
    ("<p style= color:red>+</p>", None), ("0.00", D("0")), (None, None), (5, D("5")), ("X", None),
])
def test_to_decimal(raw, expected):
    assert to_decimal(raw) == expected


def test_to_int_and_change_sign():
    assert to_int("3,000,123") == 3000123
    assert signed_change("<p style= color:green>-</p>", "1.50") == D("-1.50")
    assert signed_change("<p style= color:red>+</p>", "1.50") == D("1.50")
    assert signed_change(" ", "0.00") == D("0.00")
    assert signed_change("+", "--") is None


def test_roc_dates():
    assert roc_to_date("113/01/02") == date(2024, 1, 2)
    assert roc_to_date("1130102") == date(2024, 1, 2)
    assert roc_to_date("113年01月02日") == date(2024, 1, 2)
    assert roc_to_date("99/12/31") == date(2010, 12, 31)
    assert parse_any_date("2024-01-02") == date(2024, 1, 2)
    assert parse_any_date("20240102") == date(2024, 1, 2)
    assert date_to_roc(date(2024, 1, 2)) == "113/01/02"
    with pytest.raises(ValueError):
        roc_to_date("hello")


def test_find_col():
    f = ["證券代號", "外陸資買賣超股數(不含外資自營商)", "外資自營商買賣超股數", "自營商買賣超股數"]
    assert find_col(f, "證券代號") == 0
    assert find_col(f, "外陸資買賣超", exclude=("自營商",)) is None  # header itself contains 自營商
    assert find_col(f, "外資自營商買賣超") == 2
    assert find_col(f, "自營商買賣超", exclude=("外資",)) == 3


@pytest.mark.parametrize("price,tick", [
    ("9.99", "0.01"), ("10", "0.05"), ("49.95", "0.05"), ("50", "0.1"), ("99.9", "0.1"), ("100", "0.5"),
    ("499.5", "0.5"), ("500", "1"), ("999", "1"), ("1000", "5"), ("3000", "5"),
])
def test_tick_size(price, tick):
    assert tw_rules.tick_size(D(price)) == D(tick)


def test_etf_tick():
    assert tw_rules.tick_size(D("49.99"), is_etf=True) == D("0.01")
    assert tw_rules.tick_size(D("130"), is_etf=True) == D("0.05")


@pytest.mark.parametrize("ref,up,down", [
    ("100", "110", "90"),
    ("9.5", "10.45", "8.55"),
    ("47.3", "52.0", "42.6"),     # 52.03 -> floor 0.1 tick; 42.57 -> ceil 0.05 tick
    ("580", "638", "522"),
    ("1000", "1100", "900"),
    ("12.35", "13.55", "11.15"),  # 13.585 -> 13.55 ; 11.115 -> 11.15
])
def test_price_limits(ref, up, down):
    u, d = tw_rules.price_limits(D(ref))
    assert (u, d) == (D(up), D(down))
    assert tw_rules.is_valid_tick(u) and tw_rules.is_valid_tick(d)


def test_fees_and_tax():
    assert tw_rules.commission(D("10000")) == D("20")  # 14.25 -> min 20
    assert tw_rules.commission(D("1000000")) == D("1425")
    assert tw_rules.transaction_tax(D("1000000")) == D("3000")
    assert tw_rules.transaction_tax(D("1000000"), day_trade=True) == D("1500")
    assert tw_rules.transaction_tax(D("1000000"), is_etf=True) == D("1000")


def test_pit_daily_and_revenue():
    a = pit.daily_dataset_available_at("institutional_trades", date(2024, 3, 5))
    assert a == datetime(2024, 3, 5, 16, 30, tzinfo=pit.TPE)
    # unknown publish date: 11th of next month
    assert pit.monthly_revenue_available_at(date(2024, 12, 1)) == datetime(2025, 1, 11, tzinfo=pit.TPE)
    # known publish date: next day 00:00
    assert pit.monthly_revenue_available_at(date(2024, 3, 1), date(2024, 4, 8)) == datetime(2024, 4, 9, tzinfo=pit.TPE)


@pytest.mark.parametrize("q,expected", [(1, date(2024, 5, 16)), (2, date(2024, 8, 15)), (3, date(2024, 11, 15)),
                                        (4, date(2025, 4, 1))])
def test_pit_financials(q, expected):
    assert pit.financial_statement_available_at(2024, q).date() == expected
