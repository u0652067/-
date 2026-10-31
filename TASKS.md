# TASKS（進度追蹤 — 每次收工更新）

最後更新：2026-10-31｜目前階段：**PHASE 1 真實來源驗收完成，歷史回補進行中**｜測試：88 passed

## ▶ 下一步（依序）
1. [x] 雲端環境開放交易所網域、GitHub App 安裝（2026-10-31）。
2. [x] `check-sources`：每日行情/法人/融資券/本益比（上市+上櫃）、除權息、休市日、公司清單、月營收、財報（上市+上櫃）全部 ok，並逐欄比對真實值（`tests/test_real_payloads.py`）。
3. [~] `backfill --start 2019-01-01 --only-missing`：已在雲端 session 啟動（逐年）。**TWSE 防火牆會對雲端 IP 間歇性回「FOR SECURITY REASONS」（307）**，T86/MI_MARGN/TWT49U 的歷史日期特別常被擋，MI_INDEX 與 TPEx 全部正常。被擋的資料集會記在 `missing`，之後用 `--only-missing` 重跑補齊。**雲端容器是暫時的，資料庫不會保留** → 正式資料要在長期主機（最好是台灣 IP）重跑回補；若仍被擋，備援是 FinMind（需 token）。
4. [ ] `mopsov.twse.com.tw`：網路政策已放行（2026-10-31），但網站本身對雲端 IP 回「FOR SECURITY REASONS」（307）→ 歷史月營收回補要在長期主機（台灣 IP）驗證，或改用 FinMind。
5. [ ] 決定長期部署主機（PHASE 7 前必須；排程需要常駐）。
6. [ ] 開始 PHASE 2（見下方）。

## PHASE 0 — 架構與規劃 ✅
- [x] 環境檢查（Python 3.13、Node 22、PostgreSQL 16、Docker CLI；無 Docker daemon；交易所網域被網路政策封鎖）
- [x] 專案資料夾與 git
- [x] CLAUDE.md、PROJECT_SPEC.md、ARCHITECTURE.md、ROADMAP.md、TASKS.md、TEST_PLAN.md、CHANGELOG.md
- [x] 完整資料庫設計（41 張表，涵蓋 PHASE 1–7）+ `docs/DATABASE.md`
- [x] 稽核觸發器（只能新增、雜湊鏈、策略版本不可改、模擬限定 CHECK）

## PHASE 1 — 台股資料中心 🟡
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
- [ ] ISIN 一覽表（雲端 IP 被擋）：目前用公司清單備援，ETF/特別股只由行情自動建檔、沒有 ISIN 碼
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
- [ ] Docker 映像實際建置測試（開發環境沒有 Docker daemon）

## PHASE 2 — 量化研究與回測 ⏳
- [ ] 策略介面（訊號只能讀 `available_at <= t` 的資料；資料存取層強制）
- [ ] 回測引擎（日K，T 日收盤後訊號 → T+1 開盤委託；漲跌停鎖死無法成交；成交量參與率上限）
- [ ] 成本模型（手續費、證交稅、滑價依流動性分級）
- [ ] 時間切分、樣本外、Walk-forward
- [ ] 績效統計模組（與 PHASE 3 共用）
- [ ] 評分分位 vs 未來報酬驗證
- [ ] 基礎策略 V1.0：均線排列趨勢、突破、拉回、量能異常、法人連買

## PHASE 3–7 ⏳
見 ROADMAP.md。

## 已知問題 / 決策紀錄
- 2026-10-30：開發用雲端環境無法連到 twse/tpex（proxy 403）。決策：先以「官方格式」合成資料離線測試整條流程，真實驗收移到可連線主機。
- 2026-10-31：連續請求太快（含兩個回補程序同時跑）後，TWSE T86 等端點約 10 分鐘全部回安全警示頁。決策：節流預設 3→5 秒、安全警示頁視為 `SourceBlockedError`（不重試、不硬打）、回補加 `--only-missing` 續傳。
- 2026-10-31（下午）：網域開放後驗收。`volume_without_close` 在真實資料出現（如 1538 只有盤後定價成交、收盤為 --），維持 ERROR＝當日不可交易，屬正確行為。2063 世鎧 10/28 漲跌超過 10%，推測為上櫃除權息（TPEx 除權息尚未接）。
- 2026-10-31（上午）：新雲端環境仍封鎖 www.twse.com.tw、www.tpex.org.tw、isin.twse.com.tw、openapi.twse.com.tw、mopsov.twse.com.tw、mis.twse.com.tw（proxy 403，WebFetch 亦無法解析）。基準測試 75 passed。需在環境 Network access 的 Allowed domains 加入上述 6 個網域才能做真實驗收；GitHub push 403，需安裝 Claude GitHub App。
- 2026-10-30：選 APScheduler 而非 Celery：單機、無 Redis、狀態在 PostgreSQL 足以重啟恢復。
- 2026-10-30：KD 採台灣慣用 1/3 平滑、起始 50；MACD 用收盤價（非 DI 價）。
