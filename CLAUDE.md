# CLAUDE.md — 給 Claude Code 的工作守則（每次開啟專案先讀）

## 專案一句話
AI 台股量化研究與**模擬**交易系統：自主掃描上市櫃股票、模擬交易、檢討、研究並版本化策略；網站呈現可追溯、不可竄改的紀錄。

## 開工流程（每次 session）
1. 讀 `TASKS.md`（目前進度、下一步）與 `CHANGELOG.md` 最上面幾筆。
2. 需要細節再讀 `ARCHITECTURE.md`、`docs/DATABASE.md`、`docs/DATA_SOURCES.md`。
3. 不要重建已完成的模組；修改前先跑測試確認基準：
   ```bash
   cd backend && python -m pytest -q
   ```
4. 收工前：更新 `TASKS.md`（勾選／新增）與 `CHANGELOG.md`（新增一筆），測試必須全綠。

## 不可違反的規則
- **只做模擬交易。** 不得加入任何真實券商下單、帳戶操作或 API 金鑰登入券商的程式碼。`sim_accounts.mode` 有資料庫 CHECK 限制為 `SIMULATION`；`Settings.trading_mode` 只接受 `SIMULATION`。未來若做實盤，必須是獨立程式庫與獨立部署，且需使用者明確書面授權。
- **不得使用未來資料。** 所有決策與回測只能讀 `available_at <= 決策時間` 的資料（`aqt/data/pit.py`）。財報、營收、法人資料都有公布時間規則。
- **不得竄改紀錄。** `trade_decisions`、`fills`、`cash_ledger`、`order_events`、`reviews`、`strategy_stage_changes`、`raw_payloads` 由資料庫觸發器保護為只能新增；前四者有雜湊鏈。不得以任何 migration 移除這些觸發器。
- **不得刪除失敗交易、不得只統計成功案例、不得忽略成本與滑價。**
- **倖存者偏差：** 歷史資料按日期回補（不是按今天的股票清單），下市股票保留（`securities.status=DELISTED`，絕不刪除）。
- **策略版本不可改：** `strategy_versions.params` 寫入後不可修改，只能新增版本；晉級需走 回測→樣本外→Walk-forward→模擬→穩定性。
- **資料異常 → 安全模式。** 行情抓取失敗或資料不完整時 `system_state.safe_mode.on=true`，交易引擎必須拒絕新開倉。
- **不得宣稱即時行情。** 目前沒有授權即時行情來源；盤中資料（TWSE MIS）是延遲快照，必須顯示 `delay_seconds`。
- **金鑰只用環境變數**（`.env`，見 `.env.example`），不得寫死、不得提交。
- **不得編造資料。** 測試用的合成資料只能放在 `backend/tests/fixtures/`，且必須標註 `SYNTHETIC`。

## 程式結構速查
```
backend/aqt/
  config.py            環境變數設定（AQT_ 前綴）
  models/              SQLAlchemy 模型：market / trading / research / system
  data/
    sources/           twse, tpex, isin, mops, twse_mis（URL + 純函式解析器）
    http.py            禮貌抓取（節流/重試）+ 原始回應 gzip 存檔
    repository.py      冪等 upsert
    pipeline.py        每日流程、回補、證券主檔、行事曆、基本面
    quality.py         資料品質檢查、昨收/漲跌停
    universe.py        可交易股票池（含排除原因）
    pit.py             資料可用時間規則
    tw_rules.py        跳動點、漲跌停、手續費、證交稅
    smoke.py           真實來源煙霧測試（check-sources）
  indicators/          technical.py（純計算）、compute.py（寫入 DB、還原權值）
  jobs/                runtime.py（job_runs、安全模式、通知）、scheduler.py（APScheduler）
  api/main.py          FastAPI（唯讀資料 API）
  cli.py               python -m aqt.cli <command>
backend/alembic/       0001 schema、0002 稽核觸發器
```

## 常用指令
```bash
cd backend
python -m aqt.cli init-db                      # 套用 migration
python -m aqt.cli check-sources                # 真實資料來源 + 解析器煙霧測試（需可連到 twse/tpex）
python -m aqt.cli sync-securities              # 證券主檔
python -m aqt.cli sync-calendar --year 2026    # 休市日
python -m aqt.cli backfill --start 2019-01-01 --end 2026-10-30
python -m aqt.cli update-daily [--date YYYY-MM-DD]
python -m aqt.cli sync-fundamentals
python -m aqt.cli scheduler                    # 背景排程（常駐）
python -m aqt.cli api --port 8000
python -m pytest -q                            # 需本機 PostgreSQL 資料庫 aqt_test
```
測試資料庫：`AQT_TEST_DATABASE_URL`（預設 `postgresql+psycopg://postgres@127.0.0.1:5432/aqt_test`），測試會 DROP 並重建 schema。

## 寫程式慣例
- 解析器是純函式（payload → records），用標題名稱找欄位，格式改版時才不會默默錯位；新增來源先寫 fixture 測試。
- 金額、價格用 `Decimal`；指標用 float。
- 新增資料表 → 新 alembic revision（`alembic revision --autogenerate`），不得修改已發佈的 migration。
- Claude API 只用在研究/判斷/報告；技術指標、績效、風控一律用可測試的程式計算。每次呼叫記錄在 `llm_calls`。
