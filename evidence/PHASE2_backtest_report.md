# PHASE 2 技術面回測骨架 — 驗收報告

報告時間：2026-11-03 23:00（台北）｜分支：`claude/phase2-backtest`｜狀態：**待使用者驗收；未開始 PHASE 3/4、網站、實盤，也未啟動模擬交易**

> 本階段建立的是可驗證的研究與回測能力。以下數字只用來檢查回測機制是否正常運作，**不代表任何策略有效、可獲利或可交易**。

## 使用者限制的對照
| 限制 | 實作 |
|---|---|
| 1. 未驗證公告時間的財報不得被任何回測、訊號、策略使用 | `DataView.fundamentals()` 預設丟出 `FundamentalsNotAllowed`。`allow_fundamentals=True` 只有在 `fundamental_backtest_ready=true` 時才允許，而且只透過 `latest_statements_as_of()`，它只回傳 `pit_reliable` 的財報。有測試覆蓋。 |
| 2. 區分 technical／fundamental readiness | `integrity-check` 寫入 `system_state.data_readiness`，`/api/health` 回傳兩個獨立欄位，沒有單一的 `backtest_ready`。正式資料庫目前：**technical_backtest_ready=true、fundamental_backtest_ready=false**（618 筆財報缺可驗證公告時間）。只要 technical 為 false，引擎就拒絕執行（`DataNotReady`）；每次執行都會記錄當下的 readiness。 |
| 3. 不做 PHASE 3/4、網站、實盤 | 沒有新增任何下單、帳戶、網站或 AI 決策程式碼；回測引擎也不寫入 orders、fills 等模擬交易資料表。 |
| 4. 不宣稱策略有效 | 每筆紀錄、報告、CLI 輸出都附免責聲明（`aqt.research.DISCLAIMER`）。 |

## 要求項目
| 項目 | 結果 |
|---|---|
| 可重現、事件驅動的日頻回測引擎 | ✅ `aqt/research/engine.py`：T 日 18:00 決策，T+1 開盤成交。程式沒有任何隨機性；同樣的輸入，結果雜湊必定相同（真實資料跑兩次，三個策略的雜湊都相同） |
| point-in-time，嚴禁未來資料 | ✅ 策略只拿得到 `DataView`，查詢限制 `trade_date <= t` 且 `available_at <= t 18:00`；還原價以決策日為基準重新計算。測試：竄改截止日之後的全部價格，截止日以前的結果雜湊不變 |
| 股票池排除規則 | ✅ 只能從 `build_universe()` 取得候選股票（處置、全額交割、分盤、管理、停止或暫停交易、未解釋異常、流動性）；成交日若受限制，不得新買；下市持股以最後收盤價出場 |
| 交易成本與限制 | ✅ 手續費 0.1425%（最低 20 元）、證交稅 0.3%（ETF 0.1%）、依流動性分級的滑價、跳動點、整張交易、開盤漲停買不到、開盤跌停賣不掉（隔日重試）、單筆不超過當日成交量 5% |
| 不可竄改紀錄 | ✅ migration 0004 對 `backtest_runs`、`backtest_trades` 禁止 UPDATE、DELETE、TRUNCATE。每筆執行保存策略版本（不可修改）、參數雜湊、資料版本與雜湊、程式指紋、開始與結束時間、結果雜湊、當下的 readiness、免責聲明 |
| 基準與最小技術策略 | ✅ `cash_baseline`、`ew_liquid_baseline`（流動性前 N 名等權、每月再平衡）；`ma_trend_momentum`（收盤 > MA20 > MA60，依報酬排名取前 N 名、每週再平衡） |
| 時間切分、樣本外、Walk-forward | ✅ 依 60/20/20 時間順序切分；滾動或錨定的 Walk-forward，測試視窗不重疊；參數只在訓練期挑選；所有參數搜尋的執行都會留下紀錄 |
| 績效指標 | ✅ 總報酬、CAGR、波動、Sharpe、最大回撤與期間、勝率（虧損與打平都算進分母）、平均盈虧、期望值、Profit factor、成本拆解（手續費、證交稅、滑價）、週轉率、平均曝險、相對 TAIEX |
| 單元與整合測試 | ✅ PHASE 2 新增 16 項，全部測試 **124 passed** |

## 真實資料驗證（乾跑，不寫入正式資料庫）
本金 1,000 萬，期間 2024-01-01～2024-12-31。TAIEX 價格指數同期 +29.0%（不含股利）。

| 策略 | 結果雜湊（兩次執行一致） | 總報酬 | Sharpe | 最大回撤 | 交易數 | 勝率 | 總成本（含滑價） | 拒絕原因 |
|---|---|---:|---:|---:|---:|---:|---:|---|
| cash_baseline | 0a0875a1… | 0.0% | — | 0.0% | 0 | — | 0 | — |
| ew_liquid_baseline（top 30） | 7d4968ec… | +0.4% | 0.11 | −16.0% | 105 | 35.2% | 317,085 | 處置 1 |
| ma_trend_momentum（top 20、lookback 20） | cccc14d8… | −6.8% | −0.06 | −28.9% | 592 | 37.7% | 3,439,505 | 處置 30、開盤漲停 16、開盤跌停 3 |

- Walk-forward（ma_trend_momentum，2023-07～2024-12，訓練 120 日、測試 60 日、4 個視窗、2 組參數）：樣本外總報酬 −19.2%、Sharpe −0.49、最大回撤 −33.2%、468 筆交易。
- 2024 年共有 16～21 次公司事件自動調整持股，市值保持不變。
- 解讀：這些數字證明回測機制在真實資料上可以運作、可以重現，而且會確實套用限制與成本。範例策略扣除成本後表現不佳，符合「範例只用來驗證機制」的定位。

證據：`evidence/2026-11-03_phase2/phase2_dry_run_*.json`（兩次執行的雜湊一致）。

## 尚未做、刻意不做
- migration 0004 **尚未套用到正式資料庫**，等 PHASE 2 驗收後再套用。在此之前，真實資料驗證都以乾跑方式進行。
- 只有日 K、以開盤價成交；沒有零股、盤中模擬。
- 公司事件採「保留市值」處理，現金股利入帳的時間差沒有模擬。
- 基準指數是價格指數，不含股利。
- 評分分位 vs 未來報酬驗證，以及更多策略：待驗收後再做。
- 基本面回測：`fundamental_backtest_ready=false`，未開放。
