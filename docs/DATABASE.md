# 資料庫設計（PostgreSQL 16，41 張表）

Migration：`backend/alembic/versions/0001_initial_schema.py`（表）、`0002_audit_triggers.py`（稽核觸發器）。模型：`backend/aqt/models/`。

## 共通規則
- 時間一律 `timestamptz`；交易日 `date`（台北時區）。
- 公布晚於所描述期間的資料都有 `available_at`；讀取時必須 `available_at <= 決策時間`。
- 來源資料列都有 `source` 與 `raw_payload_id`（可追溯到原始回應）。
- 價格 `numeric(12,4)`、金額 `numeric(18,4)`；指標 `double precision`。

## 市場資料（PHASE 1）
| 表 | 主鍵 | 用途 |
|---|---|---|
| `securities` | id；唯一 (code, market) | 所有出現過的證券，含下市（status、first/last_seen_date、delisted_date） |
| `trading_calendar` | (market, cal_date) | 休市日；source = holiday_schedule / observed_data（後者優先） |
| `raw_payloads` | id | 每次 HTTP 回應原文（gzip）、sha256、抓取時間、解析狀態；**內容不可改、不可刪** |
| `daily_prices` | (security_id, trade_date) | 原始 OHLC、量、值、筆數、昨收、漲跌停、adj_factor、停牌 |
| `market_indices` | (index_code, trade_date) | 加權指數、類股指數 |
| `corporate_actions` | id；唯一 (security_id, ex_date, action_type) | 除權息前收盤、參考價、現金股利 |
| `technical_indicators` | (security_id, trade_date) | MA、KD、RSI、MACD、布林、ATR、量均、量比、高低點、calc_version |
| `institutional_trades` | (security_id, trade_date) | 外資（不含/自營商）、投信、自營商（自行/避險）、合計 |
| `margin_trades` | (security_id, trade_date) | 融資/融券 買賣償還餘額限額、資券互抵 |
| `valuations` | (security_id, trade_date) | 本益比、淨值比、殖利率 |
| `monthly_revenues` | (security_id, period) | 月營收、MoM、YoY、累計、公布日 |
| `financial_statements` | (security_id, fiscal_year, fiscal_quarter, basis) | 營收、毛利、營益、淨利、EPS、毛利率、營益率、資產/負債/權益 |
| `intraday_quotes` | id | 盤中快照（含 delay_seconds） |
| `data_quality_issues` | id | 檢查名稱、嚴重度、明細 |
| `data_source_status` | (source, dataset) | 最後成功/失敗、連續失敗、最新資料日 |

## 系統
`job_runs`（每次排程執行）、`system_logs`、`notifications`、`system_state`（safe_mode 等跨重啟狀態）、`llm_calls`（Claude 呼叫成本）。

## 模擬交易（PHASE 3）
| 表 | 重點 |
|---|---|
| `sim_accounts` | CHECK mode='SIMULATION'、initial_capital > 0 |
| `account_settings` / `_history` | 使用者可調的 8 項設定；歷史只能新增 |
| `trade_decisions` | AI 決策＋完整理由＋分析師輸入＋風控結果＋資料截止時間；**只能新增＋雜湊鏈** |
| `orders` | 委託；狀態可推進，理由/價格/數量不可改，不可刪 |
| `order_events` | 委託生命週期；只能新增＋雜湊鏈 |
| `fills` | 成交：參考價、成交價、手續費、稅、滑價、價格來源與延遲；只能新增＋雜湊鏈 |
| `positions` | 目前持倉 |
| `closed_trades` | 已平倉回合（勝率只從這裡算） |
| `cash_ledger` | 現金帳（含 T+2 交割日）；只能新增＋雜湊鏈 |
| `equity_snapshots` | 每日權益、回撤、基準 |

## 研究（PHASE 2/4/6）
`strategies`、`strategy_versions`（定義不可改、不可刪）、`strategy_stage_changes`（晉級紀錄，只能新增）、`backtest_runs`、`backtest_trades`、`market_regimes`、`scan_runs`、`stock_scores`、`reviews`（只能新增）、`research_proposals`。

## 雜湊鏈
`row_hash = sha256(prev_hash || row_to_json(row with row_hash=NULL))`，以 advisory lock 序列化。任何繞過觸發器的修改都會讓後續驗證失敗（`tests/test_db_audit.py::test_hash_chain_detects_tampering`）。PHASE 3 會加入每日把最新 row_hash 寫入 `reviews` 的錨點，並提供 `/api/audit/verify`。
