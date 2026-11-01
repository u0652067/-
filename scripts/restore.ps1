# Restore a backup made by backup.ps1. Replaces the live database.
#   .\scripts\restore.ps1 -File backups\aqt_20261031_120000.dump -Force
# A safety backup of the current database is taken first unless -NoSafetyBackup.
param([Parameter(Mandatory = $true)][string]$File, [switch]$Force, [switch]$NoSafetyBackup)
. "$PSScriptRoot\_common.ps1"
Assert-Docker
$name = Split-Path -Leaf $File
if (-not (Test-Path (Join-Path $Root "backups\$name"))) { throw "backups\$name not found (dump must be inside .\backups)" }
if (-not $Force) { throw "This replaces the live database with $name. Re-run with -Force to confirm." }
$user = Get-EnvValue 'POSTGRES_USER' 'aqt'

$manifest = Join-Path $Root "backups\$name.json"
if (Test-Path $manifest) {
    $want = (Get-Content $manifest -Raw | ConvertFrom-Json).sha256
    $got = (Get-FileHash -Algorithm SHA256 (Join-Path $Root "backups\$name")).Hash.ToLower()
    if ($want -ne $got) { throw "sha256 mismatch for $name (manifest $want, file $got) - refusing to restore" }
    Write-Host "sha256 verified."
}

if (-not $NoSafetyBackup) { & "$PSScriptRoot\backup.ps1" -Label 'pre_restore' }

Write-Host "Stopping app containers..."
Invoke-Compose --profile backfill stop api scheduler backfill
Invoke-Compose exec -T db dropdb -U $user --if-exists --force aqt
Invoke-Compose exec -T db createdb -U $user aqt
Invoke-Compose exec -T db pg_restore -U $user -d aqt --no-owner "/backups/$name"
Write-Host "Restored $name. Starting app..."
Invoke-Compose up -d migrate api scheduler
Write-Host "Done."
