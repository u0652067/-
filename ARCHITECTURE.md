# ARCHITECTURE

## 1. 技術選擇與理由
| 層 | 選擇 | 理由 |
|---|---|---|
| 後端 | Python 3.12+ / FastAPI | 量化生態（pandas/numpy）、型別、易測試 |
| 資料庫 | PostgreSQL 16 | 交易紀錄需要 ACID、CHECK 限制、觸發器（只能新增、雜湊鏈）、JSONB；TimescaleDB 可日後加掛 |
| ORM / Migration | SQLAlchemy 2 + Alembic | schema 版本化，可回溯 |
| 計算 | pandas / numpy，自行實作指標 | 避免 TA-Lib 編譯依賴；每個指標都有參考值或獨立實作對照測試 |
| 排程 | APScheduler（獨立 worker 程序） | 單機足夠、無需 Redis/Celery；狀態全存在 PostgreSQL，重啟後 `catch_up()` 補跑。若未來需多機分散再換 Celery |
| AI | Claude API（PHASE 4+） | 只用於研究假說、判讀、報告；有預算上限與 `llm_calls` 紀錄 |
| 前端 | Next.js + TypeScript（PHASE 5） | RWD、SSR；圖表用 TradingView Lightweight Charts |
| 部署 | Docker Compose：db / migrate / api / scheduler | 一台小型 VPS（2 vCPU / 4 GB）即可；建議位於台灣或日本，降低連交易所延遲 |

## 2. 系統圖
```
          ┌───────────── 公開資料來源 ─────────────┐
          │ TWSE rwd 報表 · TPEx · ISIN · MOPS OpenAPI · MIS(延遲) │
          └───────────────┬──────────────────────┘
                          │ Fetcher（節流 3s/host、重試、403→SourceBlocked）
                          ▼
                 raw_payloads（gzip 原文 + sha256 + 抓取時間，不可改）
                          │ 純函式 parser（依欄名定位）
                          ▼
   repository upsert（available_at 依 pit.py）→ PostgreSQL ◄── quality.py（品質檢查 → 安全模式）
                          │
       indicators/compute（還原權值、指標）   universe.py（可交易池）
                          │
  ┌─────── PHASE 2-6（規劃）───────────────────────────────────────┐
  │ research: 回測/樣本外/WF → strategy_versions（不可改）          │
  │ analysts → Portfolio Manager → trade_decisions（雜湊鏈）       │
  │ Risk Manager（硬規則）→ 模擬交易引擎 → orders/fills/cash_ledger │
  │ reviews / research_proposals（每日/週/月）                     │
  └────────────────────────────────────────────────────────────────┘
                          │
            FastAPI（/api/...） ──► Next.js Dashboard（PHASE 5）
    APScheduler worker（獨立程序）── job_runs / system_logs / notifications / system_state
```

## 3. 模組邊界
- `data/sources/*`：只知道 URL 與格式，不碰資料庫。
- `data/repository.py`：只負責寫入，處理 `available_at`、冪等。
- `data/pipeline.py`：組合流程與錯誤處理，每次執行寫 `job_runs`。
- 交易引擎（PHASE 3）只能透過 `trade_decisions` 接收 AI 決策，並獨立再驗證一次所有風控；AI 模組沒有直接寫 `orders` 的權限路徑。

## 4. 每日排程（Asia/Taipei，週一至週五）
| 時間 | 工作 | 階段 |
|---|---|---|
| 07:40 | 證券主檔同步（新上市/下市） | P1 ✅ |
| 08:30 | 盤前分析、當日候選與委託計畫 | P4 |
| 09:00–13:30 每 5 分 | 盤中延遲快照、持倉監控、停損停利 | P3/P4 |
| 17:45 | 收盤資料：行情、指數、除權息、法人、本益比 → 品質檢查 → 指標 → 股票池 | P1 ✅ |
| 18:30 | 收盤績效、每日檢討 | P3/P6 |
| 19:30（每日） | 月營收、財報 | P1 ✅ |
| 22:15 | 補抓融資融券（約 21:30 公布） | P1 ✅ |
| 週六 | 每週研究 | P6 |
| 每月 1 日 | 休市日更新；月報與策略優化 | P1 ✅ / P6 |
| 每 15 分 | heartbeat | P1 ✅ |

重啟：`scheduler` 啟動時執行 `catch_up()`，回頭 10 天找沒有成功 `daily_update` 的交易日補跑（資料寫入皆冪等）。

## 5. 安全模式
進入條件（PHASE 1 已實作）：任一市場行情抓取失敗、行情回應日期與要求不符、全市場筆數比前一交易日少 20% 以上、當日無任何行情列。
效果：`system_state.safe_mode.on=true` + 通知；PHASE 3 交易引擎在安全模式下拒絕新開倉（只允許停損減碼）。
解除：下一次完整且通過品質檢查的每日更新。

## 6. 研究誠信規則 → 實作對照
| 規則 | 實作 |
|---|---|
| 1–3 不用未來/未公布資料 | 每列 `available_at`（`pit.py`）：行情 14:30、法人 16:30、本益比 17:30、融資券 21:30；月營收 = 公布日隔天或次月 11 日；財報 = 公布日隔天或法定期限隔天（Q1 5/15、Q2 8/14、Q3 11/14、Q4 3/31）。重抓不會改寫 `available_at`。指標經測試證明不使用未來 K 棒。 |
| 4 不事後修改理由 | `trade_decisions` 只能新增 + 雜湊鏈；`orders.reason` 觸發器禁止修改 |
| 5–6 不刪失敗交易 | `fills`、`cash_ledger`、`closed_trades`（P3 加觸發器）不可刪；勝率只算 `closed_trades` |
| 7–8 成本與滑價 | `tw_rules.py` 手續費（0.1425%、最低 20 元）、證交稅（0.3%/當沖 0.15%/ETF 0.1%）；`fills.slippage_cost` 必填 |
| 9 倖存者偏差 | 依日期回補全市場行情，下市股票保留；股票池依當日資料建構 |
| 10 漲跌停/無法成交 | `daily_prices.limit_up/limit_down`；P3 成交模擬於鎖漲停買不到、鎖跌停賣不掉 |
| 11 時間序切分 | P2 回測框架只接受時間切分（train < validation < test），WF 滾動 |
| 12 原始資料與時間 | `raw_payloads` 保存每一次回應原文、sha256、抓取時間，不可改不可刪 |

## 7. 還原權值
`daily_prices` 存原始價；`adj_factor` 由 `corporate_actions`（TWT49U 除權息前收盤價與參考價）往回連乘。指標用還原價計算；最低價門檻、跳動點、漲跌停一律用原始價。注意：新的除權息會改變舊 K 棒的還原「價位」，但不改變報酬率與比例型指標。

## 8. 部署
```bash
cp .env.example .env   # 填 POSTGRES_PASSWORD
docker compose up -d --build
docker compose exec api python -m aqt.cli check-sources
docker compose exec api python -m aqt.cli sync-securities
docker compose exec api python -m aqt.cli backfill --start 2019-01-01 --end <today>
```
主機需能連線：www.twse.com.tw、isin.twse.com.tw、openapi.twse.com.tw、www.tpex.org.tw、mopsov.twse.com.tw、mis.twse.com.tw。
回補 7 年約 1,750 個交易日 × 8 個報表 × 3 秒節流 ≈ 12 小時（只需一次）。
