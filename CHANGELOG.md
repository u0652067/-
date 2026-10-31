# CHANGELOG

## [0.1.1] — 2026-10-31 — 真實資料驗收嘗試（受阻）
- 在新雲端環境還原專案，測試基準 75 passed。
- `check-sources` 無法執行：6 個交易所網域仍被網路政策封鎖（見 TASKS.md 決策紀錄）。未產生任何資料，未修改解析器。
- GitHub 推送 403（Claude GitHub App 未安裝於 u0652067/-）。

## [0.1.0] — 2026-10-30 — PHASE 0 完成、PHASE 1 程式完成（待真實資料驗收）

### 1. 完成項目
- 專案骨架、規格文件（CLAUDE / PROJECT_SPEC / ARCHITECTURE / ROADMAP / TASKS / TEST_PLAN / CHANGELOG、docs/DATABASE、docs/DATA_SOURCES）。
- 完整 PostgreSQL schema（41 張表，PHASE 1–7 共用）與 2 個 Alembic migration；稽核觸發器：只能新增、雜湊鏈、策略版本不可改、委託理由不可改、原始回應不可改、模擬限定 CHECK。
- 資料來源與解析器：TWSE 行情/指數/法人/融資券/本益比/除權息/休市日、TPEx 行情/法人/融資券/本益比、ISIN 證券主檔、MOPS 月營收（最新＋歷史）與財報（最新一季）、TWSE MIS 延遲快照。
- 禮貌抓取（每主機 3 秒節流、重試、被封鎖不重試）與原始回應永久保存。
- Point-in-time `available_at` 規則；昨收、漲跌停、跳動點、手續費、證交稅規則。
- 技術指標（MA、KD、RSI、MACD、布林、ATR、量均、量比、區間高低）與還原權值。
- 資料品質檢查、安全模式、通知、可交易股票池（含排除原因）。
- 每日流程、歷史回補（依日期，處理倖存者偏差）、APScheduler 排程與重啟補跑。
- 唯讀 FastAPI、CLI、Dockerfile、docker-compose。

### 2. 實際測試結果
- `python -m pytest -q` → **75 passed**（PostgreSQL 16，本機）。
- `check-sources`（真實來源）→ 17 項全部 `SourceBlockedError: proxy refused connection (403)`：開發環境網路政策封鎖交易所網域，**不是程式錯誤，但也代表解析器尚未對真實回應驗證**。

### 3. 尚未完成
- 真實來源驗收與歷史回補（需可連線主機）。
- TPEx 除權息、櫃買指數、處置/全額交割名單、歷史財報與公布日、減資還原、官方下市日、FinMind 備援、Docker 實際建置。
- PHASE 2–7 全部。

### 4. 發現的問題（已修正）
- T86 欄位比對：`外陸資買進股數(不含外資自營商)` 本身含「自營商」，原本的排除條件會讓外資欄位全部變空值 → 改為不需排除的比對字串，並加測試。
- 回補時除權息資料先於行情寫入，還原因子沒有套用 → 回補結束後重新計算所有有除權息紀錄股票的還原因子，並加測試。
- RSI 參考值：StockCharts 範例四捨五入中間值，與精確值差約 0.07 → 測試改為精確值嚴格比對、範例值寬鬆比對。

### 5. 下一階段計畫
在可連線主機完成 PHASE 1 驗收 → PHASE 2（策略介面、回測引擎、成本模型、時間切分/樣本外/Walk-forward、績效統計、評分驗證）。
