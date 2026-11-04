# 量化研究流程（PHASE 2）

> 研究用途。本文件描述流程，不代表任何策略或因子有效、可獲利或可交易。只用技術面資料；`fundamental_backtest_ready=false` 期間不使用財報。

## 流程
1. **登錄候選**（`research/registry.py`）：先寫下假說、參數（不可修改的策略版本）與評估計畫，才開始評估。同一版本不能換計畫；要改就登錄新版本。
2. **因子研究**（`research/factors.py`）：先看訊號本身是否有統計關聯，不把回測報酬當成唯一依據。
3. **回測、樣本外、Walk-forward**（`research/evaluation.py`）：照計畫跑，全部寫入不可竄改紀錄。
4. **晉級審查**（`research/promotion.py`）：用事先宣告的規則判斷是否通過流程關卡，通過與未通過都留紀錄。

## 因子驗證
- 抽樣日 T：間隔 ≥ 持有天數，所以樣本期間不重疊。
- 股票池與因子值：T 日 18:00 的 point-in-time DataView。
- 結果變數：T+1 開盤到 T+1+h 開盤的總報酬（**未扣成本**）。
- T+1 開盤漲停買不到的股票不列入，並計數。
- 期間內停止交易的股票不刪除，以最後收盤價計算到當時為止的報酬，並計數，避免倖存者偏差。
- 每日統計：rank IC（Spearman）、各分位平均報酬、頂減底。
- 跨日統計：平均 IC、以日期為單位的 t 值、IC 為正的比例、單調性，並依年度、市場環境、前後期（前 70%／後 30%）分組。
- 多重檢定：同一族檢定用 Holm 校正。
- 標籤只有描述性質：`INSUFFICIENT_SAMPLE`、`NO_STATISTICAL_EVIDENCE`、`UNSTABLE_ACROSS_SAMPLES`、`STATISTICAL_ASSOCIATION_BEFORE_COSTS`。**沒有代表「有效」的標籤。**
- 因子：`mom_20`、`mom_60`、`reversal_5`、`trend_ma20`、`trend_ma60`、`low_vol_20`、`volume_surge`，以及綜合分數 `score_composite`（`mom_60`、`trend_ma60`、`low_vol_20` 的平均百分位）。
- 紀錄：`factor_studies`（migration 0006），只能新增，保存資料指紋與結果雜湊。

## 市場環境
`research/regimes.py`。第 d 日的標籤只用 d 日（含）以前的 TAIEX：
- 趨勢：收盤價對 200 日均線（BULL／BEAR）
- 波動：20 日實現波動對其過去中位數（LOW_VOL／HIGH_VOL）
- 資料不足時為 UNKNOWN

第 d+1 日的報酬歸入 d 日收盤已知的環境。環境只用來切分結果，不提供給策略做決策。分組結果寫入 `backtest_runs.regime_metrics`。

## 晉級規則（`RULES_VERSION = 2026-11-05.1`）
階段是**流程關卡**，不是評價：`CANDIDATE → BACKTESTED → OOS_REVIEWED → WF_REVIEWED`。
- 不可跳級。`PAPER`、`ACTIVE` 在 PHASE 2 一律拒絕。
- 未通過也寫入 `strategy_stage_changes`，階段不變。

**回測只有全部符合才算證據**（`evidence_problems()`）：
- `usable_for_evaluation=true`：沒有凍結或無法交易的持股
- `benchmark_comparable=true`：基準市場範圍與策略一致
- 不是 `NEEDS_RERUN`
- 資料指紋與執行當時相同：資料修訂過就不算
- 未使用財報
- 有股利模式與資料 manifest
- 屬於同一個策略版本
- Walk-forward 彙總：每一段測試都要符合

| 關卡 | 回測類型 | 最少交易 | 最短期間 | Sharpe | 超額報酬（對可比較基準） | 其他 |
|---|---|---:|---:|---:|---:|---|
| BACKTESTED | IN_SAMPLE | 100 | 3 年 | > 0 | — | |
| OOS_REVIEWED | OUT_OF_SAMPLE，測試段 | 30 | 0.5 年 | > 0 | > 0 | |
| WF_REVIEWED | WALK_FORWARD 彙總 | 100 | 2 年 | > 0 | > 0 | ≥ 4 段、≥ 50% 段為正 |

門檻是流程條件，不是績效保證。修改門檻要更新 `RULES_VERSION`，並寫入 CHANGELOG。

## 指令
```bash
python -m aqt.cli factor-study --start 2019-10-01 --end 2026-10-31 --horizon 20
python -m aqt.cli research-registry
python -m aqt.research.research_run 2019-01-01 2026-10-31   # 只在暫存複本上執行
```
