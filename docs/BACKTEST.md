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

## 啟動條件（fail closed）
只有同時滿足下列條件，引擎才會執行，否則丟出 `DataNotReady`：
- `system_state.data_readiness` 存在
- `technical_backtest_ready` 明確等於 `true`
- `coverage_complete` 等於 `true`
- `quality_status` 為 `CLEAN` 或 `REVIEWED_WITH_EXCLUSIONS`

要啟用財報（`allow_fundamentals`），還必須 `fundamental_backtest_ready` 等於 `true`。

## 每日事件順序（交易日 d）
1. **公司事件**：只套用同時符合下列條件的事件：
   - 除權息日 ≤ d
   - 在 d 開盤前已公開（`available_at <= d 09:00`）
   - 部位在除權息日前就已持有

   每個事件只套用一次。如果事件比除權息日晚公開，就等它公開那天才套用；在那之前，價格下跌會照實呈現為損失。
   股利模式只有一種：**`TOTAL_RETURN_REINVEST`**（總報酬、無摩擦自動再投資），依「前收 ÷ 參考價」調整持股、保留市值。這個模式會寫進回測設定、績效指標與 `backtest_runs.dividend_mode`。系統沒有可驗證的現金股利入帳日，所以不提供現金入帳模式（設定時會直接報錯）。
2. **成交**：決策日 T 收盤後產生的委託，在之後的交易日以**開盤價**成交。先處理賣單，再處理買單。
3. **評價**：以 d 的收盤價計算市值。
4. **無法交易的持股**：當天沒有開盤或收盤的持股，**不會被出場**，凍結在最後收盤價並記入 `untradable_holdings`。系統不會用「之後沒有行情」、`Security.status` 或最後出現日期等事後資訊來處理。只要回測結束時仍有無法交易的持股，或任一持股連續 ≥ 20 個交易日無法交易，該次回測就會標示 **`usable_for_evaluation = false`**，並列出受影響的證券與期間。這部分要等 G9（官方下市事件）接上後才能改進。
5. **決策**：決策日（策略自訂，例如每週或每月第一個交易日）在 d 的 18:00 建立 `DataView`，策略回傳目標權重。

## 成交規則（`engine._fill`）
| 情況 | 處理 |
|---|---|
| 成交日沒有開盤價 | 不成交。買單取消；賣單隔天重試，最多 20 天 |
| **成交日當天的全日成交量、最高、最低、收盤** | **完全不讀取**，這些在開盤時還不可知 |
| 成交日屬停止交易或暫停交易 | 不成交，原因 `restricted:*` |
| 成交日屬處置、全額交割、分盤或管理股票 | **不得新買**；賣出可以 |
| 買進時開盤價 ≥ 漲停價 | 不成交（`limit_up_at_open`） |
| 賣出時開盤價 ≤ 跌停價 | 不成交、隔天重試（`limit_down_at_open`） |
| 數量 | 只買整張（1,000 股）；單筆上限為**決策日 T（含）以前 20 日的平均成交股數** × 5%（`max_participation`），超過部分只成交一部分 |
| 價格 | 開盤價依滑價往不利方向調整，再取有效跳動點，且不超過漲跌停價 |
| 滑價（單邊） | 依決策日以前 20 日的平均成交值分級：≥10 億 5bps、≥2 億 10bps、≥5,000 萬 20bps、≥1,000 萬 40bps、其他 80bps；另有 `extra_slippage_bps` 可做壓力測試 |
| 手續費 | 0.1425% × 折扣（預設不打折），最低 20 元，買賣雙邊都收 |
| 證交稅 | 賣出 0.3%；ETF 0.1% |
| 現金 | 買進前先檢查可負擔數量，不允許負現金 |

所有被拒絕的委託都會記在 `rejections`，並附上原因，不會被默默成交。

## Point-in-time 保證
- 策略只拿得到 `DataView`，拿不到資料庫連線。
- 所有查詢都限定 `trade_date <= d` 且 `available_at <= d 18:00`（行情 14:30、法人 16:30、本益比 17:30 已公布；融資券 21:30 尚未公布）。
- 還原價由 DataView **自行重建**，只用除權息日 ≤ d、而且在決策時已公開的公司事件。資料庫裡存的還原因子與技術指標表都包含之後才公開的事件，所以完全不使用。MA20、MA60、20 日均成交值也都從這份時點正確的歷史資料計算。
- 候選股票只能來自 `build_universe(d)`，它會套用處置、全額交割、分盤、管理、停止或暫停交易、未解釋的價格異常、流動性、價格與歷史天數等排除規則。股票池以外的目標一律拒絕（`not_in_universe`）。
- 基本面：`DataView.fundamentals()` 預設丟出 `FundamentalsNotAllowed`。`allow_fundamentals=True` 也只有在 `system_state.data_readiness.fundamental_backtest_ready = true` 時才允許，而且只會回傳公告時間經過驗證的財報。
- 測試：
  - 把截止日之後的價格全部竄改，截止日以前的回測結果雜湊必須完全不變。
  - 截止日之後才公開的公司事件，不可影響截止日以前的結果。
  - 改變 T+1 的全日成交量，T+1 開盤的成交結果不變。

## 紀錄（不可竄改）
`backtest_runs`、`backtest_trades` 由 migration 0004 加上只能新增的觸發器（UPDATE、DELETE、TRUNCATE 都會被拒絕）。每筆執行都會保存：
- 策略版本：`strategy_versions` 本身不可修改，版本標籤為「版本-參數雜湊」，同一個標籤若參數不同會被拒絕
- `config`（成本模型、成交規則、引擎版本、拒絕原因統計）
- `params_hash`
- **資料指紋** `data_manifest`／`data_version_hash`：回測期間往前推 400 天到結束日，涵蓋行情、公司事件、交易限制（含跨期仍有效者）、品質問題、指數，**按月計算 sha256（逐列全部欄位）**，再加上證券主檔的 sha256，最後對整份 manifest 取 sha256。只要改動任何一列的任何一個值，指紋就會改變（有測試驗證）。
- `dividend_mode`、`benchmark_index`、`benchmark_kind`、`usable_for_evaluation`、`evaluation`（無法交易持股清單）
- `code_ref`（引擎與規則原始碼的 sha256 指紋）
- `started_at` / `finished_at`
- `results_hash`（權益曲線、成交、拒絕的雜湊）
- 免責聲明

同樣的輸入必定得到同樣的 `results_hash`。

## 評估流程
- **樣本外**：交易日依時間切成訓練 60%、驗證 20%、測試 20%。參數只在訓練期挑選（Sharpe 最高；同分取格點中的第一個），驗證期和測試期各只跑一次。
- **Walk-forward**：滾動或錨定的訓練／測試視窗，測試視窗彼此不重疊。每個視窗只用訓練期選參數。**樣本外的資金是連續的**：每個測試視窗都從上一個視窗**實際的期末狀態**（現金、持股、未完成的賣單、已套用的公司事件）重新執行，不會先用固定本金跑完再按比例縮放，所以整張、最低手續費、成交量上限與成本，都是依當時真實的資產規模計算。中間的視窗把持倉延續到下一段（`terminal=CARRY`），只有最後一段做期末評價（`END_MARK`）。測試驗證三件事：每段起始資產等於前一段期末、總結果的期末淨值等於最後一段、成本拆解等於各段加總。
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
- 公司事件只有總報酬再投資模式，沒有模擬現金股利入帳的時間差，也沒有模擬配股入帳的延遲。
- 基準指數：總報酬模式只和**「發行量加權股價報酬指數」**（含股利）比較。TAIEX 價格指數只當作參考列出，並標示為「不可比較」，不計算超額報酬。報酬指數只涵蓋上市股票，策略股票池則包含上櫃，口徑仍不完全一致。
- 期末評價（`END_MARK`）：最後一段仍持有的部位，以最後收盤價扣除賣出成本計算價值。這是評價，不是成交，fills 會標示 `END_MARK`。
- 下市（G9）：沒有官方下市事件，無法交易的持股只能凍結並把回測標為不可用，不會自動出場。
- 尚未有模擬交易引擎（PHASE 3）；本引擎不會產生任何真實或模擬下單。
