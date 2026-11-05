# PR #7 合併後的沙箱重演計畫與已知限制

狀態：計畫，尚未執行。必須在 PR #7 合併之後才能跑，而且需要你核准合併。在這之前，正式庫維持 0003。

## 為什麼要重演
目前的演練把驗證拆成兩半：

| 驗證項目 | 用的映像 |
|---|---|
| 升級、API、scheduler、補抓 | `aqt-backend:main-cbd07ba67732`（正式 main，但沒有防護程式） |
| 防護、migrate.ps1、回復 | `aqt-backend:pr-882e318eec03`（有防護，但不是 main） |

正式 migration 會用的「含防護的 main 映像」現在還不存在，所以兩半還沒有在同一個映像上驗證過。

## 前提
1. PR #7 已由你合併。若 PR #9 也核准，等它合併後再做，目標版本由 `0005` 改成 `0005a`。
2. `.\scripts\build-release.ps1` 建立 `aqt-backend:main-<新 commit>`，紀錄 tag、commit、Image ID。
3. 用當時最新、且在服務停止後完成驗證的備份。**這一步要短暫停止正式 api、scheduler，需要你另外核准。** 若不核准，可沿用 `aqt_20261105_002745_pre_rehearsal2.dump`，代價是資料稍舊，且 `system_logs` 差 1 列要說明。

## 步驟
全部在獨立 compose 專案 `aqtsb` 執行，有自己的 PostgreSQL、volume、連接埠與資料夾。不碰正式環境，只讀正式狀態做前後比對。除了專案名稱、volume 與連接埠，腳本不修改；因為映像是 `main-` 標籤，不需要放寬標籤檢查。

| # | 步驟 | 通過條件 |
|---|---|---|
| 1 | 紀錄正式環境狀態 | — |
| 2 | 還原備份到沙箱 | 版本 0003；全表稽核 T0 |
| 3 | `AQT_IMAGE=新映像`，執行 `restore.ps1` | 還原成功，拒絕啟動服務 |
| 4 | `start.ps1`；一般 `compose up` | 都拒絕，版本維持 0003 |
| 5 | `backup.ps1 -Verify` | 全部資料表筆數相同，`ok=true` |
| 6 | `migrate.ps1` 無核准、無備份、備份未驗證 | 三種都拒絕 |
| 7 | `migrate.ps1 -Backup ... -IHaveApproval` | 升級成功 |
| 8 | 全表比對 T0 vs 升級後 | 既有資料表全部相同，新表只有預期的 |
| 9 | `rollback-check`、`-DowngradeTo 0003` | 允許；結構與資料相同 |
| 10 | 再升級，`start.ps1` | API ok；`build_commit` 等於新 commit；`schema.match=true` |
| 11 | 刪除沙箱最後 3 筆 `daily_update` 紀錄，重啟 scheduler | 真實補抓 SUCCESS，運行 5 分鐘無重啟 |
| 12 | `update-daily`、`integrity-check` | exit 0，readiness 符合預期 |
| 13 | 寫入一筆回測 | `rollback-check` exit 4，`-DowngradeTo` 拒絕 |
| 14 | 切回 `aqt-backend:main-7a89d6da132b`，`restore.ps1 -NoStart` | 全表相同，`backtest_runs` 0 筆 |
| 15 | `start.ps1` 用 0003 映像 | API ok，scheduler 正常 |
| 16 | 紀錄正式環境狀態並清理沙箱 | 與步驟 1 相同 |

任一步驟失敗就停止，不進入正式 migration，修正後整份重跑。

預估時間 40～60 分鐘。避開 17:45、19:30、22:15、07:40，因為它會用到真實來源。

## 產出
新的演練紀錄、映像對照表、全表比對 CSV、更新後的 MIGRATION_PLAN 第 2、3 節。之後才進入正式 migration 的核准。

## 已知限制
1. **防護還沒有在正式環境生效。** 目前正式容器是舊映像，舊的 compose 設定已被工作目錄取代，但容器本身沒有重建。合併 PR #7 前後，在正式環境執行 `start.ps1` 都會替換容器，只能在核准的時間窗做。
2. **PowerShell 腳本與 compose 沒有單元測試。** 只靠沙箱演練，腳本改動後必須重跑。第一輪已經證明單元測試抓不到這類錯誤。
3. **沙箱與正式環境不完全相同。** 沙箱是全新容器，沒有驗證「舊容器切換成新映像」本身，也沒有等到實際排程時間點，只驗證補抓與工作註冊。
4. **`rollback-check` 只由 `migrate.ps1` 把關。** 直接執行 `alembic downgrade` 不受限制，因為不能修改已發布的 migration。靠規則與文件禁止。
5. **映像只在本機。** 沒有推到 registry，digest 以本機 Image ID 為準。映像遺失時可用同一 commit 重建，但 Image ID 不保證相同。
6. **備份驗證必須在服務停止時做。** 服務運行中驗證會因為 `system_logs` 差異而失敗；`migrate.ps1` 會拒絕這種備份。
7. **`table-audit` 不含二進位欄位內容。** `raw_payloads.content_gz` 由旁邊的 sha256 欄位代表，`integrity-check` 只抽樣驗證 sha256。
8. **內容雜湊不是密碼學摘要。** 每列用 PostgreSQL `hashtextextended` 算 64 位元再加總，足以偵測意外差異，不足以防止蓄意偽造。
9. **Docker Desktop 必須開著。** 排程跟著它停止，重新開啟後補抓只回看有限天數。
10. **部署會中斷執行中的工作。** 2026-11-02 就是這樣發生的。目前靠時間窗規則避免，程式沒有在排程工作執行時擋下 `start.ps1`。
