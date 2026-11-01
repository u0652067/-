# CHANGELOG

## [0.1.4] — 2026-11-01 — 本機部署實際執行
- 安裝 Git 2.56、Docker Desktop 4.97（Engine 29.9.2）；本機資料夾接回 GitHub（分支 `claude/local-deploy-phase1`，基於 `claude/project-thread-fe2thb` d2e0123）。
- 修正 `.gitignore`：`data/` 會誤忽略 `backend/aqt/data/`，改為只忽略根目錄 `/data/`、`/backups/`；`log_event` 的 WARN 改用 `logging.warning`。
- 實際執行（證據 `evidence/2026-11-01_local_deploy/`）：測試 97 passed；健康檢查 28/28 OK；ISIN 證券主檔同步成功（上市 41,119、上櫃 13,723 筆，含 ISIN 碼）；`stop.ps1 -Remove` 後重建資料一致；`backup.ps1 -Verify` 與 `restore.ps1` 還原比對一致；smoke 回補 2019-01-02～01-11 完整（行情/法人/融資券/本益比/月營收 0 缺口）；pre_backfill 驗證備份（8.1 MB）與完整性基準報告後，啟動 2019-01-01～2026-10-31 全量回補（2,044 個平日）。
- PHASE 1 仍為條件式驗收；未進入 PHASE 2。

## [0.1.3] — 2026-10-31 — 本機持久化部署與 PHASE 1 收尾（條件式驗收）

### 1. 完成項目
- 長期主機決定為使用者 Windows 電腦（台灣家用 IP）。雲端資料庫不再視為正式資料。
- `docker-compose.yml`：專案名 `aqt`；PostgreSQL 用 external volume `aqt_pgdata`（`down -v` 也不會刪）；原始回應副本 `data/raw`、日誌 `data/logs`、報告 `data/reports`、備份 `backups` 綁定到專案資料夾；`backfill`（可續傳、`restart: on-failure`）與 `test`（tmpfs 拋棄式資料庫）兩個 profile。
- `scripts/`：`start.ps1`（首次自動產生 `.env` 隨機密碼、建立 volume、等 API 就緒）、`stop.ps1`、`backup.ps1`（pg_dump + sha256 + 筆數清單，`-Verify` 還原比對）、`restore.ps1`（sha256 檢查、先做安全備份、需 `-Force`）、`health.ps1`、`backfill.ps1`（smoke / start / status / stop / report / logs）、`test.ps1`。
- 新指令：`health-check`（逐端點傳輸層證據，不重試）、`backfill-full`（進度存 `system_state`、中斷續傳、被擋冷卻重試、ETA、MOPS 歷史月營收階段、分季重算指標）、`backfill-status`、`integrity-check`（JSON + Markdown）。
- 原始回應除 DB 外另存 `.gz` 檔（檔名含日期、raw_id、sha256 前 12 碼）；CLI 每個指令寫輪替日誌檔；`SIGTERM` 轉為 `SystemExit` 以保存進度。
- README 改寫為本機部署手冊；TASKS 列出 PHASE 1 條件式驗收與資料缺口 G1–G12；TEST_PLAN 新增本機驗收條件 A1–A8。

### 2. 實際執行證據（家用 IP，2026-10-31 13:55–13:58 台北）
- MOPS 歷史月營收 `mopsov.twse.com.tw/nas/t21/{sii,otc}/t21sc03_*`：2019-01、2021-06、2024-12、2026-09 上市/上櫃共 8 頁全部 HTTP 200、無「FOR SECURITY REASONS」，本專案解析器分別解析 889/918/988/997（上市）、748/780/859/867（上櫃）家；原始頁 gzip 與 `mops_probe.json`（sha256、延遲）存於 `evidence/2026-10-31_home_ip/`。
- 同一網路下 TWSE MI_INDEX/STOCK_DAY、T86 2019-01-02、MI_MARGN 2019-01-02、TPEx、OpenAPI 公司清單、ISIN 一覽表（9.8 MB）皆 HTTP 200。

### 3. 測試結果
- 新增 `tests/test_local_ops.py` 9 項；Python 與 PowerShell 檔案語法檢查通過。**本機尚無 Docker/PostgreSQL，完整測試尚未執行**（TEST_PLAN A1）。

### 4. 尚未完成 / 發現的問題
- 本機未安裝 Docker Desktop、WSL2、Git → 容器、回補、備份尚未實際執行；本次變更尚未 commit/push。
- 資料缺口見 TASKS.md「尚未解決的資料缺口」G1–G12。

### 5. 下一步
- 使用者安裝 Docker Desktop 後依 TASKS 下一步 6–7 執行；**不進入 PHASE 2，等待使用者驗收。**

## [0.1.2] — 2026-10-31 — PHASE 1 真實來源驗收

### 1. 完成項目
- `check-sources` 對真實官方端點執行；上市/上櫃每日行情、三大法人、融資融券、本益比、TWSE 除權息、休市日、公司清單、月營收、財報全部解析成功並逐欄人工比對。
- 修正：TPEx 日行情端點改為 `/afterTrading/otc`（舊 `dailyQ` 被導向錯誤頁）；TPEx 財報 OpenAPI 混用英文欄位（SecuritiesCompanyCode/Year/Season/Date）；ISIN 頁回「FOR SECURITY REASONS」時視為錯誤，改用 OpenAPI 公司清單備援（不會清掉既有 ISIN、不會把 ETF 判成下市）；HTTP 未解決的 3xx 改為可重試。
- TWSE 安全警示頁（307/200 + FOR SECURITY REASONS）一律視為被拒、不重試；同主機節流 3→5 秒；`backfill --only-missing` 只補尚未入庫的資料集，回補摘要列出 `missing`。
- 新增真實回應 fixtures（`tests/fixtures/real/`，只保留少數列）與 12 個測試；check-sources 加入 TPEx 財報、公司清單、歷史月營收。
- 實際寫入資料庫：證券主檔（上市 1099、上櫃 897 家公司）、2026 休市日、最新月營收與財報、2026-10-26～30 行情（上市約 1389、上櫃約 1018 檔/日）與指標。

### 2. 測試結果
- `pytest`：88 passed。

### 3. 尚未完成
- 2019 起完整回補在雲端 session 背景執行中，容器結束後資料不保留。
- 歷史月營收（mopsov.twse.com.tw 被擋）、MIS 盤中（502）、ISIN 一覽表（被擋）。

### 4. 發現的問題
- TWSE 防火牆對雲端 IP 間歇封鎖部分端點（法人、融資券、除權息的歷史日期最明顯）。
- TWSE 休市日 API 只回當年度；過去年度回安全警示頁。
- 真實資料有「有量無收盤價」（只有盤後定價成交）的情況，品質檢查會標記並排除當日交易。

### 5. 下一階段
- 長期主機部署與完整回補；PHASE 2 回測引擎。

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
