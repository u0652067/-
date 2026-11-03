# A7 真實 Docker + PostgreSQL 中斷續傳測試（2026-11-02，台灣家用 IP）

環境：Docker Engine 29.9.2、postgres:16；獨立暫存資料庫 `aqt_a7`（同一 PostgreSQL 容器，不影響正式資料庫 `aqt`）。
指令：`backfill-full --start 2019-01-02 --end 2019-01-31 --phases daily --state-key a7_interrupt_test`（容器 `aqt-a7`）。

## 過程
| 時間（台北） | 事件 |
|---|---|
| 22:16:03 | 啟動容器 |
| 22:19:42 | days_done = 8 → `docker stop -t 60 aqt-a7`（SIGTERM） |
| 22:19:43 | 容器停止（1 秒內）；進度紀錄 status=INTERRUPTED、current_day=2019-01-11、days_done=8、interrupted_reason="SystemExit: signal 15" |
| 22:19:43 | 停止時資料：raw_payloads 67 筆、daily_prices 16,894 筆 |
| 22:19:55 | `docker start aqt-a7`（同一指令重跑） |
| 22:25:54 | status=DONE，days_done=22/22，2019-01 月份 21 個交易日、missing={} |

## 續傳證據
- job_runs：中斷前 2019-01-02～01-11 每日約 26 秒（實際抓取）；續傳後同樣 8 天各 < 0.2 秒（`only_missing` 全部跳過、未重抓），2019-01-14（中斷當下處理中的日子）補齊缺的資料集，其後正常。中斷時正在處理的 job（id 9, 2019-01-14）保留為 RUNNING，未被改寫。
- raw_payloads：171 筆、170 個 (dataset, 日期)。唯一重複是 2019-01-14 T86：中斷前已存檔原始回應但尚未寫入資料表，續傳時重抓，兩次 sha256 相同（2c087a1a…）。

## 與正式資料庫比對（同期間）
| 項目 | 結果 |
|---|---|
| daily_prices 每日筆數與收盤價總和（21 天） | **完全一致** |
| institutional_trades 每日每市場筆數、合計買賣超（42 個市場日） | **完全一致** |
| 原始回應 sha256（8 種資料集） | 171 筆中 161 筆與正式庫逐位元組相同；10 筆 TWSE T86 雜湊不同，經解壓比對：906 列內容完全相同，只有**列的排列順序**不同（TWSE 每次回傳順序不固定），解析後資料一致 |
