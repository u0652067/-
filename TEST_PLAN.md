# TEST_PLAN

## 原則
- 每個階段都必須有可重跑的自動化測試；「沒有測試」= 未完成。
- 單元測試用純函式與已知答案（參考值或獨立實作對照）。
- 資料庫相關測試跑在真的 PostgreSQL（`aqt_test`，每個測試重建 schema 並執行全部 migration），因為稽核規則寫在觸發器裡。
- 外部來源以 `httpx.MockTransport` 離線模擬；合成資料全部標註 `SYNTHETIC`，只存在 `backend/tests/fixtures/`。
- 真實來源另有 `check-sources` 煙霧測試，部署後每日由健康檢查執行。

## 執行
```bash
cd backend && python -m pytest -q        # 全部
python -m pytest -q tests/test_pipeline.py -k safe_mode
```

## PHASE 0/1 測試清單（目前 75 項，全部通過）
| 檔案 | 驗證內容 |
|---|---|
| `test_rules_parsing_pit.py` | 數字/民國日期/漲跌符號解析；跳動點 11 個邊界；漲跌停 6 例（含跨跳動點級距）；手續費最低 20 元；證交稅三種稅率；營收與財報 available_at（含 Q4 跨年） |
| `test_indicators.py` | RSI 對照 Wilder 參考序列；KD、MACD 對照獨立迴圈實作；布林對照 numpy；ATR；**因果性：加上未來 K 棒不改變過去指標值**；量比不含當日 |
| `test_sources.py` | TWSE 行情（新格式、舊 fields9 格式、無資料日）、指數漲跌正負號、停牌列；T86（外資欄位不誤抓外資自營商）；融資券（略過摘要表）；本益比 "-"；除權息；休市日（最後交易日不是休市）；TPEx 行情新舊格式、法人 24 欄、融資券、本益比；ISIN Big5；MOPS 營收/損益/資產負債/歷史 HTML；MIS 延遲秒數 |
| `test_db_audit.py` | 模擬限定 CHECK；決策只能新增（UPDATE/DELETE/TRUNCATE 皆拒絕）；雜湊鏈連續；**繞過觸發器竄改可被雜湊驗證偵測**；委託理由不可改但狀態可推進；策略版本參數不可改不可刪；原始回應內容不可改 |
| `test_pipeline.py` | 單日更新（10 檔、法人、融資券、本益比、指數、行事曆、available_at）；原始回應 sha256 可驗證；休市日/週末略過；重跑冪等；100 日回補 → 昨收、漲跌停、除息參考價、還原因子、除息日不誤報、MA60 暖機、MA5 數值；股票池排除原因；**股票池無未來資料**；行情 500 → 安全模式 → 隔日恢復；**回應日期不符被拒**；筆數驟減 → 安全模式；OHLC 錯誤被標記；**中途消失（下市）股票保留歷史** |
| `test_api_scheduler.py` | API 端到端（health、stocks、prices 原始/還原、indicators、coverage、sources、jobs、institutional、404）；**重啟補跑**（只補缺的日子、第二次不重跑）；排程工作註冊 |

## 尚未能測（需可連線主機）
- 真實交易所回應格式（`check-sources`）。
- 長時間回補效能。
- Docker Compose 實際啟動（開發環境無 Docker daemon）。

## 後續階段測試重點
- PHASE 2：已知答案回測（單一股票、固定訊號，手算損益）；洩漏偵測（把未來資料打亂，績效不得改變）；成本敏感度。
- PHASE 3：資金守恆（現金 + 持倉成本 + 已實現 − 成本 = 初始本金 + 損益）；每個使用者設定改變行為；漲跌停無法成交；委託 ≠ 成交；T+2 交割；安全模式拒單。
- PHASE 4：一個完整交易日模擬（含「不交易」）；Claude 回應異常時退回保守行為；預算上限。
- PHASE 5：Playwright E2E、手機版面、無資料狀態。
- PHASE 7：重啟、資料中斷、時鐘跳動、長時間運作（20 交易日）。
