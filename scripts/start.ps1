# Start (or update) the local deployment: db + migrate + api + scheduler.
# Safe to run repeatedly; rebuilding never touches the data volume or ./data, ./backups.
param([switch]$NoBuild)
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

$build = @(); if (-not $NoBuild) { $build = @('--build') }
Invoke-Compose up -d @build db migrate api scheduler

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
throw "API did not become healthy in 120s. Check: docker compose logs api migrate"
