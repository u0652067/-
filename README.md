# AI Quant Trader — 台股自主量化研究與模擬交易系統

> ⚠️ 僅限模擬交易。本系統不連接任何券商、不使用真實資金。模擬績效不代表真實投資成果。

目前進度：PHASE 0 完成、PHASE 1（台股資料中心）程式完成並通過 75 項自動化測試，待在可連線交易所網站的主機上做真實資料驗收。詳見 [TASKS.md](TASKS.md)。

## 快速開始（本機）
```bash
cp .env.example .env            # 設定資料庫密碼
cd backend
pip install -e ".[dev]"
python -m aqt.cli init-db
python -m aqt.cli check-sources # 確認能連到 TWSE/TPEx 且解析器正確
python -m aqt.cli sync-securities
python -m aqt.cli backfill --start 2019-01-01 --end 2026-10-30
python -m aqt.cli api           # http://localhost:8000/docs
python -m aqt.cli scheduler     # 另開一個程序常駐
```

## Docker
```bash
cp .env.example .env
docker compose up -d --build
```

## 文件
[CLAUDE.md](CLAUDE.md) · [PROJECT_SPEC.md](PROJECT_SPEC.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [ROADMAP.md](ROADMAP.md) · [TASKS.md](TASKS.md) · [TEST_PLAN.md](TEST_PLAN.md) · [CHANGELOG.md](CHANGELOG.md) · [docs/DATABASE.md](docs/DATABASE.md) · [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md)
