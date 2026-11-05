#!/bin/bash
# Proves the reconciliation on a full copy of production in a separate compose project. Production is not touched.
# usage: sandbox_reconcile.sh <repo> <sandbox dir> <pr image> <dump name> <research records dump>
set -u
REPO=$1; SB=$2; PRI=$3; NAME=$4; RECS=$5
step() { echo; echo "=== [$(date '+%F %T')] $* ==="; }
sbsql() { docker compose exec -T db psql -U aqt -d aqt -At -F ' | ' -c "$1"; }
cli() { AQT_IMAGE=$PRI docker compose run --rm --no-deps api python -m aqt.cli "$@"; }
verdict() { python -I -c "import json,sys;d=json.load(open(sys.argv[1],encoding='utf-8'));print(json.dumps({k:d['verdict'].get(k) for k in ('technical_backtest_ready','fundamental_backtest_ready','technical_ready_through')}|{'gaps':d['verdict']['coverage']['gaps'],'rerunnable':d.get('rerunnable_days'),'reconciled':d.get('reconciled_days')},ensure_ascii=False))" "$1"; }
step "S0 sandbox from commit $(cd "$REPO" && git rev-parse HEAD)"
cd "$REPO"
rm -rf "$SB"; mkdir -p "$SB/backups" "$SB/data/reports"
git archive HEAD docker-compose.yml scripts .env.example | tar -x -C "$SB"
cp "backups/$NAME" "backups/$RECS" "$SB/backups/"
sed -i 's/^name: aqt$/name: aqtsb/; s/name: aqt_pgdata/name: aqtsb_pgdata/; s/127.0.0.1:8000:8000/127.0.0.1:8001:8000/' "$SB/docker-compose.yml"
PW=$(python -I -c 'import secrets;print(secrets.token_hex(16))')
sed "s/change-me/$PW/; s/^AQT_DB_PORT=.*/AQT_DB_PORT=5434/; s/^AQT_IMAGE=.*/AQT_IMAGE=$PRI/" .env.example > "$SB/.env"
cd "$SB"; docker volume create aqtsb_pgdata; docker compose up -d db; sleep 15
step "R1 restore production copy"; docker compose exec -T db pg_restore -U aqt -d aqt --no-owner "/backups/$NAME"; sbsql "select version_num from alembic_version"
step "R3 migrate the copy to 0005a"
cli init-db --apply-migrations 2>&1 | grep -v plugins; sbsql "select version_num from alembic_version"
echo "job 2065 before: $(sbsql "select id,status,started_at,finished_at,result::text,error from job_runs where id=2065")"
step "R4 integrity to 2026-11-04 before reconciliation"
cli integrity-check --end 2026-11-04 --out /data/reports/i1 > /dev/null 2>&1; echo "exit $?"
verdict "$(ls "$SB"/data/reports/i1/*.json | head -1)"
step "R5 backtest ending before and after the problem day"
cli backtest --strategy cash_baseline --start 2026-10-01 --end 2026-10-30 2>&1 | grep -E '"run_id"|Error|DataNotReady' | tail -2; echo "exit ${PIPESTATUS[0]}"
cli backtest --strategy cash_baseline --start 2026-10-01 --end 2026-11-03 2>&1 | grep -E '"run_id"|DataNotReady' | tail -1 | cut -c1-220; echo "exit ${PIPESTATUS[0]}"
step "R6 verify and record"
cli reconcile-job --job-id 2065 --out /data/reports/evidence_sandbox.json 2>&1 | grep -v "plugins\|Container"
cli reconcile-job --job-id 2065 --record --reason "Scheduler container was replaced by a start.ps1 deployment at 2026-11-02 22:15:34 Asia/Taipei while the job ran. All 8 datasets match their payloads." --by "Claude, on request of pudding" 2>&1 | grep -v "plugins\|Container"
sbsql "select id,job_run_id,outcome,executed_by,reconciled_at,evidence_sha256,left(reason,80) from job_reconciliations"
echo "job 2065 after: $(sbsql "select id,status,started_at,finished_at,result::text,error from job_runs where id=2065")"
step "R7 integrity to 2026-11-04 after reconciliation"
cli integrity-check --end 2026-11-04 --out /data/reports/i2 > /dev/null 2>&1; echo "exit $?"
verdict "$(ls "$SB"/data/reports/i2/*.json | head -1)"
step "R8 append-only"
docker compose exec -T db psql -U aqt -d aqt -c "update job_reconciliations set outcome='DATA_INCOMPLETE'" 2>&1 | tail -2
docker compose exec -T db psql -U aqt -d aqt -c "delete from job_reconciliations" 2>&1 | tail -2
step "R9 rollback-check counts it"
cli rollback-check 2>&1 | grep -E 'job_reconciliations"|downgrade_allowed'
step "Z cleanup"
docker compose down; docker volume rm aqtsb_pgdata; cd "$REPO"; rm -rf "$SB"
step "production after"; docker inspect aqt-api-1 aqt-scheduler-1 --format '{{.Name}} {{.Image}} {{.State.StartedAt}} {{.RestartCount}}'
docker compose exec -T db psql -U aqt -d aqt -At -c 'select version_num from alembic_version'
