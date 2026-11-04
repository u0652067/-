# CHANGELOG

## [0.4.0] — 2026-11-05 — PHASE 2 研究層（分支 `claude/phase2-research`，待審）
- 候選登錄、因子驗證（IC、分位、Holm）、市場環境分析、晉級規則；migration 0006（未套用到正式庫）。
- 登錄計畫在暫存複本執行：候選 `V1-bbe8a8a9` 未通過 BACKTESTED（全期淨 Sharpe -0.14），維持 CANDIDATE。不構成任何策略有效的宣稱。
- 測試 154 passed。

## [0.3.2] — 2026-11-04 — PHASE 2 第二輪審查修正（同分支 `claude/phase2-fixes`，PR #5 待審）
- #1 `blocked_securities()` 的 `known_at` 改為必填；決策用 T 18:00，開盤成交用 T+1 09:00，且只看當日生效的限制。修正前有 30 筆委託被當晚才公告的處置擋下（時間洩漏）。
- #2 migration 0005：`data_quality_issues.known_at/effective_at/detection_method`；`record_post_hoc_issue()`；只能新增的 `backtest_run_invalidations`；`run_validity()`；三欄納入資料指紋。
- #3 `PortfolioState.last_trading_day`；排程跨視窗延續；CARRY 視窗最後一天可決策；報告列出實際再平衡日。
- #4 `benchmark_comparable` 與 `markets`：含上櫃不與上市基準比較；撤回先前報告的相對基準解讀。
- 測試 138 passed；真實資料在正式庫的暫存複本驗證（正式庫維持 0003），17 筆回測紀錄寫入暫存庫。

## [0.3.1] — 2026-11-04 — PHASE 2 審查修正（分支 `claude/phase2-fixes`，待審；PR #4 不合併）
- PR #3 已合併：main `c799b88` → `7a89d6d`（PHASE 1 整合）。
- #1 成交量上限與滑價只用決策日以前的歷史均量／均值；成交日只讀開盤價與漲跌停。
- #2 `check_readiness()` fail closed：缺狀態、非明確 true、覆蓋不完整、品質未審都拒絕。
- #3 移除以事後資訊出場；無法交易持股凍結，並以 `usable_for_evaluation=false` 標示不可用。
- #4 `dividend_mode=TOTAL_RETURN_REINVEST`（唯一模式）；基準改為發行量加權股價報酬指數，價格指數只當參考、不可比較；寫入 `backtest_runs`。
- #5 Walk-forward 改為連續資金與持倉延續（`PortfolioState`、`terminal=CARRY/MARK`）。
- #6 `research/fingerprint.py`：按月逐列 sha256 manifest → `backtest_runs.data_manifest`／`data_version_hash`。
- #7 公司事件只在開盤前已公開時才套用；DataView 自行重建時點正確的還原價與指標。
- migration 0004（尚未發布、尚未套用）新增 dividend_mode、benchmark_index/kind、usable_for_evaluation、evaluation、data_manifest。
- 測試 128 passed；真實資料乾跑、樣本外、Walk-forward 已重跑（`evidence/2026-11-03_phase2/phase2_fix_dry_run_20261104T014143.json`）。

## [0.3.0] — 2026-11-03 — PHASE 2 技術面回測骨架（分支 `claude/phase2-backtest`，待驗收）
- 新增 `aqt/research/`：事件驅動日頻回測引擎、point-in-time DataView、台股成本模型、策略介面與範例（cash / 等權流動性基準 / 均線趨勢動能範例）、績效指標、時間切分、樣本外、Walk-forward、不可竄改紀錄。
- migration 0004：`backtest_runs`／`backtest_trades` 只能新增；新增 config、params_hash、data_version(_hash)、results_hash、engine_version、執行時間、免責聲明欄位。
- CLI：`backtest`、`evaluate --mode oos|walk-forward`；`python -m aqt.research.dry_run`（不寫紀錄）。
- 測試新增 16 項，全部 124 passed；真實資料乾跑（2024）三個策略兩次結果雜湊一致；technical_backtest_ready=false 時引擎拒絕執行。
- 限制：只做技術面（財報一律拒絕）；不做模擬交易、AI 決策、網站、實盤；回測結果不代表策略有效或可獲利。詳見 `docs/BACKTEST.md`。

## [0.2.1] — 2026-11-03 — PR #2 收尾
- G7 財報公告時間回補完成：15,374/15,374；54,729 筆可靠、618 筆查無公告時間（永久排除）。
- integrity 與 `/api/health` 改為分開呈現 `technical_backtest_ready`、`fundamental_backtest_ready`（不再有單一 backtest_ready）；狀態存於 `system_state.data_readiness`。
- 每日流程結束時同步分類已知品質問題（避免新日期出現未分類問題）。
- 最終驗證：測試 108 passed；integrity coverage COMPLETE、quality REVIEWED_WITH_EXCLUSIONS（未分類 0）、technical_backtest_ready=true、fundamental_backtest_ready=false；G13 抽驗 25/25 相符；健康檢查 27/28（MIS 盤後逾時）。

## [0.2.0] — 2026-11-03 — PHASE 1 驗收缺口修正（PR，待審；未進入 PHASE 2）
- **G4/G8** 新來源 `sources/corporate_events.py`：TPEx 除權息（exDailyQ）、上市/上櫃減資（TWTAUU / revivt）、上市/上櫃面額變更（TWTB8U / pvChgRslt）、上市 ETF 分割（TWTCAU）。還原因子＝參考價/前收，對所有事件一致；除權息日遇休市（颱風）套用到下一交易日。
- **G6** 新來源 `sources/restrictions.py` 與資料表 `trading_restrictions`：處置（區間）、變更交易/全額交割（每日）、分盤、管理股票、停止交易、暫停交易。`tradability.blocked_securities()` 由 `build_universe()` 呼叫；每日排程同步更新。
- **G7** `sources/mops_history.py`：MOPS 彙總損益表/資產負債表（各產業模板）＋證交所電子文件上傳時間；`financial_statements` 新增 `published_at`、`published_basis`、`pit_reliable`；`fundamentals.latest_statements_as_of()` 只回傳可靠公告時間且已公告者。
- **G13** `quality_review.py`：957 筆全數分類；未解釋者升為 ERROR 並阻擋回測 120 天；以個股月報表獨立抽驗。
- **integrity** 拆成「覆蓋完整度」「資料品質狀態」「backtest_ready」。
- migration 0003（`corporate_actions.action_type` 加長、`trading_restrictions`、財報公告欄位、品質分類欄位）；套用前做驗證備份 `pre_gap_fixes`。
- `Fetcher` 支援 POST（MOPS）；新增 CLI `review-quality`；`backfill-full` 新增 phases：corporate_events、restrictions、financials、financial_pubdates、quality_review。
- 測試 107 passed；A7 真實中斷續傳通過；健康檢查 28/28 OK。證據：`evidence/2026-11-02_gap_fixes/`。

## [0.1.5] — 2026-11-02 — 2019 起全量回補完成
- `backfill-full` 2019-01-01～2026-10-31：2026-11-01 20:00 → 11-02 10:47（台北），2,044 平日、94 個月全部 DONE、0 失敗、0 次安全警示；MOPS 歷史月營收 186 個市場月全部完成；技術指標 3,948,909 列。
- 完整性報告（`evidence/2026-11-01_local_deploy/integrity_post_full_backfill.md`）：Complete=True。daily_prices 4,014,475、institutional_trades 3,563,322、margin_trades 3,621,536、valuations 3,410,130、monthly_revenues 163,697、corporate_actions 8,788、raw_payloads 15,729；資料庫 5.3 GB、原始回應檔 479 MB。
- 品質檢查：`volume_without_close` 4,865（ERROR，當日不可交易，屬正確）、`move_exceeds_limit` 957（WARN，新增缺口 G13）。
- PHASE 1 仍為條件式驗收（G3–G13）；未進入 PHASE 2，等待使用者驗收。

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
