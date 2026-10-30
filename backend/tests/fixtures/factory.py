"""SYNTHETIC test payloads that mirror the documented TWSE/TPEx/MOPS response layouts.

These are NOT market data. Prices are a seeded random walk; codes are real so the code-type
classification paths are exercised. They exist only so the pipeline can be tested offline.
"""
from datetime import date, timedelta

import numpy as np

FIXTURE_NOTE = "SYNTHETIC TEST FIXTURE - not market data"

TWSE_STOCKS = [("2330", "台積電", 500.0), ("2317", "鴻海", 100.0), ("1101", "台泥", 33.0),
               ("2603", "長榮", 150.0), ("0050", "元大台灣50", 130.0), ("9999", "低量股", 12.0)]
TPEX_STOCKS = [("6488", "環球晶", 400.0), ("5347", "世界", 80.0), ("8069", "元太", 200.0)]
HOLIDAYS = set()

QUOTE_FIELDS = ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價", "收盤價",
                "漲跌(+/-)", "漲跌價差", "最後揭示買價", "最後揭示買量", "最後揭示賣價", "最後揭示賣量", "本益比"]
INDEX_FIELDS = ["指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"]
TPEX_FIELDS = ["代號", "名稱", "收盤 ", "漲跌", "開盤 ", "最高 ", "最低", "均價 ", "成交股數  ", "成交金額(元)",
               "成交筆數 ", "最後買價", "最後買量(張數)", "最後賣價", "最後賣量(張數)", "發行股數 ", "次日參考價 ",
               "次日漲停價 ", "次日跌停價"]
T86_FIELDS = ["證券代號", "證券名稱", "外陸資買進股數(不含外資自營商)", "外陸資賣出股數(不含外資自營商)",
              "外陸資買賣超股數(不含外資自營商)", "外資自營商買進股數", "外資自營商賣出股數", "外資自營商買賣超股數",
              "投信買進股數", "投信賣出股數", "投信買賣超股數", "自營商買賣超股數", "自營商買進股數(自行買賣)",
              "自營商賣出股數(自行買賣)", "自營商買賣超股數(自行買賣)", "自營商買進股數(避險)", "自營商賣出股數(避險)",
              "自營商買賣超股數(避險)", "三大法人買賣超股數"]


def fmt(x: float, nd: int = 2) -> str:
    return f"{x:,.{nd}f}"


class Market:
    """Deterministic synthetic market over a date range."""

    def __init__(self, start: date, days: int = 120, seed: int = 7, ex_div: tuple[str, int, float] | None = None):
        rng = np.random.default_rng(seed)
        self.dates = []
        d = start
        while len(self.dates) < days:
            if d.weekday() < 5 and d not in HOLIDAYS:
                self.dates.append(d)
            d += timedelta(days=1)
        self.bars: dict[tuple[str, str], dict[date, dict]] = {}
        self.ex_div = ex_div  # (code, day index, cash dividend)
        for market, lst in (("TWSE", TWSE_STOCKS), ("TPEX", TPEX_STOCKS)):
            for code, name, p0 in lst:
                p = p0
                bars = {}
                for i, dd in enumerate(self.dates):
                    prev = p
                    if ex_div and ex_div[0] == code and ex_div[1] == i:
                        prev = p - ex_div[2]
                    p = round(max(1.0, prev * (1 + rng.normal(0.0005, 0.015))), 2)
                    p = min(max(p, prev * 0.905), prev * 1.095)
                    p = round(p, 2)
                    hi = round(max(p, prev) * (1 + abs(rng.normal(0, 0.004))), 2)
                    lo = round(min(p, prev) * (1 - abs(rng.normal(0, 0.004))), 2)
                    op = round(min(max(prev * (1 + rng.normal(0, 0.003)), lo), hi), 2)
                    vol = int((5_000 if code == "9999" else 3_000_000) * (1 + abs(rng.normal(0, 0.5))))
                    bars[dd] = dict(name=name, open=op, high=hi, low=lo, close=p, prev=prev, volume=vol,
                                    turnover=int(vol * p))
                self.bars[(market, code)] = bars

    # ---------------------------------------------------------------- TWSE payloads
    def twse_mi_index(self, d: date) -> dict:
        if d not in self.dates:
            return {"stat": "很抱歉，沒有符合條件的資料!"}
        rows = []
        for (m, code), bars in self.bars.items():
            if m != "TWSE":
                continue
            b = bars[d]
            chg = b["close"] - b["prev"]
            sign = "<p style= color:red>+</p>" if chg > 0 else ("<p style= color:green>-</p>" if chg < 0 else " ")
            rows.append([code, b["name"], fmt(b["volume"], 0), "1,234", fmt(b["turnover"], 0), fmt(b["open"]),
                         fmt(b["high"]), fmt(b["low"]), fmt(b["close"]), sign, fmt(abs(chg)), fmt(b["close"]), "10",
                         fmt(b["close"]), "5", "15.20"])
        # a suspended security: no trade, "--" everywhere
        rows.append(["1234", "停牌股", "0", "0", "0", "--", "--", "--", "--", " ", "0.00", "--", "0", "--", "0", "0.00"])
        return {"_fixture_note": FIXTURE_NOTE, "stat": "OK", "date": d.strftime("%Y%m%d"), "tables": [
            {"title": "價格指數(臺灣證券交易所)", "fields": INDEX_FIELDS,
             "data": [["發行量加權股價指數", "17,853.76", "<p style= color:red>+</p>", "123.45", "0.70", ""],
                      ["電子類指數", "900.12", "<p style= color:green>-</p>", "3.21", "0.36", ""]]},
            {"title": "每日收盤行情(全部(不含權證、牛熊證))", "fields": QUOTE_FIELDS, "data": rows},
        ]}

    def twse_t86(self, d: date) -> dict:
        if d not in self.dates:
            return {"stat": "很抱歉，沒有符合條件的資料!"}
        rows = []
        for (m, code), bars in self.bars.items():
            if m != "TWSE":
                continue
            fb, fs, fdn, tb, ts, dsn, dhn = 1_000_000, 800_000, 1_000, 50_000, 20_000, 3_000, -2_000
            fn, tn, dn = fb - fs, tb - ts, dsn + dhn
            rows.append([code, bars[d]["name"], fmt(fb, 0), fmt(fs, 0), fmt(fn, 0), "1,000", "0", fmt(fdn, 0),
                         fmt(tb, 0), fmt(ts, 0), fmt(tn, 0), fmt(dn, 0), "5,000", "2,000", fmt(dsn, 0),
                         "1,000", "3,000", fmt(dhn, 0), fmt(fn + fdn + tn + dn, 0)])
        return {"_fixture_note": FIXTURE_NOTE, "stat": "OK", "date": d.strftime("%Y%m%d"),
                "fields": T86_FIELDS, "data": rows}

    def twse_margin(self, d: date) -> dict:
        if d not in self.dates:
            return {"stat": "很抱歉，沒有符合條件的資料!"}
        f = ["代號", "名稱", "買進", "賣出", "現金償還", "前日餘額", "今日餘額", "次一營業日限額", "買進", "賣出",
             "現券償還", "前日餘額", "今日餘額", "次一營業日限額", "資券互抵", "註記"]
        rows = [[c, b[d]["name"], "100", "50", "1", "1,000", "1,049", "250,000", "5", "10", "0", "30", "35",
                 "250,000", "2", ""] for (m, c), b in self.bars.items() if m == "TWSE"]
        return {"_fixture_note": FIXTURE_NOTE, "stat": "OK", "date": d.strftime("%Y%m%d"), "tables": [
            {"title": "信用交易統計", "fields": ["項目", "買進", "賣出"], "data": [["融資(交易單位)", "1", "2"]]},
            {"title": "融資融券彙總", "fields": f, "data": rows}]}

    def twse_bwibbu(self, d: date) -> dict:
        if d not in self.dates:
            return {"stat": "很抱歉，沒有符合條件的資料!"}
        f = ["證券代號", "證券名稱", "收盤價", "殖利率(%)", "股利年度", "本益比", "股價淨值比", "財報年/季"]
        rows = [[c, b[d]["name"], fmt(b[d]["close"]), "2.10", "113", "18.50" if c != "1101" else "-", "3.20",
                 "113/2"] for (m, c), b in self.bars.items() if m == "TWSE"]
        return {"_fixture_note": FIXTURE_NOTE, "stat": "OK", "date": d.strftime("%Y%m%d"),
                "tables": [{"fields": f, "data": rows}]}

    def twse_twt49u(self, start: date, end: date) -> dict:
        f = ["資料日期", "股票代號", "股票名稱", "除權息前收盤價", "除權息參考價", "權值+息值", "權/息", "漲停價格",
             "跌停價格", "開盤競價基準", "減除股利參考價", "詳細資料", "最近一次申報資料 季別/日期",
             "最近一次申報每股 (單位)淨值", "最近一次申報每股 (單位)盈餘"]
        rows = []
        if self.ex_div:
            code, idx, div = self.ex_div
            d = self.dates[idx]
            if start <= d <= end:
                prev_close = self.bars[("TWSE", code)][self.dates[idx - 1]]["close"]
                roc = f"{d.year - 1911}年{d.month:02d}月{d.day:02d}日"
                rows.append([roc, code, "x", fmt(prev_close), fmt(prev_close - div), fmt(div), "息", "", "", "",
                             "", "", "", "", ""])
        return {"_fixture_note": FIXTURE_NOTE, "stat": "OK", "fields": f, "data": rows}

    # ---------------------------------------------------------------- TPEx payloads
    def tpex_daily(self, d: date) -> dict:
        rows = []
        if d in self.dates:
            for (m, code), bars in self.bars.items():
                if m != "TPEX":
                    continue
                b = bars[d]
                rows.append([code, b["name"], fmt(b["close"]), f"{b['close'] - b['prev']:+.2f}", fmt(b["open"]),
                             fmt(b["high"]), fmt(b["low"]), fmt(b["close"]), fmt(b["volume"], 0),
                             fmt(b["turnover"], 0), "999", fmt(b["close"]), "3", fmt(b["close"]), "4",
                             "1,000,000,000", fmt(b["close"]), "0.00", "0.00"])
            rows.append(["TOTAL", "合計", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""])
        return {"_fixture_note": FIXTURE_NOTE, "date": d.strftime("%Y%m%d"), "stat": "ok",
                "tables": [{"title": "上櫃股票行情", "fields": TPEX_FIELDS, "data": rows}]}

    def tpex_insti(self, d: date) -> dict:
        rows = []
        if d in self.dates:
            for (m, code), bars in self.bars.items():
                if m != "TPEX":
                    continue
                rows.append([code, bars[d]["name"], "1,000", "500", "500", "0", "0", "0", "1,000", "500", "500",
                             "300", "100", "200", "50", "10", "40", "20", "30", "-10", "70", "40", "30", "730"])
        fields = ["代號", "名稱"] + ["買進股數", "賣出股數", "買賣超股數"] * 7 + ["三大法人買賣超股數合計"]
        return {"_fixture_note": FIXTURE_NOTE, "date": d.strftime("%Y%m%d"), "stat": "ok",
                "tables": [{"fields": fields, "data": rows}]}

    def tpex_margin(self, d: date) -> dict:
        rows = []
        if d in self.dates:
            rows = [[c, b[d]["name"], "1,000", "100", "50", "1", "1,049", "0", "1.2", "100,000", "30", "10", "5",
                     "0", "35", "0", "0.1", "100,000", "2", ""] for (m, c), b in self.bars.items() if m == "TPEX"]
        return {"_fixture_note": FIXTURE_NOTE, "date": d.strftime("%Y%m%d"), "stat": "ok",
                "tables": [{"fields": [f"c{i}" for i in range(20)], "data": rows}]}

    def tpex_pe(self, d: date) -> dict:
        rows = []
        if d in self.dates:
            rows = [[c, b[d]["name"], "20.10", "3.00", "113", "1.80", "2.50"] for (m, c), b in self.bars.items()
                    if m == "TPEX"]
        return {"_fixture_note": FIXTURE_NOTE, "date": d.strftime("%Y%m%d"), "stat": "ok",
                "tables": [{"fields": ["股票代號", "名稱", "本益比", "每股股利", "股利年度", "殖利率(%)", "股價淨值比"],
                            "data": rows}]}
