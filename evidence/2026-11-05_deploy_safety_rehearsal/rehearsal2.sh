#!/bin/bash
# Rehearsal 2: full copy of production in a separate compose project (own PostgreSQL, volume, ports, folders).
# Reads production only through pg_dump and a read-only table audit. Never restarts production containers.
# usage: rehearsal2.sh <repo> <sandbox dir> <main image> <rollback image> <pr image>
set -u
REPO=$1; SB=$2; MAIN=$3; OLD=$4; PRI=$5
PS="powershell -NoProfile -ExecutionPolicy Bypass -File"
OUT=$REPO/evidence/2026-11-05_deploy_safety_rehearsal
step() { echo; echo "=== [$(date '+%F %T')] $* ==="; }
expect_fail() { "$@"; c=$?; echo "-> exit code $c"; if [ $c -ne 0 ]; then echo "-> REFUSED as expected"; else echo "-> UNEXPECTED SUCCESS"; fi; }
prodstate() {
  docker inspect aqt-api-1 aqt-scheduler-1 aqt-db-1 --format '{{.Name}} image={{.Image}} started={{.State.StartedAt}} restarts={{.RestartCount}}'
  (cd "$REPO" && docker compose exec -T db psql -U aqt -d aqt -At -c 'select version_num from alembic_version')
}
sbsql() { docker compose exec -T db psql -U aqt -d aqt -At -c "$1"; }
ver() { echo "sandbox alembic_version: $(sbsql 'select version_num from alembic_version')"; }
setimg() { sed -i "s|^AQT_IMAGE=.*|AQT_IMAGE=$1|" .env; echo "sandbox AQT_IMAGE=$1"; }
audit() {
  AQT_IMAGE=$PRI docker compose run --rm --no-deps api python -m aqt.cli table-audit --out /data/reports/$1 ${2:+--columns-from /data/reports/$2} > /dev/null
  python -I "$REPO/evidence/2026-11-05_deploy_safety_rehearsal/audit_summary.py" "$SB/data/reports/$1"
}
compare() { AQT_IMAGE=$PRI docker compose run --rm --no-deps api python -m aqt.cli table-compare /data/reports/$1 /data/reports/$2; echo "-> compare exit $?"; }
health() {
  for i in $(seq 1 45); do curl -sf http://127.0.0.1:8001/api/health -o "$SB/h.json" && break; sleep 2; done
  python -I -m json.tool "$SB/h.json" | sed -n 1,40p
}

step "R0 production state before"; prodstate
cd "$REPO"
step "R1 production backup + verified restore, all tables (production stays running, read only)"
time $PS scripts/backup.ps1 -Verify -Label pre_rehearsal2 || exit 1
DUMP=$(ls -t backups/*pre_rehearsal2.dump | head -1); NAME=$(basename "$DUMP"); echo "$NAME"
python -I "$OUT/audit_summary.py" --manifest "$DUMP.json"
step "R2 read-only audit of every production table with the PR image"
mkdir -p data/reports/rehearsal2
time AQT_IMAGE=$PRI docker compose run --rm --no-deps api python -m aqt.cli table-audit --out /data/reports/rehearsal2/audit_prod.json > /dev/null

step "S0 build sandbox"
rm -rf "$SB"; mkdir -p "$SB/backups" "$SB/data/reports"
git archive HEAD docker-compose.yml scripts .env.example | tar -x -C "$SB" --force-local
cp "$DUMP" "$DUMP.json" "$SB/backups/"; cp data/reports/rehearsal2/audit_prod.json "$SB/data/reports/"
sed -i 's/^name: aqt$/name: aqtsb/; s/name: aqt_pgdata/name: aqtsb_pgdata/; s/127.0.0.1:8000:8000/127.0.0.1:8001:8000/' "$SB/docker-compose.yml"
sed -i 's/aqt_pgdata/aqtsb_pgdata/g; s/127.0.0.1:8000/127.0.0.1:8001/g' "$SB/scripts/start.ps1"
sed -i 's/\^aqt-backend:main-(\[0-9a-f\]{12})\$/^aqt-backend:(?:main|pr)-([0-9a-f]{12})$/' "$SB/scripts/_common.ps1"
PW=$(python -I -c 'import secrets;print(secrets.token_hex(16))')
sed "s/change-me/$PW/; s/^AQT_DB_PORT=.*/AQT_DB_PORT=5434/; s/^AQT_IMAGE=.*/AQT_IMAGE=$MAIN/" .env.example > "$SB/.env"
cd "$SB"; echo "sandbox differs from the PR only in: project name, volume name, ports, and accepting the pr- tag"
docker volume create aqtsb_pgdata; docker compose up -d db; sleep 15

step "A1 restore into sandbox PostgreSQL"; time docker compose exec -T db pg_restore -U aqt -d aqt --no-owner "/backups/$NAME"; ver
step "A2 every table: restored copy vs production"; time audit audit_T0.json; compare audit_prod.json audit_T0.json
docker compose exec -T db pg_dump -U aqt -d aqt -s > "$SB/schema_0003.sql"
step "A3 upgrade 0003 -> 0005 with the immutable main image $MAIN (main's CLI has no flag yet, so plain init-db)"
time AQT_IMAGE=$MAIN docker compose run --rm --no-deps api python -m aqt.cli init-db; ver
step "A4 every table after upgrade, on the columns that existed before"; time audit audit_T1.json audit_T0.json; compare audit_T0.json audit_T1.json
step "A5 schema checks"
echo "NULLs known_at/effective_at/detection_method/rows: $(sbsql "select count(*) filter (where known_at is null),count(*) filter (where effective_at is null),count(*) filter (where detection_method is null),count(*) from data_quality_issues")"
echo "triggers: $(sbsql "select count(*) from pg_trigger where not tgisinternal")"
sbsql "select tgrelid::regclass, tgname from pg_trigger where not tgisinternal and tgrelid::regclass::text in ('backtest_runs','backtest_trades','backtest_run_invalidations') order by 1,2"
LAST=$(sbsql "select max(trade_date) from daily_prices"); echo "latest trade date $LAST"
step "A6 integrity-check on main image"
AQT_IMAGE=$MAIN docker compose run --rm --no-deps api python -m aqt.cli integrity-check --end "$LAST" > "$SB/integrity.json"; echo "exit $?"
python -I "$OUT/audit_summary.py" --integrity "$SB/integrity.json"
step "A7 start API and scheduler from main image on 0005; force a real catch-up by removing the last 3 daily_update records in the sandbox"
sbsql "delete from job_runs where id in (select id from job_runs where job_name='daily_update' and status='SUCCESS' order by started_at desc limit 3) returning job_name, started_at"
docker compose up -d --no-deps api scheduler; health
sleep 420
docker compose logs --no-color --tail 25 scheduler
sbsql "select job_name,status,started_at,finished_at from job_runs order by started_at desc limit 6"
docker compose ps --format '{{.Service}} {{.Status}}'
step "A8 update-daily on main image in the sandbox"
AQT_IMAGE=$MAIN docker compose run --rm --no-deps api python -m aqt.cli update-daily --date "$LAST" | tail -5; echo "exit ${PIPESTATUS[0]}"
echo "safe_mode: $(sbsql "select value from system_state where key='safe_mode'")"
docker compose stop api scheduler

step "B0 guards with the PR image $PRI: services defined by compose"
setimg "$PRI"; docker compose config --services
step "B1 restore.ps1 with a 0005 image must restore but refuse to start services"
expect_fail $PS scripts/restore.ps1 -File "backups/$NAME" -Force -NoSafetyBackup; ver
docker compose ps --format '{{.Service}} {{.Status}}'
step "B2 start.ps1 must refuse on mismatch"
expect_fail $PS scripts/start.ps1; ver; docker compose ps --format '{{.Service}} {{.Status}}'
step "B3 plain docker compose up: services must refuse, schema must not change"
docker compose up -d api scheduler; sleep 25; docker compose logs --no-color --tail 4 api scheduler; ver; docker compose stop api scheduler
step "B4 migrate.ps1 without approval / without backup / with both"
expect_fail $PS scripts/migrate.ps1 -Backup "backups/$NAME"; ver
expect_fail $PS scripts/migrate.ps1 -IHaveApproval; ver
time $PS scripts/migrate.ps1 -Backup "backups/$NAME" -IHaveApproval; echo "exit $?"; ver
step "B5 downgrade allowed while no audit record exists"
AQT_IMAGE=$PRI docker compose run --rm --no-deps api python -m aqt.cli rollback-check | head -14; echo "exit ${PIPESTATUS[0]}"
time $PS scripts/migrate.ps1 -DowngradeTo 0003 -IHaveApproval; echo "exit $?"; ver
docker compose exec -T db pg_dump -U aqt -d aqt -s > "$SB/schema_after_downgrade.sql"
diff <(grep -v restrict "$SB/schema_0003.sql") <(grep -v restrict "$SB/schema_after_downgrade.sql") && echo "schema after downgrade IDENTICAL to 0003"
audit audit_T2.json audit_T0.json; compare audit_T0.json audit_T2.json
step "B6 upgrade again, start with guards on"
$PS scripts/migrate.ps1 -Backup "backups/$NAME" -IHaveApproval; ver
$PS scripts/start.ps1 | tail -30; echo "exit ${PIPESTATUS[0]}"
sleep 30; docker compose ps --format '{{.Service}} {{.Status}}'
step "B7 write a formal backtest record: downgrade must now be refused"
AQT_IMAGE=$PRI docker compose run --rm --no-deps api python -m aqt.cli backtest --strategy ew_liquid_baseline --start 2024-01-01 --end 2024-03-31 | tail -4; echo "exit ${PIPESTATUS[0]}"
AQT_IMAGE=$PRI docker compose run --rm --no-deps api python -m aqt.cli rollback-check | head -14; echo "exit ${PIPESTATUS[0]}"
expect_fail $PS scripts/migrate.ps1 -DowngradeTo 0003 -IHaveApproval; ver
echo "backtest_runs: $(sbsql 'select count(*) from backtest_runs')"
step "B8 standard rollback: restore the pre-migration backup with the 0003 release image $OLD"
setimg "$OLD"
time $PS scripts/restore.ps1 -File "backups/$NAME" -Force -NoSafetyBackup -NoStart; echo "exit $?"; ver
audit audit_T3.json audit_T0.json; compare audit_T0.json audit_T3.json
$PS scripts/start.ps1 | tail -24; echo "exit ${PIPESTATUS[0]}"
sleep 20; docker compose ps --format '{{.Service}} {{.Status}}'
docker compose logs --no-color --tail 6 scheduler
cp "$SB"/data/reports/audit_*.json "$OUT/"
step "Z cleanup sandbox"
docker compose down; docker volume rm aqtsb_pgdata; cd "$REPO"; rm -rf "$SB"
step "R9 production state after (must equal R0)"; prodstate
