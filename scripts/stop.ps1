# Stop everything. Data is kept: the aqt_pgdata volume is external and ./data, ./backups are host folders.
#   -Remove   also remove the containers (docker compose down, WITHOUT -v)
param([switch]$Remove)
. "$PSScriptRoot\_common.ps1"
Assert-Docker
if ($Remove) {
    Invoke-Compose --profile backfill --profile test down --remove-orphans
} else {
    Invoke-Compose --profile backfill --profile test stop
}
Write-Host "Stopped. Data volume aqt_pgdata and ./data, ./backups are untouched."
