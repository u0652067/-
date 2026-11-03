# TASKS（進度追蹤 — 每次收工更新）

最後更新：2026-11-03｜目前階段：**PHASE 1 條件式驗收候選＋缺口修正 PR 待審（未進入 PHASE 2）**｜測試：107 passed（本機 Docker）

## PHASE 1 條件式驗收 — 狀態說明
「條件式」= 程式與部署流程完成、真實來源在本機（台灣家用 IP）驗證可用，但下列資料缺口尚未補齊或尚無官方來源。缺口補齊並經使用者驗收前，**不得進入 PHASE 2**。

### 尚未解決的資料缺口
| # | 缺口 | 影響 | 目前處理 |
|---|---|---|---|
| ~~G1~~ | ~~2019 起全量回補~~ | — | ✅ 2026-11-02 10:47 完成：2019-01-01～2026-10-31，行情/法人/融資券/本益比 0 缺日、月營收 186/186 月、0 失敗、0 次被擋（`evidence/2026-11-01_local_deploy/integrity_post_full_backfill.md`） |
| ~~G13~~ | ~~`move_exceeds_limit` 957 筆未逐筆確認~~ | — | ✅ 2026-11-03 全部分類：除權息 312、新上市前五日 91、ETF 無漲跌幅（未驗證，阻擋回測）554、未解釋 0；抽樣 25 筆以個股報表獨立比對全部相符。發現並修正「除權息日遇颱風休市」（2019-08-09、2023-08-03、2024-07-24/25、2026-07-10）|
| ~~G2~~ | ~~本機尚未安裝 Docker Desktop / WSL2 / Git~~ | — | ✅ 2026-11-01 已安裝（Docker 4.97 / Engine 29.9.2、Git 2.56），部署已啟動 |
| G3 | 過去年度休市日：TWSE 只提供當年度 | 過去年度以「實際有無行情」推斷 | 已實作；`integrity-check` 列出未解析的平日 |
| ~~G4~~ | ~~TPEx 除權息~~ | — | ✅ TPEx exDailyQ 2019–2026 共 8,230 筆；還原因子、前收、指標已重算 |
| G5 | TPEx 指數（櫃買指數） | 上櫃大盤狀態無資料 | 未處理 |
| ~~G6~~ | ~~處置／全額交割／暫停交易~~ | — | ✅ 處置（上市 1,616、上櫃 1,974 筆區間）、變更交易／全額交割、分盤、管理、停止交易（每日清單 1,899 天）、暫停交易；`tradability.blocked_securities()` 由 `build_universe()` 套用 |
| G7 | 歷史財報與實際公布時間 | 公告時間未取得者不得用於基本面回測 | ✅ 2026-11-03 完成：15,374 次電子文件查詢全部完成；54,729 筆（98.9%）有實際上傳時間（`pit_reliable=true`）；618 筆查無紀錄 → 永久排除，`fundamental_backtest_ready=false` |
| ~~G8~~ | ~~減資、分割、面額變更~~ | — | ✅ 減資 333、面額變更 24、ETF 分割/反分割 11 筆；TPEx ETF 分割未接（ETF 不在股票池）|
| G9 | 下市日期官方來源 | 以「最後出現日」推定 | 未處理 |
| G10 | 歷史月營收無實際公布日 | `available_at` 用法定期限（次月 10 日）推定 | 規則已實作；屬保守估計 |
| G11 | TWSE MIS 盤中延遲快照 | 未接排程、未宣稱即時 | PHASE 3 一起做 |
| G12 | FinMind 備援 adapter | 官方來源被擋時無備援 | 未處理；本機 IP 目前不需要 |

### 2026-11-02～03 缺口修正 PR（`claude/phase1-gap-fixes`）新增限制
| # | 限制 | 處理 |
|---|---|---|
| L1 | ETF 是否有漲跌幅限制無官方清單可驗證 | 554 筆 ETF 異常 `blocks_backtest`（ETF 本來就不在股票池） |
| L2 | ~~財報公告時間回補中~~ | ✅ 2026-11-03 完成（15,374/15,374） |
| L3 | 少數公司（每年約 50–90 家）電子文件查無財報上傳紀錄 | 永久 `pit_reliable=false`，不得用於基本面回測 |
| L4 | TPEx 暫停交易只由每日「停止交易」欄位涵蓋，無獨立區間查詢 | 已足以排除當日 |
| L5 | 處置隔日生效以「公布日 18:00 可得」計 | 保守處理 |
| L6 | 回測／模擬引擎尚未存在（PHASE 2/3） | 已以 `build_universe()`、`latest_statements_as_of()` 為唯一入口，寫入 CLAUDE.md 規則 |

## ▶ 下一步（依序）
1. [x] 雲端環境開放交易所網域、GitHub App 安裝（2026-10-31）。
2. [x] `check-sources`：每日行情/法人/融資券/本益比（上市+上櫃）、除權息、休市日、公司清單、月營收、財報（上市+上櫃）全部 ok，並逐欄比對真實值（`tests/test_real_payloads.py`）。
3. [x] 決定長期部署主機：**使用者自己的 Windows 電腦（台灣家用 IP）**，Docker Desktop 常駐（2026-10-31）。雲端資料庫只是暫存，不是正式資料。
4. [x] 本機持久化部署流程：`docker-compose.yml`（external volume `aqt_pgdata` + `data/raw`、`data/logs`、`data/reports`、`backups` 綁定）、`scripts/*.ps1`（啟動、停止、備份、還原、健康檢查、回補、測試）、README。
5. [x] 家用 IP 重新驗證 MOPS 歷史月營收（2026-10-31 13:57 台北）：上市/上櫃 2019-01、2021-06、2024-12、2026-09 共 8 頁全部 HTTP 200、無安全警示頁，解析 748～997 筆；原始頁與 sha256 在 `evidence/2026-10-31_home_ip/`。同時 TWSE T86 / MI_MARGN 2019-01-02、ISIN 一覽表、OpenAPI 皆可連線（雲端 IP 被擋的端點在本機都正常）。
6. [x] 2026-11-01：`start.ps1` → `test.ps1`（97 passed）→ `health.ps1`（28/28 OK）→ 容器重建驗證 → `backup.ps1 -Verify` / `restore.ps1` → smoke 回補（2019-01-02～01-11 完整）→ `backfill.ps1 start`（20:00 台北啟動，先做 pre_backfill 驗證備份與完整性基準）。
7. [x] 全量回補完成（2026-11-02 10:47，約 14.8 小時）；`integrity-check` Complete=True；回補後做驗證備份。**等待使用者驗收 PHASE 1。**
8. [ ] 使用者驗收 PHASE 1 後才開始 PHASE 2。

## PHASE 0 — 架構與規劃 ✅
- [x] 環境檢查（Python 3.13、Node 22、PostgreSQL 16、Docker CLI；無 Docker daemon；交易所網域被網路政策封鎖）
- [x] 專案資料夾與 git
- [x] CLAUDE.md、PROJECT_SPEC.md、ARCHITECTURE.md、ROADMAP.md、TASKS.md、TEST_PLAN.md、CHANGELOG.md
- [x] 完整資料庫設計（41 張表，涵蓋 PHASE 1–7）+ `docs/DATABASE.md`
- [x] 稽核觸發器（只能新增、雜湊鏈、策略版本不可改、模擬限定 CHECK）

## PHASE 1 — 台股資料中心 🟡 條件式驗收
- [x] 證券主檔：ISIN 一覽表（上市/上櫃，Big5）、下市標記（不刪除）、回補中遇到的已下市代號自動建檔
- [x] 歷史行情：TWSE MI_INDEX（新舊兩種格式）、TPEx dailyQ（新站 tables + 舊站 aaData）
- [x] 指數：加權指數與類股指數（TWSE）
- [x] 成交量／成交金額／筆數
- [x] 除權息（TWSE TWT49U）→ 參考價、還原因子
- [x] 昨收（含除權息參考價）、漲跌停價、跳動點規則
- [x] 技術指標：MA5/10/20/60、KD(9)、RSI6/14、MACD、布林、ATR14、量均線、量比、20/60 日高低
- [x] 三大法人（TWSE T86、TPEx）
- [x] 融資融券（TWSE MI_MARGN、TPEx）
- [x] 本益比/殖利率/淨值比（TWSE BWIBBU_d、TPEx peQryDate）
- [x] 月營收（OpenAPI 最新月 + MOPS 歷史 HTML 回補）
- [x] 財報（OpenAPI 最新一季損益表、資產負債表：營收、毛利、營益、淨利、EPS、毛利率、營益率、資產負債權益）
- [x] 盤中延遲快照 parser（TWSE MIS，量測並保存延遲秒數；**未接入排程，未宣稱即時**）
- [x] Point-in-time `available_at` 規則
- [x] 原始回應保存（gzip + sha256 + 時間）
- [x] 資料品質檢查：OHLC 邏輯、非正價格、有量無價、零量、超過漲跌幅、筆數驟減、法人加總
- [x] 安全模式（行情失敗/日期不符/資料不完整）與通知
- [x] 可交易股票池（排除：非普通股、當日無成交、品質錯誤、歷史不足 60 日、價格 < 10、20 日均成交值 < 1,000 萬）
- [x] 休市日（TWSE 行事曆）+ 實際資料推斷的交易日
- [x] 排程（APScheduler）+ 重啟補跑 + job_runs + heartbeat
- [x] 唯讀 API：health、sources、coverage、quality、jobs、notifications、stocks、prices、indicators、institutional
- [x] CLI、Dockerfile、docker-compose
- [x] 真實來源驗收（2026-10-31）：修正 TPEx 行情網址（`dailyQ`→`otc`）、TPEx 財報英文欄位、ISIN 安全警示頁偵測 + OpenAPI 公司清單備援、未解決 3xx 重試；真實回應精簡後放 `tests/fixtures/real/`
- [x] 本機部署：持久化 volume、原始回應雙份保存（DB + `data/raw`）、檔案日誌、備份/還原（含還原驗證）
- [x] `health-check`：TWSE、TPEx、OpenAPI、ISIN、MOPS、MIS 逐端點記錄 HTTP 狀態、轉址、格式、筆數、延遲、是否安全警示頁
- [x] `backfill-full`：可中斷、可續傳、進度存 DB、被擋自動冷卻重試、ETA；`backfill-status`
- [x] `integrity-check`：各表筆數、行情缺日、法人/融資券/本益比缺日、月營收缺月、原始回應 sha256 抽驗、可重跑日
- [~] ISIN 一覽表：雲端 IP 被擋；**本機家用 IP 可連線**（2026-10-31），本機部署後主檔改由 ISIN 取得
- [ ] 過去年度休市日：TWSE 只能查當年度；過去以實際資料推斷（已實作）
- [ ] TWSE MIS 盤中快照：雲端回 502，尚未驗證
- [ ] TPEx 除權息（目前只有 TWSE TWT49U；上櫃股票的還原權值尚未處理）
- [ ] TPEx 指數（櫃買指數）
- [ ] 處置股／全額交割股／暫停交易名單（股票池排除用）
- [ ] 歷史財報回補（MOPS 歷史查詢或 FinMind；含實際公布日）
- [ ] 減資、分割、面額變更的還原（TWSE TWTAVU 等）
- [ ] 下市日期官方來源（目前以「最後出現日」推定）
- [ ] FinMind 備援來源 adapter
- [ ] 盤中快照排程（PHASE 3 一起做）
- [x] Docker 映像實際建置測試（2026-11-01 本機）
- 2026-10-23（五）、10-26（一）TWSE 與 TPEx 都回「沒有資料」→ 記為休市（observed_data）。

## PHASE 2 — 量化研究與回測 🟡 技術面回測骨架完成，待使用者驗收（分支 `claude/phase2-backtest`）
使用者限制（2026-11-03）：只做技術面；`fundamental_backtest_ready=false` 時財報不得被任何回測、訊號、策略使用；不做 PHASE 3/4、網站、實盤；不得宣稱策略有效或可獲利。
- [x] 策略介面：策略只拿到 `DataView`（`trade_date <= t` 且 `available_at <= t 18:00`），無資料庫連線（`aqt/research/dataview.py`）
- [x] 回測引擎：事件驅動日頻；T 日收盤後決策 → T+1 開盤成交；漲跌停開盤鎖住不成交；成交量參與率上限；整張；賣單重試；下市以最後收盤出場；公司事件保留市值（`engine.py`）
- [x] 股票池：只能從 `build_universe()` 取得（處置、全額交割、分盤、管理、停止/暫停交易、未解釋異常、流動性）；成交日有限制者不得新買
- [x] 成本模型：手續費 0.1425%（最低 20）、證交稅 0.3%/ETF 0.1%、滑價依 20 日均成交值分級、跳動點（`costs.py`）
- [x] 時間切分、樣本外、Walk-forward（`splits.py`、`evaluation.py`）
- [x] 績效：總報酬、CAGR、波動、Sharpe、最大回撤與期間、勝率、平均盈虧、期望值、Profit factor、成本拆解、週轉率、曝險、相對 TAIEX
- [x] 不可竄改紀錄：migration 0004 觸發器；策略版本＋參數雜湊＋資料版本＋程式指紋＋執行時間＋結果雜湊＋免責聲明（`records.py`）
- [x] 基準策略：`cash_baseline`、`ew_liquid_baseline`；最小技術範例：`ma_trend_momentum`
- [x] 真實資料乾跑（不寫紀錄，2024 年、Walk-forward 2023-07～2024-12）：三個策略皆可重現（相同結果雜湊）
- [ ] 評分分位 vs 未來報酬驗證（待 PHASE 2 後續）
- [ ] 更多策略（突破、拉回、量能、法人）— 待驗收後
- [ ] migration 0004 尚未套用到正式資料庫（待使用者驗收 PHASE 2）

## PHASE 3–7 ⏳
見 ROADMAP.md。

## 已知問題 / 決策紀錄
- 2026-10-31（晚）：決定以使用者 Windows 電腦（台灣家用 IP）為常駐開發、資料收集、模擬交易環境。PostgreSQL 用 external named volume（Windows 綁定掛載對 PostgreSQL 資料目錄權限/效能不佳），其餘（原始回應副本、日誌、報告、備份）綁定到專案資料夾方便直接檢視。回補以 `system_state` 存進度，不新增資料表（不需新 migration）。
- 2026-10-30：開發用雲端環境無法連到 twse/tpex（proxy 403）。決策：先以「官方格式」合成資料離線測試整條流程，真實驗收移到可連線主機。
- 2026-10-31：連續請求太快（含兩個回補程序同時跑）後，TWSE T86 等端點約 10 分鐘全部回安全警示頁。決策：節流預設 3→5 秒、安全警示頁視為 `SourceBlockedError`（不重試、不硬打）、回補加 `--only-missing` 續傳。
- 2026-10-31（下午）：網域開放後驗收。`volume_without_close` 在真實資料出現（如 1538 只有盤後定價成交、收盤為 --），維持 ERROR＝當日不可交易，屬正確行為。2063 世鎧 10/28 漲跌超過 10%，推測為上櫃除權息（TPEx 除權息尚未接）。
- 2026-10-31（上午）：新雲端環境仍封鎖 www.twse.com.tw、www.tpex.org.tw、isin.twse.com.tw、openapi.twse.com.tw、mopsov.twse.com.tw、mis.twse.com.tw（proxy 403，WebFetch 亦無法解析）。基準測試 75 passed。需在環境 Network access 的 Allowed domains 加入上述 6 個網域才能做真實驗收；GitHub push 403，需安裝 Claude GitHub App。
- 2026-10-30：選 APScheduler 而非 Celery：單機、無 Redis、狀態在 PostgreSQL 足以重啟恢復。
- 2026-10-30：KD 採台灣慣用 1/3 平滑、起始 50；MACD 用收盤價（非 DI 價）。
