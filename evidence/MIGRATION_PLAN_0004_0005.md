# 正式資料庫 Migration 0004／0005 — 計畫、演練與回復政策

狀態：**尚未套用，也尚未核准**。正式資料庫維持 `0003`，正式 api／scheduler 容器沒有被重啟或重建。未經使用者明確核准，不會執行第 4 節。

範圍只有 main 帶入的 0004、0005。研究分支的 0006 不在這次範圍。

## 1. 目前部署狀態（已修正的風險）
| 風險（舊版） | 修正後 |
|---|---|
| `start.ps1`、`compose up` 會自動升級 | `migrate` 改放 `migrate` profile，`up` 不會執行；`api`／`scheduler` 只依賴 `db`；`init-db` 必須帶 `--apply-migrations` |
| 版本不符時靜默升級 | `start.ps1`、`restore.ps1` 比對資料庫版本與映像 head，不符就拒絕；含本修正的映像，`api`、`scheduler` 與資料庫指令本身也拒絕啟動 |
| 共用 `aqt-backend:local` 可能是研究分支（含 0006） | 正式服務沒有 `build:`，`AQT_IMAGE` 必須是 `aqt-backend:main-<12 碼 commit>`，標籤與映像內的 revision label 必須一致 |
| `restore.ps1` 結尾自動 migrate | 不再 migrate；版本不符時服務維持停止；`-NoStart` 可還原後先驗證 |
| 備份只比對 12 張表 | `backup.ps1 -Verify` 比對全部資料表；`table-audit` 另比對內容雜湊 |

`.env` 目前的 `AQT_IMAGE=aqt-backend:main-7a89d6da132b`，對應正式庫的 0003。這只影響之後的 compose 指令，不影響正在執行的容器。重開機時，現有容器會用原本的映像恢復，不會 migrate。

## 2. 映像
| 用途 | 標籤 | Commit | Image ID | Schema head |
|---|---|---|---|---|
| 回復用（0003） | `aqt-backend:main-7a89d6da132b` | `7a89d6da132bd536ff52b6b42f4781ed08c450fc` | `sha256:8f54d2bf75fe2933a33202b9bd7ea20814552f997cba3dbec8837ec70c70c989` | 0003 |
| 演練用的正式 main | `aqt-backend:main-cbd07ba67732` | `cbd07ba6773285cfe12cc49aa014f157542da988` | `sha256:594d1b682fbf79fa3b60d9246b7c140a4a55bd8306260055520cdf9aa3c4d5ec` | 0005 |
| 本 PR（只驗證防護） | `aqt-backend:pr-882e318eec03` | `882e318eec034b7cfb49af78971a8da261ae3ea5` | `sha256:55db0bcf48d630305412f042fa80045736d7d69bb5b8b9e15d4bbbea55021683` | 0005 |

這些都是本機映像，沒有推到 registry，所以 digest 以本機 Image ID 為準。

**實際套用時的映像**：main 目前的 `cbd07ba` 還沒有防護程式，也沒有 `init-db --apply-migrations`。本 PR 合併後，要用合併後的 main commit 重新執行 `build-release.ps1`，得到新的 `aqt-backend:main-<新 commit>`。第 4 節用的是那個映像，不是 `cbd07ba`。0004、0005 的 migration 檔案兩者完全相同。

## 3. 演練 2（2026-11-05）
完整輸出：`evidence/2026-11-05_deploy_safety_rehearsal/`（`rehearsal2_log.txt`、`rehearsal2b_log.txt`、腳本、各階段 `audit_*.json`）。

**環境**：獨立 compose 專案 `aqtsb`，有自己的 PostgreSQL、volume、連接埠與資料夾。跟正式環境的差異只有專案名稱、volume 名稱、連接埠，以及接受 `pr-` 標籤。對正式環境只做 `pg_dump` 和唯讀稽核。

**第一輪發現的問題**：`Assert-ReleaseImage` 用的 `docker inspect` 格式字串被 PowerShell 5.1 吃掉引號，所有映像都被當成不存在。第一輪 B2、B4～B7 的「拒絕」因此是錯誤原因，**不算數**。修正後，全部腳本步驟在 `rehearsal2b_log.txt` 重跑一次，下表腳本步驟都以第二輪為準。

| 步驟 | 映像 | 結果 | 耗時 |
|---|---|---|---:|
| 正式庫備份 | — | `aqt_20261105_002745_pre_rehearsal2.dump`，1,545.8 MB，sha256 `4f13f1f6…3988`；43 張表、19,449,260 列 | 約 7 分（含還原驗證 11.5 分） |
| 備份驗證（服務運行中） | — | `system_logs` 差 1 列，判定未通過。原因是正式服務在備份後又寫入一筆心跳。**所以正式 migration 必須先停服務** | — |
| 還原到沙箱 | — | 0003 | 2.9～3.7 分 |
| 全部資料表比對：還原 vs 正式庫 | PR | 42 張表，只有上述 `system_logs` 不同；其餘筆數與內容雜湊相同 | 14 秒 |
| 升級 0003→0005 | main `cbd07ba` | 成功 | 2.4 秒 |
| 全部資料表比對：升級前 vs 後 | PR | 42 張表、19,449,258 列的既有欄位內容**完全相同**；新表只有 `backtest_run_invalidations` | 12 秒 |
| 新欄位與觸發器 | — | 5,837 筆 NULL 為 0；觸發器 28 個；三張表的只能新增與禁止 TRUNCATE 都在 | — |
| API 在 0005 啟動 | main `cbd07ba` | `/api/health` status ok，`realtime_quotes` 維持不可用 | — |
| scheduler 在 0005 啟動＋補抓 | main `cbd07ba` | 刪除沙箱最後 3 筆 `daily_update` 後啟動，補抓真實官方資料 SUCCESS（79 秒），排程器 6 個工作註冊完成，運行 7 分鐘無重啟 | 79 秒 |
| `update-daily --date 2026-11-04` | main `cbd07ba` | exit 0 | 78 秒 |
| `integrity-check` | main `cbd07ba` | 執行成功，品質 `REVIEWED_WITH_EXCLUSIONS`，未分類 0；覆蓋率有 1 個缺口（殘留工作），見第 7 節 | 8 秒 |
| `restore.ps1` 用 0005 映像 | PR | 還原完成，拒絕啟動服務，版本 0003 | — |
| `start.ps1` 版本不符 | PR | 拒絕，沒有啟動服務，版本 0003 | — |
| 一般 `docker compose up` | PR | api、scheduler 報錯退出並重試，版本維持 0003，沒有 migrate | — |
| 服務停止後備份驗證 | — | 43 張表筆數完全相同，`ok=true` | 9 分 |
| `migrate.ps1` 無核准旗標／無備份／備份未通過驗證 | PR | 三種都拒絕，版本 0003 | — |
| `migrate.ps1` 正常升級 | PR | 成功；全部資料表比對相同 | 7.9 秒 |
| 無紀錄時的 downgrade | PR | `rollback-check` 允許；降回 0003；結構與原 0003 相同（只差 `\restrict` 亂數）；全部資料表比對相同 | 5.9 秒 |
| 再升級並用 `start.ps1` 啟動 | PR | API ok，`/api/health` 顯示 `build_commit` 與 `schema.match=true`；scheduler 運行 2 分鐘無重啟 | — |
| 寫入一筆正式回測後 | PR | `rollback-check` exit 4；`migrate.ps1 -DowngradeTo` 拒絕；版本維持 0005 | — |
| **標準回復：從備份還原** | `main-7a89d6da132b` | 還原後 0003，`backtest_runs` 0 筆，全部資料表比對相同；`start.ps1` 用 0003 映像啟動，API ok，scheduler 正常 | 3.1 分 |
| 演練前後的正式環境 | — | api、scheduler、db 的映像、啟動時間、重啟次數完全相同，版本 0003 | — |

**尚未驗證**：
- 含防護的 **main** 映像。這要等本 PR 合併後才存在。上表防護步驟用 PR 映像，migration、API、scheduler 同時用正式 main 映像驗證過。
- 正式環境實際從舊容器切換到新映像。沙箱是全新容器。
- 下一個實際排程時間點（17:45、19:30、22:15、07:40）的觸發。演練只驗證補抓與工作註冊。

## 4. 套用步驟（待核准）
時間窗：非交易時段，避開 17:45、19:30、22:15、07:40。預估停機約 20 分鐘。
```powershell
# 0. 本 PR 合併後，建立不可變映像，並先在沙箱重跑一次第 3 節的防護步驟
git fetch origin
.\scripts\build-release.ps1                 # 印出 aqt-backend:main-<新 commit>

# 1. 先停服務，db 保持運行。此時 .env 仍是 0003 映像
docker compose stop scheduler api

# 2. 服務停止後做完整備份與還原驗證（約 10 分鐘）
.\scripts\backup.ps1 -Verify -Label pre_migration_0005

# 3. 升級前的全部資料表稽核（用含防護的新映像，稽核不受版本限制）
$env:AQT_IMAGE = 'aqt-backend:main-<新 commit>'
docker compose run --rm --no-deps api python -m aqt.cli table-audit --out /data/reports/audit_before.json

# 4. 把 .env 的 AQT_IMAGE 改成新映像，然後只做 migration
.\scripts\migrate.ps1 -Backup backups\aqt_<時間>_pre_migration_0005.dump -IHaveApproval

# 5. 驗證（第 5 節）。全部通過才繼續
docker compose run --rm --no-deps api python -m aqt.cli table-audit --out /data/reports/audit_after.json --columns-from /data/reports/audit_before.json
docker compose run --rm --no-deps api python -m aqt.cli table-compare /data/reports/audit_before.json /data/reports/audit_after.json

# 6. 啟動
.\scripts\start.ps1
```

## 5. 套用後驗證
| # | 項目 | 預期 |
|---|---|---|
| 1 | `.\scripts\migrate.ps1 -Status` | 資料庫與映像都是 0005 |
| 2 | `table-compare` | **所有**既有資料表 `identical=true`；新表只有 `backtest_run_invalidations` |
| 3 | `data_quality_issues` 三個新欄位的 NULL | 0 |
| 4 | 觸發器 | 28 個；`backtest_runs`、`backtest_trades`、`backtest_run_invalidations` 都有只能新增與禁止 TRUNCATE |
| 5 | `integrity-check --end 2026-10-31` | COMPLETE；technical true；fundamental false（截止日較晚時見第 7 節） |
| 6 | `/api/health` | `status=ok`、`schema.match=true`、`build_commit` 等於映像 commit |
| 7 | api、scheduler 容器 | 運行 5 分鐘以上且無重啟；scheduler 排程已註冊 |
| 8 | 補抓 | 停機期間若漏掉工作，`daily_update` 為 SUCCESS |
| 9 | 下一個交易日的 `daily_update` | SUCCESS；新品質問題帶 `effective_at`，未分類為 0 |

第 1～7 項任一失敗就停止服務，回到第 6 節。

## 6. 回復政策
**判斷方式**：一律先跑 `.\scripts\migrate.ps1 -Status` 與 `rollback-check`。

### 6.1 只有尚未寫入任何回測／稽核紀錄時，才可以 downgrade
條件：`backtest_runs`、`backtest_trades`、`backtest_run_invalidations`、人工或事後的 `data_quality_issues`（`detection_method<>'AUTO_RULE'`）、`strategy_versions`、`strategy_stage_changes` 都沒有資料。`rollback-check` exit 0 才算成立。
```powershell
.\scripts\migrate.ps1 -DowngradeTo 0003 -IHaveApproval
# 把 .env 的 AQT_IMAGE 改回 aqt-backend:main-7a89d6da132b
.\scripts\start.ps1
```
不得直接執行 `alembic downgrade`。`rollback-check` 只在 `migrate.ps1` 把關。

### 6.2 寫入任何正式紀錄之後：唯一標準回復是從 pre-migration 備份還原
一旦有正式回測、資料指紋、結果雜湊或失效紀錄，**不可 downgrade**，`migrate.ps1` 會拒絕。
```powershell
# 把 .env 的 AQT_IMAGE 改回 aqt-backend:main-7a89d6da132b
.\scripts\restore.ps1 -File backups\aqt_<時間>_pre_migration_0005.dump -Force -NoStart
# 用第 4 節的 audit_before.json 比對全部資料表，相同才繼續
.\scripts\start.ps1
```
代價要先知道：
- 備份之後才寫入的所有資料會消失，包括回測紀錄本身。這些紀錄要另外保存報告後才可以回復。
- 備份之後的行情由排程補抓。補抓只回看有限天數，超過要用 `update-daily --date`。
- `restore.ps1` 預設會先做一份安全備份，保留回復前的狀態供稽核。

### 6.3 downgrade 會遺失的稽核資料
**0005 → 0004**
- `data_quality_issues.known_at`、`effective_at`、`detection_method`：每筆排除何時可知、何時生效、如何發現。沒有這些欄位，排除條件無法再以時點正確的方式重建。
- `backtest_runs.benchmark_comparable`：基準範圍是否一致。
- 整張 `backtest_run_invalidations`：所有 `NEEDS_RERUN` 標記。

**0004 → 0003**
- `backtest_runs`：`config`、`params_hash`、`data_version`、`data_version_hash`、`data_manifest`、`results_hash`、`engine_version`、`started_at`、`finished_at`、`parent_run_id`、`disclaimer`、`dividend_mode`、`benchmark_index`、`benchmark_kind`、`usable_for_evaluation`、`evaluation`。
- `backtest_trades`：`entry_costs`、`exit_costs`、`gross_pnl`。
- `backtest_runs`、`backtest_trades` 的只能新增與禁止 TRUNCATE 觸發器。

downgrade 不刪回測紀錄列，但留下的列已無法重現、無法驗證、也失去不可竄改保護。這就是 6.2 禁止 downgrade 的原因。

## 7. 核准前要知道的事
1. **2026-11-02 殘留 RUNNING 工作（`job_runs.id=2065`）**

   **更正**：先前本文件寫「應該是 Docker Desktop 被關閉」，這是錯的。實際原因是我在 2026-11-02 22:15:34（台北）為了部署缺口修正執行 `start.ps1`，替換了 scheduler 容器；該工作 22:15:00 開始，正在執行中。scheduler 日誌顯示 22:15:50 重新啟動。

   **查核結果**（PR #9 的 `reconcile-job`，對正式庫唯讀執行，`evidence/2026-11-05_job_2065_reconciliation/verify_job_2065_production_readonly.json`）：
   - 工作自己的 8 份原始回應都在，HTTP 200、sha256 相符、解析 OK。
   - 8 組資料逐列比對，共 8,838 列（先前寫的 9,840 是我算錯，已更正）：回應筆數 = 資料庫筆數 = 來自這份回應的列數，數值差異 0。
   - **遺漏**：回應有而資料庫沒有 0 列，資料庫有而回應沒有 0 列。**重複**：回應內 0、資料列 0。
   - **實際寫入範圍**（依 `raw_payload_id`）：行情 2,407、法人 2,227、融資券 2,224、估值 1,980、指數 267、公司事件 1；以及 22:15:37 的品質問題 5 筆。沒有新增 securities。
   - **一個已知的重複**：這 5 筆品質問題與 17:45 那 5 筆內容相同，同一天有 10 筆。這不是這次中斷造成的，而是 `save_issues` 每次重跑都新增。全庫共 5,837 筆，不重複 5,774 筆。它們是 `NO_CLOSE_FIXED_PRICE_ONLY`，不阻擋回測。稽核表只能新增，所以不刪除。
   - 融資券在 17:45 還是空的，由這次工作寫入：上市 1,305、上櫃 919 列。法人也是這次的修訂版。
   - 行情與本益比回應與 17:45 成功工作完全相同，所以 17:45 算出的 2,353 筆技術指標仍然正確。
   - 品質問題 10 筆，未分類 0。工作在寫完品質問題後被中斷，沒執行到的只有重算指標、股票池與安全模式檢查；前兩者輸入沒變，隔日工作已成功。
   - 結論：`DATA_COMPLETE`。

   **可重現**：`python -m aqt.cli reconcile-job --job-id 2065 --out <檔案>`，唯讀交易，可在任何 schema 執行。證據內含查核方法。

   **處理方式**（PR #9，待審）：
   - 不修改 `job_runs`，第 2065 列永遠維持 RUNNING。
   - 新增只能新增的 `job_reconciliations`（migration `0005a`），記錄結論、理由、證據、證據雜湊、時間與執行者。`integrity-check` 只有在**該工作**有 `DATA_COMPLETE` 紀錄時才放行；`DATA_INCOMPLETE`、或同一天又有新的殘留工作，都仍視為缺口。
   - **截止日邏輯**：新增 `technical_ready_through`。回測的結束日不晚於這個日期就可以跑，所以把檢查截止日往後移，不會取消較早視窗的資格；結束日晚於它一律拒絕。同時補上原本的漏洞：回測結束日晚於已檢查視窗時，現在也會拒絕。

   **沙箱驗證**（完整複本，`sandbox_reconcile_log.txt`）：紀錄前 `technical_ready_through=2026-11-01`，結束於 10-30 的回測可跑，結束於 11-03 的回測被拒絕。紀錄後 `technical_backtest_ready=true`，`job_runs` 第 2065 列完全不變，UPDATE、DELETE 都被觸發器拒絕。

   **目前的 append-only 紀錄**：你禁止套用 migration，所以正式庫不能建 `job_reconciliations`。在這之前，紀錄保存在 git 內只能新增、以雜湊串接的 `evidence/2026-11-05_job_2065_reconciliation/reconciliation_ledger.jsonl`，含原因、方法、結果、時間與操作者。這是資料庫外的暫行紀錄，不會讓 `integrity-check` 放行。

   **尚未完成**：正式庫還沒有這筆稽核事件，`job_runs` 與 readiness 也都沒有動，所以截止日含 11-02 以後的檢查在正式庫仍是未就緒，符合「未核對完成維持不可用」。核准 PR #9 與 migration 後，用以下指令寫入，寫入時會重新驗證一次：
   ```powershell
   docker compose run --rm --no-deps api python -m aqt.cli reconcile-job --job-id 2065 --record --reason "<理由>" --by "<執行者>"
   ```
   若核准 PR #9，第 4 節的目標版本會由 `0005` 變成 `0005a`，第 5 節的預期版本與觸發器數（30 個）也跟著改；PR #8 的 `0006` 要改接在 `0005a` 之後。

   **避免再發生**：部署與 `start.ps1` 不得在 17:45、19:30、22:15、07:40 前後執行（第 3 節的時間窗本來就排除）。這次是我違反了這個原則。

2. **`effective_at` 回填值是規則推定**（各交易日 17:45），實際寫入時間保留在 `created_at`。PR #5 已通過，我視為接受。
3. **`aqt-backend:prod-0003` 已由 `aqt-backend:main-7a89d6da132b` 取代。** 內容同為 main `7a89d6d`，舊標籤不再使用。

## 8. 需要你核准的事
1. 審查並合併本 PR。
2. 合併後，我建立新的 main 映像，在沙箱重跑防護步驟。
3. 是否在第 4 節的時間窗套用 0004、0005，以及日期。

演練 1（2026-11-04）的紀錄保留在 `evidence/2026-11-05_migration_rehearsal/`，已被本文件取代。
