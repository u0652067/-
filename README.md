# AI Quant Trader — 台股自主量化研究與模擬交易系統

> ⚠️ 僅限模擬交易。本系統不連接任何券商、不使用真實資金。模擬績效不代表真實投資成果。

目前進度：PHASE 1（台股資料中心）**條件式驗收**，本機（台灣家用 IP）持久化部署中。詳見 [TASKS.md](TASKS.md)。

## 本機部署（Windows + Docker Desktop）— 正式資料環境

雲端開發容器的資料庫是暫存的，**只有這台本機的資料庫是正式資料**。

### 1. 一次性安裝
1. 以系統管理員開 PowerShell：`wsl --install`，重新開機。
2. 安裝 [Docker Desktop](https://www.docker.com/products/docker-desktop/)，啟動到顯示 *Engine running*。建議在 Settings → General 勾選開機自動啟動。
3. 安裝 Git：`winget install Git.Git`（之後 `git pull` 更新程式）。
4. 若 PowerShell 不允許執行腳本：`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`。

### 2. 指令（在專案根目錄執行）
| 動作 | 指令 |
|---|---|
| 建立正式映像（只接受 main 上的 commit） | `.\scripts\build-release.ps1`，把印出的標籤寫進 `.env` 的 `AQT_IMAGE` |
| 啟動（db + api + scheduler，不建置、不 migration） | `.\scripts\start.ps1` |
| 查看資料庫與映像的 schema 版本 | `.\scripts\migrate.ps1 -Status` |
| 套用 migration（經核准後才可執行） | `.\scripts\migrate.ps1 -Backup backups\<已驗證備份>.dump -IHaveApproval` |
| 停止（資料保留） | `.\scripts\stop.ps1`（`-Remove` 會移除容器，但**不會**刪資料） |
| 來源健康檢查 | `.\scripts\health.ps1` |
| 備份 | `.\scripts\backup.ps1`（`-Verify` 會還原到暫存資料庫，比對**每一張**資料表的筆數） |
| 還原 | `.\scripts\restore.ps1 -File backups\aqt_YYYYMMDD_HHMMSS.dump -Force`（`-NoStart` 還原後不啟動服務） |
| 小批次回補驗收 | `.\scripts\backfill.ps1 smoke -Start 2024-01-02 -End 2024-01-12` |
| 全量回補（2019 起，背景、可續傳） | `.\scripts\backfill.ps1 start`（先自動做驗證過的備份與完整性基準） |
| 回補進度／停止／日誌 | `.\scripts\backfill.ps1 status` ／ `stop` ／ `logs` |
| 完整性報告 | `.\scripts\backfill.ps1 report` |
| 自動化測試（拋棄式資料庫） | `.\scripts\test.ps1` |

第一次執行 `start.ps1` 會從 `.env.example` 產生 `.env` 並填入隨機資料庫密碼。API：<http://127.0.0.1:8000/docs>（只綁本機）。

### 2.1 部署安全規則
- **migration 不會自動執行。** `start.ps1`、`docker compose up`、重開機、服務重啟都不會升級資料庫；`migrate` 服務放在獨立 profile，只由 `migrate.ps1` 執行，且必須有已驗證的備份與 `-IHaveApproval`。
- **版本不符就拒絕。** `start.ps1` 與 `restore.ps1` 比對資料庫版本和映像內的 migration head，不符就不啟動服務；含本修正的映像，`api`、`scheduler` 與其他資料庫指令本身也會拒絕啟動。
- **正式映像不可變。** `AQT_IMAGE` 必須是 `aqt-backend:main-<commit 前 12 碼>`。映像由 `git archive` 取該 commit 建置，不受目前分支或本機修改影響，標籤不會被覆蓋。不再有共用的 `aqt-backend:local`；`docker-compose.yml` 的正式服務沒有 `build:`。
- **回復政策。** `alembic downgrade` 只能在尚未寫入任何回測／稽核紀錄時使用（`rollback-check` 把關）；之後唯一標準回復方式是從 pre-migration 備份還原。不要直接執行 `alembic downgrade`。詳見 `evidence/MIGRATION_PLAN_0004_0005.md`。

### 3. 資料存放（容器重建不會遺失）
| 內容 | 位置 |
|---|---|
| PostgreSQL（行情、原始回應 `raw_payloads`、排程狀態 `job_runs`/`system_state`、回補進度） | Docker volume `aqt_pgdata`（external，`docker compose down -v` 也刪不掉） |
| 原始官方回應第二份（gzip，含 sha256 於檔名） | `data\raw\<source>\<dataset>\<year>\` |
| 日誌（每個指令一個檔，每日輪替保留 90 天） | `data\logs\` |
| 健康檢查、完整性報告（JSON + Markdown） | `data\reports\health\`、`data\reports\integrity\` |
| 資料庫備份（`.dump` + `.dump.json` 清單：sha256、筆數） | `backups\` |

刪除資料只有兩種方式，都需要手動：`docker volume rm aqt_pgdata`、刪除 `data\` / `backups\` 資料夾。

### 4. 全量回補怎麼續傳
- 進度每處理完一天就寫入資料庫（`system_state.backfill_full`）；容器被停止、電腦重開機都會從中斷處繼續（服務設 `restart: on-failure`，Docker Desktop 開機自動啟動即可）。
- 已完成的月份跳過；未完成的月份只抓尚未入庫的資料集（`only_missing`）。
- 來源回「FOR SECURITY REASONS」時暫停 15 分鐘 × 次數後重試當日，最多 3 次，不會硬打。
- 順序：除權息 → 每日行情/法人/融資券/本益比 → MOPS 歷史月營收 → 還原因子與技術指標。

## 開發（不用 Docker）
```bash
cp .env.example .env
cd backend
pip install -e ".[dev]"
python -m aqt.cli init-db --apply-migrations
python -m aqt.cli health-check
python -m pytest -q        # 需本機 PostgreSQL 資料庫 aqt_test
```

## 文件
[CLAUDE.md](CLAUDE.md) · [PROJECT_SPEC.md](PROJECT_SPEC.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [ROADMAP.md](ROADMAP.md) · [TASKS.md](TASKS.md) · [TEST_PLAN.md](TEST_PLAN.md) · [CHANGELOG.md](CHANGELOG.md) · [docs/DATABASE.md](docs/DATABASE.md) · [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md)
