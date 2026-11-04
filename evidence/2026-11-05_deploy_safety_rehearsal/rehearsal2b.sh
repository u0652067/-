#!/bin/bash
# Rerun of the scripted part of rehearsal 2 after the label bug it found was fixed, from the same production dump.
# Production is only read for its state; the dump and production audit come from rehearsal2_log.txt.
# usage: rehearsal2b.sh <repo> <sandbox dir> <main image> <rollback image> <pr image> <dump name>
set -u
REPO=$1; SB=$2; MAIN=$3; OLD=$4; PRI=$5; NAME=$6
PS="powershell -NoProfile -ExecutionPolicy Bypass -File"
OUT=$REPO/evidence/2026-11-05_deploy_safety_rehearsal
step() { echo; echo "=== [$(date '+%F %T')] $* ==="; }
expect_fail() { "$@"; c=$?; echo "-> exit code $c"; if [ $c -ne 0 ]; then echo "-> REFUSED"; else echo "-> UNEXPECTED SUCCESS"; fi; }
prodstate() {
  docker inspect aqt-api-1 aqt-scheduler-1 aqt-db-1 --format '{{.Name}} image={{.Image}} started={{.State.StartedAt}} restarts={{.RestartCount}}'
  (cd "$REPO" && docker compose exec -T db psql -U aqt -d aqt -At -c 'select version_num from alembic_version')
}
sbsql() { docker compose exec -T db psql -U aqt -d aqt -At -c "$1"; }
ver() { echo "sandbox alembic_version: $(sbsql 'select version_num from alembic_version')"; }
setimg() { sed -i "s|^AQT_IMAGE=.*|AQT_IMAGE=$1|" .env; echo "sandbox AQT_IMAGE=$1"; }
audit() {
  AQT_IMAGE=$PRI docker compose run --rm --no-deps api python -m aqt.cli table-audit --out /data/reports/$1 ${2:+--columns-from /data/reports/$2} > /dev/null
  python -I "$OUT/audit_summary.py" "$SB/data/reports/$1"
}
compare() { AQT_IMAGE=$PRI docker compose run --rm --no-deps api python -m aqt.cli table-compare /data/reports/$1 /data/reports/$2; echo "-> compare exit $?"; }
health() {
  for i in $(seq 1 45); do curl -sf http://127.0.0.1:8001/api/health -o "$SB/h.json" && break; sleep 2; done
  python -I -m json.tool "$SB/h.json" | sed -n 1,24p
}

step "R0 production state before"; prodstate
cd "$REPO"
step "S0 build sandbox from commit $(git rev-parse HEAD)"
rm -rf "$SB"; mkdir -p "$SB/backups" "$SB/data/reports"
git archive HEAD docker-compose.yml scripts .env.example | tar -x -C "$SB"
cp "backups/$NAME" "backups/$NAME.json" "$SB/backups/"; cp data/reports/rehearsal2/audit_prod.json "$SB/data/reports/"
sed -i 's/^name: aqt$/name: aqtsb/; s/name: aqt_pgdata/name: aqtsb_pgdata/; s/127.0.0.1:8000:8000/127.0.0.1:8001:8000/' "$SB/docker-compose.yml"
sed -i 's/aqt_pgdata/aqtsb_pgdata/g; s/127.0.0.1:8000/127.0.0.1:8001/g' "$SB/scripts/start.ps1"
sed -i 's/\^aqt-backend:main-(\[0-9a-f\]{12})\$/^aqt-backend:(?:main|pr)-([0-9a-f]{12})$/' "$SB/scripts/_common.ps1"
PW=$(python -I -c 'import secrets;print(secrets.token_hex(16))')
sed "s/change-me/$PW/; s/^AQT_DB_PORT=.*/AQT_DB_PORT=5434/; s/^AQT_IMAGE=.*/AQT_IMAGE=$MAIN/" .env.example > "$SB/.env"
cd "$SB"; echo "sandbox differs from the PR only in: project name, volume name, ports, and accepting the pr- tag"
docker volume create aqtsb_pgdata; docker compose up -d db; sleep 15

step "A1 restore"; time docker compose exec -T db pg_restore -U aqt -d aqt --no-owner "/backups/$NAME"; ver
step "A2 every table: restored copy vs production audit"; audit audit_T0.json; compare audit_prod.json audit_T0.json
docker compose exec -T db pg_dump -U aqt -d aqt -s > "$SB/schema_0003.sql"
step "A3 upgrade with main image and read the integrity verdict"
AQT_IMAGE=$MAIN docker compose run --rm --no-deps api python -m aqt.cli init-db; ver
LAST=$(sbsql "select max(trade_date) from daily_prices"); echo "latest trade date $LAST"
AQT_IMAGE=$MAIN docker compose run --rm --no-deps api python -m aqt.cli integrity-check --end "$LAST" > "$SB/integrity.json"; echo "exit $?"
python -I "$OUT/audit_summary.py" --integrity "$SB/integrity.json"
PREV=$(sbsql "select max(trade_date) from daily_prices where trade_date < '$LAST'")
AQT_IMAGE=$MAIN docker compose run --rm --no-deps api python -m aqt.cli integrity-check --end "$PREV" > "$SB/integrity2.json"; echo "exit $?"
python -I "$OUT/audit_summary.py" --integrity "$SB/integrity2.json"

step "B1 restore.ps1 with the guarded PR image $PRI: restores 0003, refuses to start services"
setimg "$PRI"; docker compose config --services
expect_fail $PS scripts/restore.ps1 -File "backups/$NAME" -Force -NoSafetyBackup; ver
docker compose ps --format '{{.Service}} {{.Status}}'
step "B2 start.ps1 must refuse on mismatch"
expect_fail $PS scripts/start.ps1; ver; docker compose ps --format '{{.Service}} {{.Status}}'
step "B3 plain docker compose up: services must refuse, schema must not change"
docker compose up -d api scheduler; sleep 25; docker compose logs --no-color --tail 2 api scheduler; ver; docker compose ps -a --format '{{.Service}} {{.Status}}'; docker compose stop api scheduler
step "B3b verified pre-migration backup with services stopped"
time $PS scripts/backup.ps1 -Verify -Label pre_migration; echo "exit $?"
BK=$(ls -t backups/*pre_migration.dump | head -1); python -I "$OUT/audit_summary.py" --manifest "$BK.json"
step "B4 migrate.ps1: no approval / no backup / backup without a clean verification (production dump taken while services ran) / valid"
expect_fail $PS scripts/migrate.ps1 -Backup "$BK"; ver
expect_fail $PS scripts/migrate.ps1 -IHaveApproval; ver
expect_fail $PS scripts/migrate.ps1 -Backup "backups/$NAME" -IHaveApproval; ver
time $PS scripts/migrate.ps1 -Backup "$BK" -IHaveApproval; echo "exit $?"; ver
step "B5 downgrade allowed while no audit record exists"
AQT_IMAGE=$PRI docker compose run --rm --no-deps api python -m aqt.cli rollback-check; echo "exit $?"
audit audit_T1b.json audit_T0.json; compare audit_T0.json audit_T1b.json
time $PS scripts/migrate.ps1 -DowngradeTo 0003 -IHaveApproval; echo "exit $?"; ver
docker compose exec -T db pg_dump -U aqt -d aqt -s > "$SB/schema_after_downgrade.sql"
diff <(grep -v restrict "$SB/schema_0003.sql") <(grep -v restrict "$SB/schema_after_downgrade.sql") && echo "schema after downgrade IDENTICAL to 0003"
audit audit_T2.json audit_T0.json; compare audit_T0.json audit_T2.json
step "B6 upgrade again, start with guards on"
$PS scripts/migrate.ps1 -Backup "$BK" -IHaveApproval; ver
$PS scripts/start.ps1 | tail -8; echo "exit ${PIPESTATUS[0]}"
health
sleep 120; docker compose ps --format '{{.Service}} {{.Status}}'
docker compose logs --no-color --tail 3 scheduler
sbsql "select job_name,status,started_at,finished_at from job_runs order by started_at desc limit 2"
step "B7 write a formal backtest record: downgrade must now be refused"
AQT_IMAGE=$PRI docker compose run --rm --no-deps api python -m aqt.cli backtest --strategy ew_liquid_baseline --start 2024-01-01 --end 2024-03-31 | tail -4; echo "exit ${PIPESTATUS[0]}"
AQT_IMAGE=$PRI docker compose run --rm --no-deps api python -m aqt.cli rollback-check | head -14; echo "exit ${PIPESTATUS[0]}"
expect_fail $PS scripts/migrate.ps1 -DowngradeTo 0003 -IHaveApproval; ver
echo "backtest_runs: $(sbsql 'select count(*) from backtest_runs')"
step "B8 standard rollback: restore the pre-migration backup with the 0003 release image $OLD"
setimg "$OLD"
time $PS scripts/restore.ps1 -File "$BK" -Force -NoSafetyBackup -NoStart; echo "exit $?"; ver
audit audit_T3.json audit_T0.json; compare audit_T0.json audit_T3.json
echo "backtest_runs: $(sbsql 'select count(*) from backtest_runs')"
$PS scripts/start.ps1 | tail -6; echo "exit ${PIPESTATUS[0]}"
health
sleep 30; docker compose ps --format '{{.Service}} {{.Status}}'
docker compose logs --no-color --tail 3 scheduler
cp "$SB"/data/reports/audit_*.json "$OUT/"; cp "$REPO/data/reports/rehearsal2/audit_prod.json" "$OUT/"
step "Z cleanup sandbox"
docker compose down; docker volume rm aqtsb_pgdata; cd "$REPO"; rm -rf "$SB"
step "R9 production state after (must equal R0)"; prodstate
