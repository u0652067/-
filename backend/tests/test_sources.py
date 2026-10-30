"""Parser tests against payloads in the documented layouts (current and legacy)."""
from datetime import date, datetime, timezone
from decimal import Decimal as D

from aqt.data.sources import isin, mops, tpex, twse, twse_mis
from tests.fixtures.factory import Market

DAY = date(2024, 1, 2)


def test_twse_quotes_current_layout():
    m = Market(DAY, days=3)
    res = twse.parse_daily_quotes(m.twse_mi_index(DAY), DAY)
    assert res.is_trading_day is True
    codes = {r.code: r for r in res.records}
    assert {"2330", "2317", "0050", "1234"} <= set(codes)
    b = m.bars[("TWSE", "2330")][DAY]
    r = codes["2330"]
    assert r.close == D(f"{b['close']:.2f}") and r.volume == b["volume"] and r.turnover == b["turnover"]
    assert abs(r.change - D(f"{b['close'] - b['prev']:.2f}")) <= D("0.01")
    sus = codes["1234"]
    assert sus.close is None and sus.volume == 0
    taiex = [i for i in res.indices if i.index_code == "TAIEX"][0]
    assert taiex.close == D("17853.76") and taiex.change == D("123.45")
    elec = [i for i in res.indices if i.index_code != "TAIEX"][0]
    assert elec.change == D("-3.21") and elec.change_pct == D("-0.36")


def test_twse_quotes_legacy_layout_and_no_data():
    legacy = {"stat": "OK", "date": "20150105", "fields9": ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額",
                                                             "開盤價", "最高價", "最低價", "收盤價", "漲跌(+/-)", "漲跌價差",
                                                             "最後揭示買價", "最後揭示買量", "最後揭示賣價", "最後揭示賣量",
                                                             "本益比"],
              "data9": [["2330", "台積電", "30,000,000", "10,000", "4,000,000,000", "133.00", "135.00", "132.50",
                         "134.50", "<p style= color:red>+</p>", "2.00", "134.50", "1", "135.00", "1", "15.0"]]}
    res = twse.parse_daily_quotes(legacy, date(2015, 1, 5))
    assert res.records[0].close == D("134.50") and res.records[0].change == D("2.00")
    no = twse.parse_daily_quotes({"stat": "很抱歉，沒有符合條件的資料!"}, date(2024, 1, 1))
    assert no.is_trading_day is False and no.records == []


def test_twse_institutional_margin_valuation():
    m = Market(DAY, days=3)
    inst = twse.parse_institutional(m.twse_t86(DAY), DAY).records
    r = [x for x in inst if x.code == "2330"][0]
    assert (r.foreign_buy, r.foreign_sell, r.foreign_net) == (1_000_000, 800_000, 200_000)
    assert r.foreign_dealer_net == 1_000 and r.trust_net == 30_000
    assert (r.dealer_self_net, r.dealer_hedge_net, r.dealer_net) == (3_000, -2_000, 1_000)
    assert r.total_net == 200_000 + 1_000 + 30_000 + 1_000
    mg = twse.parse_margin(m.twse_margin(DAY), DAY).records
    assert len(mg) == 6  # summary table skipped
    assert mg[0].margin_balance == 1049 and mg[0].short_balance == 35 and mg[0].offset == 2
    val = {v.code: v for v in twse.parse_valuation(m.twse_bwibbu(DAY), DAY).records}
    assert val["2330"].pe_ratio == D("18.50") and val["1101"].pe_ratio is None


def test_twse_corporate_actions_and_holidays():
    m = Market(DAY, days=40, ex_div=("2317", 20, 5.0))
    acts = twse.parse_corporate_actions(m.twse_twt49u(DAY, date(2024, 3, 1))).records
    assert len(acts) == 1 and acts[0].action_type == "DIV" and acts[0].ex_date == m.dates[20]
    assert acts[0].prev_close - acts[0].ref_price == D("5.00")
    hol = twse.parse_holidays({"stat": "OK", "fields": ["日期", "名稱", "說明"], "data": [
        ["2024-01-01", "中華民國開國紀念日", "依規定放假1日。"],
        ["2024-02-05", "農曆春節前最後交易日", ""],
        ["2024-02-06", "市場無交易，僅辦理結算交割作業", ""],
    ]}).records
    assert [(h.cal_date, h.is_trading_day) for h in hol] == [
        (date(2024, 1, 1), False), (date(2024, 2, 5), True), (date(2024, 2, 6), False)]


def test_tpex_quotes_current_and_legacy():
    m = Market(DAY, days=3)
    res = tpex.parse_daily_quotes(m.tpex_daily(DAY), DAY)
    assert res.is_trading_day and {r.code for r in res.records} == {"6488", "5347", "8069"}  # TOTAL row dropped
    b = m.bars[("TPEX", "6488")][DAY]
    r = [x for x in res.records if x.code == "6488"][0]
    assert r.close == D(f"{b['close']:.2f}") and r.volume == b["volume"]
    legacy = {"reportDate": "113/01/02", "aaData": [
        ["6488", "環球晶", "400.00", "+5.00", "396.00", "401.00", "395.00", "398.00", "1,234,000", "490,000,000",
         "1,000", "400.00", "3", "400.50", "4", "478,000,000", "400.00", "440.00", "360.00"]]}
    lr = tpex.parse_daily_quotes(legacy, DAY).records[0]
    assert (lr.close, lr.change, lr.volume, lr.next_ref_price) == (D("400.00"), D("5.00"), 1234000, D("400.00"))
    empty = tpex.parse_daily_quotes({"tables": [{"fields": ["代號"], "data": []}]}, date(2024, 1, 6))
    assert empty.is_trading_day is False


def test_tpex_institutional_margin_pe():
    m = Market(DAY, days=3)
    r = tpex.parse_institutional(m.tpex_insti(DAY), DAY).records[0]
    assert (r.foreign_net, r.trust_net, r.dealer_self_net, r.dealer_hedge_net, r.dealer_net, r.total_net) == \
        (500, 200, 40, -10, 30, 730)
    mg = tpex.parse_margin(m.tpex_margin(DAY), DAY).records[0]
    assert (mg.margin_balance_prev, mg.margin_buy, mg.margin_balance, mg.short_sell, mg.short_balance, mg.offset) == \
        (1000, 100, 1049, 10, 35, 2)
    pe = tpex.parse_valuation(m.tpex_pe(DAY), DAY).records[0]
    assert pe.pe_ratio == D("20.10") and pe.dividend_yield == D("1.80") and pe.pb_ratio == D("2.50")


ISIN_HTML = """<html><body><table class='h4'>
<tr><td>有價證券代號及名稱 </td><td>國際證券辨識號碼(ISIN Code)</td><td>上市日</td><td>市場別</td><td>產業別</td><td>CFICode</td><td>備註</td></tr>
<tr><td bgcolor=#FAFAD2 colspan=7 ><B> 股票 <B> </td></tr>
<tr><td bgcolor=#FAFAD2>1101　台泥</td><td bgcolor=#FAFAD2>TW0001101004</td><td bgcolor=#FAFAD2>1962/02/09</td><td bgcolor=#FAFAD2>上市</td><td bgcolor=#FAFAD2>水泥工業</td><td bgcolor=#FAFAD2>ESVUFR</td><td bgcolor=#FAFAD2></td></tr>
<tr><td bgcolor=#FAFAD2>2330　台積電</td><td bgcolor=#FAFAD2>TW0002330008</td><td bgcolor=#FAFAD2>1994/09/05</td><td bgcolor=#FAFAD2>上市</td><td bgcolor=#FAFAD2>半導體業</td><td bgcolor=#FAFAD2>ESVUFR</td><td bgcolor=#FAFAD2></td></tr>
<tr><td bgcolor=#FAFAD2 colspan=7 ><B> ETF <B> </td></tr>
<tr><td bgcolor=#FAFAD2>0050　元大台灣50</td><td bgcolor=#FAFAD2>TW0000050004</td><td bgcolor=#FAFAD2>2003/06/30</td><td bgcolor=#FAFAD2>上市</td><td bgcolor=#FAFAD2></td><td bgcolor=#FAFAD2>CEOGEU</td><td bgcolor=#FAFAD2></td></tr>
</table></body></html>"""


def test_isin_parse_big5():
    raw = ISIN_HTML.encode("cp950")
    res = isin.parse(isin.decode(raw), "TWSE")
    by = {r.code: r for r in res.records}
    assert set(by) == {"1101", "2330", "0050"}
    assert by["2330"].name == "台積電" and by["2330"].isin == "TW0002330008"
    assert by["2330"].listed_date == date(1994, 9, 5) and by["2330"].industry == "半導體業"
    assert by["2330"].security_type == "STOCK" and by["0050"].security_type == "ETF"


def test_mops_revenue_income_balance():
    rev = mops.parse_revenue_openapi([{
        "出表日期": "1131011", "資料年月": "11309", "公司代號": "2330", "公司名稱": "台積電", "產業別": "半導體業",
        "營業收入-當月營收": "251872717", "營業收入-上月營收": "250866368", "營業收入-去年當月營收": "180430282",
        "營業收入-上月比較增減(%)": "0.40", "營業收入-去年同月增減(%)": "39.59",
        "累計營業收入-當月累計營收": "2025846521", "累計營業收入-去年累計營收": "1536206988",
        "累計營業收入-前期比較增減(%)": "31.87", "備註": "-"}]).records[0]
    assert rev.period == date(2024, 9, 1) and rev.published_date == date(2024, 10, 11)
    assert rev.revenue == 251872717 and rev.yoy_pct == D("39.59") and rev.cum_yoy_pct == D("31.87")
    inc = mops.parse_income_openapi([{
        "出表日期": "1131115", "年度": "113", "季別": "3", "公司代號": "9999", "公司名稱": "測試",
        "營業收入": "1000", "營業成本": "600", "營業毛利（毛損）": "400", "未實現銷貨（損）益": "0",
        "已實現銷貨（損）益": "0", "營業毛利（毛損）淨額": "390", "營業費用": "100", "營業利益（損失）": "290",
        "營業外收入及支出": "10", "本期淨利（淨損）": "250", "淨利（淨損）歸屬於母公司業主": "240",
        "基本每股盈餘（元）": "2.50"}]).records[0]
    assert (inc.fiscal_year, inc.fiscal_quarter) == (2024, 3)
    assert (inc.revenue, inc.gross_profit, inc.operating_income, inc.net_income, inc.eps) == (1000, 390, 290, 250, D("2.50"))
    bal = mops.parse_balance_openapi([{
        "出表日期": "1131115", "年度": "113", "季別": "3", "公司代號": "9999", "公司名稱": "測試",
        "資產總額": "5000", "負債總額": "2000", "權益總額": "3000", "歸屬於母公司業主之權益合計": "2900"}]).records[0]
    assert (bal.total_assets, bal.total_liabilities, bal.equity) == (5000, 2000, 3000)


def test_mops_revenue_history_html():
    html = """<table><tr><th>公司代號</th><th>公司名稱</th><th>當月營收</th></tr>
    <tr align=right><td align=center>1101</td><td align=left>台泥</td><td>10,000</td><td>9,000</td><td>8,000</td>
    <td>11.11</td><td>25.00</td><td>100,000</td><td>90,000</td><td>11.11</td><td>-</td></tr></table>"""
    r = mops.parse_revenue_history_html(html, 2023, 5).records
    assert len(r) == 1 and r[0].period == date(2023, 5, 1) and r[0].revenue == 10000 and r[0].published_date is None


def test_mis_intraday_delay_is_measured():
    t = datetime(2024, 1, 2, 2, 0, 0, tzinfo=timezone.utc)
    payload = {"msgArray": [{"c": "2330", "ex": "tse", "z": "580.0000", "tlong": str(int(t.timestamp() * 1000)),
                             "v": "12345", "b": "579.0000_578.0000_", "a": "580.0000_581.0000_"}]}
    r = twse_mis.parse(payload, fetched_at=t.replace(second=20)).records[0]
    assert r.price == D("580") and r.best_bid == D("579") and r.best_ask == D("580")
    assert r.delay_seconds == 20.0
