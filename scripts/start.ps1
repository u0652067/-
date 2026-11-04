# Start the local deployment: db + api + scheduler, from the immutable image in AQT_IMAGE.
# Never builds and never migrates: a schema mismatch stops here with an explanation (see migrate.ps1).
. "$PSScriptRoot\_common.ps1"
Assert-Docker
New-DataDirs

if (-not (Test-Path .env)) {
    $pw = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 32 | ForEach-Object { [char]$_ })
    (Get-Content .env.example) -replace 'change-me', $pw | Set-Content -Encoding ascii .env
    Write-Host "Created .env with a random database password (keep this file; it is git-ignored)."
}

if ((Test-Native docker volume inspect aqt_pgdata) -ne 0) {
    docker volume create aqt_pgdata | Out-Null
    Write-Host "Created persistent volume aqt_pgdata."
}

$image = Assert-ReleaseImage
Write-Host "Release image: $image"
Invoke-Compose up -d db
Wait-Db
$st = Get-SchemaState
Write-Host "Database schema: $($st.Database); image expects: $($st.ImageHead)"
if (-not $st.Match) {
    throw "Refused: database schema $($st.Database) does not match $image (expects $($st.ImageHead)). No service was started or changed and nothing was migrated. Set AQT_IMAGE to the release of this schema, or run the approved migration with scripts\migrate.ps1."
}
Invoke-Compose up -d --no-deps api scheduler

Write-Host "Waiting for API..."
for ($i = 0; $i -lt 60; $i++) {
    try {
        $h = Invoke-RestMethod -Uri http://127.0.0.1:8000/api/health -TimeoutSec 3
        Write-Host "API up: http://127.0.0.1:8000/docs"
        $h | ConvertTo-Json -Depth 5
        exit 0
    } catch { Start-Sleep -Seconds 2 }
}
Invoke-Compose ps
throw "API did not become healthy in 120s. Check: docker compose logs api"
