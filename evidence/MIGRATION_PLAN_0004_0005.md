# 正式資料庫 Migration 0004／0005 — 演練結果與套用計畫

狀態：**尚未套用**。正式資料庫維持 `0003`。本文件是核准前的資料；未經使用者明確核准，不會執行第 4 節的步驟。

> 本文件只涵蓋 PR #6（`claude/phase2-backtest`）帶入的 0004、0005。研究分支的 0006 不在這次範圍。

## 1. 暫存複本演練結果（2026-11-04 實際執行）
完整紀錄：`evidence/2026-11-05_migration_rehearsal/rehearsal_log.txt`

演練用的是當時正式資料庫的完整複本，映像來自 `claude/phase2-backtest` @ `3fdad77`。

| 步驟 | 結果 | 耗時 |
|---|---|---:|
| 備份正式庫（`backup.ps1`） | `aqt_20261104_230051_pre_migration_rehearsal.dump`，1,546 MB，sha256 `dac821c7…` | 405 秒 |
| 還原到暫存庫（還原演練） | 成功，版本 0003；12 張表筆數與備份清單完全一致 | 201 秒 |
| 升級 0003 → 0005 | 成功 | **4 秒** |
| 升級前後筆數 | 23 張表完全相同 | — |
| 新欄位 | `known_at`、`effective_at`、`detection_method` 都是 NOT NULL；`backtest_runs` 新欄位都存在 | — |
| 回填 | 5,837 筆全部 `AUTO_RULE`；NULL 為 0；不等於「交易日 17:45」的列數為 0 | — |
| 觸發器 | 總數 28；三張表的只能新增觸發器都存在 | — |
| 完整性檢查 | `technical_backtest_ready=true`、`fundamental_backtest_ready=false`、`quality_status=REVIEWED_WITH_EXCLUSIONS` | — |
| 煙霧回測（2024 Q1，寫入紀錄） | 成功，紀錄含股利模式、資料指紋、結果雜湊 | 10 秒 |
| 只能新增的驗證 | UPDATE、DELETE `backtest_runs` 都被觸發器拒絕 | — |
| 降版 0005 → 0003 | 成功 | **2 秒** |
| 降版後結構 | 與 0003 完全相同（`pg_dump -s` 比對。差異只有 `pg_dump` 每次隨機產生的 `\restrict` 標記） | — |
| 降版後筆數 | 既有資料表不變。煙霧回測寫入的 `strategies`、`strategy_versions`、`backtest_runs`、`backtest_trades` 列保留 | — |
| 再次升級 | 成功 | 3 秒 |
| 演練結束時的正式庫 | 版本 0003，全部 23 張表筆數與演練中途相同，沒有被動到 | — |

**影響摘要**
- 兩個 migration 都是新增性質：沒有刪除欄位或資料。
- 唯一改動既有資料的是 0005 回填 `data_quality_issues` 的三個新欄位。
- 尚未驗證：實際部署時的 API／排程重啟流程（演練只跑 migration 與 CLI）。第 5 節會驗證。

## 2. 核准前要先確認的風險
1. **`start.ps1` 會自動套用 migration。** `docker compose up` 的 `migrate` 服務會把資料庫升到映像內最新版。合併 PR #6 之後、核准之前，**不可執行 `start.ps1` 或 `docker compose up`**。
2. **映像標籤已指向新程式。** `aqt-backend:local` 目前是研究分支的映像（含 0006），因為開發測試共用這個標籤。正式環境的 api、scheduler 容器仍在跑舊映像，重開 Docker 也不受影響；但只要重建容器，就會換成新映像並嘗試升級。
3. **回復用的舊映像**：已建立 `aqt-backend:prod-0003`，內容是 main @ `7a89d6d`。
4. **`restore.ps1` 結尾會啟動 `migrate`。** 用新映像還原後會立刻又升級。回復時必須先換回舊映像（見第 6 節 B）。
5. **`effective_at` 回填值是規則推定**（各交易日 17:45），不是實際寫入時間；實際時間保留在 `created_at`。你已通過 PR #5，我視為接受這個做法；如果不接受，要在套用前說。

## 3. 時間窗
- 只在**非交易時段**，而且避開排程：17:45、19:30、22:15、07:40。
- 建議：週末，或平日 22:30 之後、隔日 07:00 之前。
- 預估停機：備份驗證約 15 分鐘 + 升級與驗證約 5 分鐘 = **約 20～25 分鐘**。排程器的補抓機制會處理停機期間漏掉的工作。

## 4. 套用步驟（待核准）
```powershell
# 0. 前提：PR #6 已由使用者合併；工作目錄切到 main 最新版
git fetch origin; git checkout main; git pull origin main

# 1. 停止會寫入資料庫的服務（db 保持運行）
docker compose stop scheduler api

# 2. 完整備份並驗證可還原（還原到暫存庫比對筆數）
powershell -ExecutionPolicy Bypass -File .\scripts\backup.ps1 -Verify -Label pre_migration_0005

# 3. 記錄升級前狀態
docker compose exec -T db psql -U aqt -d aqt -At -c "select version_num from alembic_version"   # 應為 0003

# 4. 用 main 的程式建映像，只執行 migration
docker compose build
docker compose run --rm migrate

# 5. 驗證（第 5 節），全部通過才繼續

# 6. 啟動服務
docker compose up -d --no-deps api scheduler
```

## 5. 套用後驗證項目
| # | 項目 | 預期 |
|---|---|---|
| 1 | `alembic_version` | `0005` |
| 2 | 23 張表筆數 | 與備份清單及升級前一致 |
| 3 | `data_quality_issues`：`known_at`、`effective_at` 的 NULL 數 | 0 |
| 4 | 觸發器總數；`backtest_runs`、`backtest_trades`、`backtest_run_invalidations` | 28；都有只能新增觸發器 |
| 5 | `integrity-check --end <最近交易日>` | coverage COMPLETE；technical true；fundamental false |
| 6 | `/api/health` | `status=ok`，readiness 兩個旗標分開 |
| 7 | 全套測試（拋棄式資料庫） | 全部通過 |
| 8 | 煙霧回測：`aqt.cli backtest`，一季 | 成功，紀錄含資料指紋 |
| 9 | 下一個交易日的 `daily_update` | SUCCESS；新品質問題帶 `effective_at`，未分類為 0 |
| 10 | 只能新增：對 `backtest_runs` UPDATE | 被拒絕 |

第 1～8 項任一失敗就**不啟動服務**，進入第 6 節。

## 6. 回復方案
**先判斷**：資料是否有問題？只有結構或程式問題用 A，資料異常用 B。

**A. 降版（首選，演練 2 秒）** — 適用於還沒有正式回測紀錄、或願意保留紀錄列。
```powershell
docker compose stop api scheduler
docker compose run --rm --no-deps api python -m alembic downgrade 0003
docker tag aqt-backend:prod-0003 aqt-backend:local
docker compose up -d --no-deps api scheduler
```
注意：
- 降版會刪除 `known_at`／`effective_at`／`detection_method`、回測紀錄的新欄位，以及 `backtest_run_invalidations`。
- 回測紀錄列本身會保留，但可重現性欄位會消失。

**B. 從備份還原（最終手段）** — 適用於資料本身有問題。
```powershell
docker compose stop api scheduler
docker tag aqt-backend:prod-0003 aqt-backend:local     # 必須先換回舊映像，否則還原後會自動升級
powershell -ExecutionPolicy Bypass -File .\scripts\restore.ps1 -File backups\aqt_<時間>_pre_migration_0005.dump -Force
```
注意：
- 演練中還原耗時約 3.5 分鐘。
- 還原會回到備份當時的狀態；備份之後抓取的資料要由排程補抓。
- 舊映像不認得 0004 以後的版本號，所以還原或降版後 `alembic_version` 必須是 0003。

## 7. 需要你核准的事
1. 是否合併 PR #6。
2. 是否在第 3 節的時間窗套用 0004、0005，以及你希望的日期。
