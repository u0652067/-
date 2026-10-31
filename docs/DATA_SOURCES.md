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
| 除權息 | TWSE | `/rwd/zh/exRight/TWT49U` | 除權息日 08:00 | 上櫃尚未接 |
| 休市日 | TWSE | `/rwd/zh/holidaySchedule/holidaySchedule` | — | 實際有無行情優先；只能查當年度，查過去年度回安全警示頁（307） |
| 證券主檔 | TWSE ISIN | `isin.twse.com.tw/isin/C_public.jsp?strMode=2/4` | — | Big5 HTML；雲端 IP 會收到「FOR SECURITY REASONS」頁 → 視為錯誤 |
| 證券主檔（備援） | TWSE/TPEx OpenAPI | `openapi.twse.com.tw/v1/opendata/t187ap03_L`、`tpex.org.tw/openapi/v1/mopsfin_t187ap03_O` | — | 只有公司（普通股），不含 ETF/特別股；只用來判定普通股下市 |
| 月營收（最新） | MOPS（TWSE/TPEx OpenAPI） | `t187ap05_L`、`mopsfin_t187ap05_O` | 出表日隔天；未知則次月 11 日 | |
| 月營收（歷史） | MOPS | `mopsov.twse.com.tw/nas/t21/{sii,otc}/t21sc03_{民國年}_{月}_0.html` | 次月 11 日（保守） | |
| 財報（最新季） | MOPS OpenAPI | `t187ap06_*_ci`、`t187ap07_*_ci` | 出表日隔天；未知則法定期限隔天 | 一般業；金融業模板待補 |
| 盤中快照 | TWSE MIS | `mis.twse.com.tw/stock/api/getStockInfo.jsp` | 量測延遲 | **非授權即時行情**，只作延遲參考 |

## 即時行情
系統目前**沒有**合法授權的即時行情。若要讓盤中策略（隔日沖、盤中趨勢）的模擬更接近真實，選項：
1. 券商 API 的行情（多數只需開戶、不需下單權限；仍須遵守其使用條款）。
2. 付費資料商（授權即時或延遲行情）。
這需要使用者決定並提供帳號／金鑰；在那之前，盤中模擬以延遲快照 + 保守成交假設進行，並在網站標示延遲。

## 備援
FinMind（`api.finmindtrade.com`）提供多數相同資料集，可作為主要來源中斷時的備援與歷史財報回補；adapter 尚未實作（TASKS）。
