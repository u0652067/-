# 資料來源

全部為交易所／主管機關公開資料，免費、合法；交易所會封鎖過度頻繁的請求，系統每主機至少間隔 3 秒。

| 資料 | 來源 | 端點 | 公布時間（available_at） | 備註 |
|---|---|---|---|---|
| 上市日行情 + 指數 | TWSE | `/rwd/zh/afterTrading/MI_INDEX?type=ALLBUT0999` | 14:30 | 新 `tables` 與舊 `fieldsN/dataN` 格式皆支援 |
| 上櫃日行情 | TPEx | `/www/zh-tw/afterTrading/otc?type=EW`（2026-10 驗證；`dailyQ` 已導向 /errors） | 14:30 | 新站 `tables`、舊站 `aaData`；2019 起可查 |
| 上市三大法人 | TWSE | `/rwd/zh/fund/T86` | 16:30 | |
| 上櫃三大法人 | TPEx | `/www/zh-tw/insti/dailyTrade` | 16:30 | 24 欄固定順序 |
| 上市融資融券 | TWSE | `/rwd/zh/marginTrading/MI_MARGN` | 21:30 | 單位：張 |
| 上櫃融資融券 | TPEx | `/www/zh-tw/margin/balance` | 21:30 | |
| 本益比/殖利率/淨值比 | TWSE / TPEx | `BWIBBU_d` / `peQryDate` | 17:30 | |
| 除權息 | TWSE | `/rwd/zh/exRight/TWT49U` | 除權息日 08:00 | 上櫃見下方 TPEx exDailyQ |
| 休市日 | TWSE | `/rwd/zh/holidaySchedule/holidaySchedule` | — | 實際有無行情優先；只能查當年度，查過去年度回安全警示頁（307） |
| 證券主檔 | TWSE ISIN | `isin.twse.com.tw/isin/C_public.jsp?strMode=2/4` | — | Big5 HTML；雲端 IP 會收到「FOR SECURITY REASONS」頁 → 視為錯誤 |
| 證券主檔（備援） | TWSE/TPEx OpenAPI | `openapi.twse.com.tw/v1/opendata/t187ap03_L`、`tpex.org.tw/openapi/v1/mopsfin_t187ap03_O` | — | 只有公司（普通股），不含 ETF/特別股；只用來判定普通股下市 |
| 月營收（最新） | MOPS（TWSE/TPEx OpenAPI） | `t187ap05_L`、`mopsfin_t187ap05_O` | 出表日隔天；未知則次月 11 日 | |
| 月營收（歷史） | MOPS | `mopsov.twse.com.tw/nas/t21/{sii,otc}/t21sc03_{民國年}_{月}_0.html` | 次月 11 日（保守） | |
| 財報（最新季） | MOPS OpenAPI | `t187ap06_*_ci`、`t187ap07_*_ci` | 出表日隔天；未知則法定期限隔天 | 一般業；金融業模板待補 |
| 上櫃除權息（G4） | TPEx | `/www/zh-tw/bulletin/exDailyQ?startDate=YYYY/MM/DD&endDate=…`（除權除息計算結果表） | 除權息日 08:00 | 2019 起可查；含現金增資除權 |
| 減資（G8） | TWSE / TPEx | `/rwd/zh/reducation/TWTAUU`、`/www/zh-tw/bulletin/revivt` | 恢復買賣日 08:00 | 參考價 = 交易所公布之恢復買賣參考價 |
| 變更面額（G8） | TWSE / TPEx | `/rwd/zh/change/TWTB8U`、`/www/zh-tw/bulletin/pvChgRslt` | 恢復買賣日 08:00 | 參考價 = 停止買賣前收盤 / 換股率 |
| ETF 分割／反分割（G8） | TWSE | `/rwd/zh/split/TWTCAU` | 恢復買賣日 08:00 | TPEx ETF 分割未接（ETF 不在股票池） |
| 處置股（G6） | TWSE / TPEx | `/rwd/zh/announcement/punish`、`/www/zh-tw/bulletin/disposal`（區間） | 公布日 18:00，隔日生效 | 含處置起迄日 |
| 變更交易／全額交割（G6） | TWSE | `/rwd/zh/fullDelivery/TWT85U?date=`（每日清單，93 年起） | 當日 08:00 | `**` = 分盤集合競價 |
| 變更交易、分盤、管理股票、停止交易（G6） | TPEx | `/www/zh-tw/afterTrading/chtm?date=`（每日清單） | 當日 08:00 | |
| 暫停交易（G6） | TWSE | `/rwd/zh/afterTrading/TWTAWU`（區間） | 暫停日 08:00 | 上櫃以 chtm「停止交易」涵蓋 |
| 歷史財報（G7） | MOPS | `mopsov.twse.com.tw/mops/web/ajax_t163sb04`（損益）、`ajax_t163sb05`（資產負債），POST `TYPEK=sii/otc&year=民國&season=01..04` | 見下一列 | 各產業模板（一般、銀行、金控、證券、保險）同頁；累計值、仟元 |
| 財報實際公告時間（G7） | TWSE 電子文件 | `doc.twse.com.tw/server-java/t57sb01?step=1&co_id=…&year=民國&mtype=A` | **該季財報 PDF 最早上傳時間（精確到秒）** | 每公司每年 1 次查詢；查不到者 `pit_reliable=false`，不得用於基本面回測 |
| 個股月成交（獨立驗證用） | TWSE / TPEx | `/rwd/zh/afterTrading/STOCK_DAY`、`/www/zh-tw/afterTrading/tradingStock` | — | G13 抽驗：與全市場日報不同的報表 |
| 盤中快照 | TWSE MIS | `mis.twse.com.tw/stock/api/getStockInfo.jsp` | 量測延遲 | **非授權即時行情**，只作延遲參考 |

## 可交易性（G6）與還原（G4/G8）的使用規則
- 所有選股、訊號、回測、模擬交易**只能**從 `build_universe()`（內部呼叫 `tradability.blocked_securities()`）取得候選股票，不得直接讀行情表選股。
- 排除原因：處置、變更交易／全額交割、分盤、管理股票、停止交易、暫停交易、近 120 天內有未解釋價格異常（G13 `blocks_backtest`）。
- 還原因子 = 交易所參考價 / 前收盤，對除權息、減資、變更面額、分割一體適用（`recompute_adj_factors`）。
- 基本面只能透過 `fundamentals.latest_statements_as_of()` 讀取：只回傳 `pit_reliable=true` 且 `available_at <= 決策時間` 的財報。

## 即時行情
系統目前**沒有**合法授權的即時行情。若要讓盤中策略（隔日沖、盤中趨勢）的模擬更接近真實，選項：
1. 券商 API 的行情（多數只需開戶、不需下單權限；仍須遵守其使用條款）。
2. 付費資料商（授權即時或延遲行情）。
這需要使用者決定並提供帳號／金鑰；在那之前，盤中模擬以延遲快照 + 保守成交假設進行，並在網站標示延遲。

## 備援
FinMind（`api.finmindtrade.com`）提供多數相同資料集，可作為主要來源中斷時的備援與歷史財報回補；adapter 尚未實作（TASKS）。
