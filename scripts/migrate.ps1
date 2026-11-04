# The only way to change the database schema. Never runs on its own; needs an approved, explicit call.
#   Upgrade to the head of AQT_IMAGE, after a verified backup exists:
#     .\scripts\migrate.ps1 -Backup backups\aqt_<time>_pre_migration.dump -IHaveApproval
#   Status only:
#     .\scripts\migrate.ps1 -Status
#   Downgrade (refused as soon as any backtest / audit record exists; then the backup is the only rollback):
#     .\scripts\migrate.ps1 -DowngradeTo 0003 -IHaveApproval
param([string]$Backup = '', [string]$DowngradeTo = '', [switch]$Status, [switch]$IHaveApproval)
. "$PSScriptRoot\_common.ps1"
Assert-Docker
$image = Assert-ReleaseImage
Write-Host "Release image: $image"
Invoke-Compose up -d db
Wait-Db
$ErrorActionPreference = 'Continue'
$st = Get-SchemaState
Write-Host "Database schema: $($st.Database); image expects: $($st.ImageHead)"
if ($Status) { exit 0 }
if (-not $IHaveApproval) { throw "Refused: schema changes need -IHaveApproval, after the migration plan was approved." }
if ($DowngradeTo) {
    & docker compose run --rm --no-deps api python -m aqt.cli rollback-check
    if ($LASTEXITCODE -ne 0) { throw "Downgrade refused: audit records exist. The standard rollback is restoring the pre-migration backup (scripts\restore.ps1)." }
    Invoke-Compose stop api scheduler
    & docker compose run --rm --no-deps api python -m alembic downgrade $DowngradeTo
    if ($LASTEXITCODE -ne 0) { throw "Downgrade failed" }
    Write-Host "Downgraded to $DowngradeTo. Set AQT_IMAGE to the release image of that schema before starting."
    exit 0
}
if (-not $Backup) { throw "Refused: -Backup <verified pre-migration dump> is required." }
$name = Split-Path -Leaf $Backup
$manifestPath = Join-Path $Root "backups\$name.json"
if (-not (Test-Path $manifestPath)) { throw "No manifest for $name" }
if ($st.Match) { Write-Host "Nothing to migrate."; exit 0 }
$m = Get-Content $manifestPath -Raw | ConvertFrom-Json
if (-not $m.verified_restore.ok) { throw "$name has no verified restore: run scripts\backup.ps1 -Verify." }
$sha = (Get-FileHash -Algorithm SHA256 (Join-Path $Root "backups\$name")).Hash.ToLower()
if ($sha -ne $m.sha256) { throw "sha256 mismatch for $name" }
Invoke-Compose stop api scheduler
& docker compose --profile migrate run --rm migrate
if ($LASTEXITCODE -ne 0) { throw "Migration failed: services stay stopped. See MIGRATION_PLAN." }
if (-not (Get-SchemaState).Match) { throw "Schema does not match after migrating: services stay stopped." }
Write-Host "Migrated. Run the post-migration checks, then scripts\start.ps1."
