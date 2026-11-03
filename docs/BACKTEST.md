# 回測引擎（PHASE 2，技術面）

> 研究用途。回測結果是歷史模擬的量測值，**不代表任何策略有效、可獲利或可交易**，也不是投資建議。
> 本階段只做技術面回測：`fundamental_backtest_ready = false`，財報一律不可使用。

## 程式位置
```
backend/aqt/research/
  engine.py      事件驅動日頻回測（BacktestConfig, Backtester, run_backtest）
  dataview.py    point-in-time 資料檢視（策略唯一的資料入口）
  costs.py       台股成本模型：手續費、證交稅、流動性分級滑價、跳動點、整張、漲跌停、量能上限
  strategies.py  策略介面＋範例：cash_baseline、ew_liquid_baseline、ma_trend_momentum
  metrics.py     績效：報酬、CAGR、波動、Sharpe、最大回撤、勝率、平均盈虧、期望值、Profit factor、成本
  splits.py      時間順序切分、Walk-forward 視窗
  evaluation.py  樣本外與 Walk-forward 流程（參數只在訓練期選）
  records.py     不可竄改的紀錄：策略版本、參數雜湊、資料版本、程式指紋、執行時間、結果雜湊
  dry_run.py     不寫紀錄的真實資料驗證
```

## 每日事件順序（交易日 d）
1. **公司事件**：除權息日落在 (前一交易日, d] 的事件，依「前收 ÷ 參考價」調整持股數，等同把股利再投入、保留市值。這是總報酬處理方式，簡化掉現金股利入帳的時間差。
2. **成交**：前一個決策日收盤後產生的委託，在 d 的**開盤價**成交。先處理賣單，再處理買單。
3. **下市**：已下市、之後再也沒有行情的持股，以最後收盤價出場（扣賣出成本）。
4. **評價**：以 d 的收盤價計算市值。如果當天沒有收盤價，沿用最後收盤價。
5. **決策**：決策日（策略自訂，例如每週或每月第一個交易日）在 d 的 18:00 建立 `DataView`，策略回傳目標權重。

## 成交規則（`engine._fill`）
| 情況 | 處理 |
|---|---|
| 成交日沒有行情、沒有收盤價或成交量為 0 | 不成交。買單取消；賣單隔天重試，最多 20 天 |
| 成交日屬停止交易或暫停交易 | 不成交，原因 `restricted:*` |
| 成交日屬處置、全額交割、分盤或管理股票 | **不得新買**；賣出可以 |
| 買進時開盤價 ≥ 漲停價 | 不成交（`limit_up_at_open`） |
| 賣出時開盤價 ≤ 跌停價 | 不成交、隔天重試（`limit_down_at_open`） |
| 數量 | 只買整張（1,000 股）；單筆上限為當日成交量 × 5%（`max_participation`），超過部分只成交一部分 |
| 價格 | 開盤價依滑價往不利方向調整，再取有效跳動點，且不超過漲跌停價 |
| 滑價（單邊） | 依前 20 日平均成交值分級：≥10 億 5bps、≥2 億 10bps、≥5,000 萬 20bps、≥1,000 萬 40bps、其他 80bps；另有 `extra_slippage_bps` 可做壓力測試 |
| 手續費 | 0.1425% × 折扣（預設不打折），最低 20 元，買賣雙邊都收 |
| 證交稅 | 賣出 0.3%；ETF 0.1% |
| 現金 | 買進前先檢查可負擔數量，不允許負現金 |

所有被拒絕的委託都會記在 `rejections`，並附上原因，不會被默默成交。

## Point-in-time 保證
- 策略只拿得到 `DataView`，拿不到資料庫連線。
- 所有查詢都限定 `trade_date <= d` 且 `available_at <= d 18:00`（行情 14:30、法人 16:30、本益比 17:30 已公布；融資券 21:30 尚未公布）。
- 還原價以 d 當天為基準重新計算（adj(s)/adj(d)），所以只用到 d 以前發生的公司事件。技術指標存的是全期還原尺度，策略只能拿來做比值比較，例如收盤價 / MA20。
- 候選股票只能來自 `build_universe(d)`，它會套用處置、全額交割、分盤、管理、停止或暫停交易、未解釋的價格異常、流動性、價格與歷史天數等排除規則。股票池以外的目標一律拒絕（`not_in_universe`）。
- 基本面：`DataView.fundamentals()` 預設丟出 `FundamentalsNotAllowed`。`allow_fundamentals=True` 也只有在 `system_state.data_readiness.fundamental_backtest_ready = true` 時才允許，而且只會回傳公告時間經過驗證的財報。
- 測試：把截止日之後的價格全部竄改，截止日以前的回測結果雜湊必須完全不變。

## 紀錄（不可竄改）
`backtest_runs`、`backtest_trades` 由 migration 0004 加上只能新增的觸發器（UPDATE、DELETE、TRUNCATE 都會被拒絕）。每筆執行都會保存：
- 策略版本：`strategy_versions` 本身不可修改，版本標籤為「版本-參數雜湊」，同一個標籤若參數不同會被拒絕
- `config`（成本模型、成交規則、引擎版本、拒絕原因統計）
- `params_hash`
- `data_version` 與其雜湊（raw payload 最大 id、行情筆數與最後日期、事件、限制、品質阻擋數、可靠財報數）
- `code_ref`（引擎與規則原始碼的 sha256 指紋）
- `started_at` / `finished_at`
- `results_hash`（權益曲線、成交、拒絕的雜湊）
- 免責聲明

同樣的輸入必定得到同樣的 `results_hash`。

## 評估流程
- **樣本外**：交易日依時間切成訓練 60%、驗證 20%、測試 20%。參數只在訓練期挑選（Sharpe 最高；同分取格點中的第一個），驗證期和測試期各只跑一次。
- **Walk-forward**：滾動或錨定的訓練／測試視窗，測試視窗彼此不重疊。每個視窗重新選參數，然後用新的投資組合跑測試期，最後把各測試期的權益串接起來計算整體樣本外績效。
- 所有參數搜尋的執行也都會記錄（`role=parameter_search`），不會只保留最好的一次。

## 指令
```bash
python -m aqt.cli backtest --strategy ma_trend_momentum --start 2024-01-01 --end 2024-12-31 --params '{"top_n": 20}'
python -m aqt.cli evaluate --mode oos --strategy ma_trend_momentum --start 2021-01-01 --end 2024-12-31 --grid '[{"top_n":10},{"top_n":20}]'
python -m aqt.cli evaluate --mode walk-forward --strategy ma_trend_momentum --start 2021-01-01 --end 2024-12-31 --train-days 250 --test-days 60
python -m aqt.research.dry_run 2024-01-01 2024-12-31 2023-07-01   # 不寫紀錄
```

## 已知簡化與限制
- 只有日 K，以開盤價成交。沒有盤中模擬，也沒有零股。
- 公司事件用「保留市值」方式調整持股，不模擬現金股利入帳的時間差，也不模擬除權配股的入帳延遲。
- 基準指數 TAIEX 是價格指數，不含股利，因此與總報酬口徑不同。
- 期末仍持有的部位，以最後收盤價扣除賣出成本後計算價值，但不檢查當天的成交限制。
- 尚未有模擬交易引擎（PHASE 3）；本引擎不會產生任何真實或模擬下單。
