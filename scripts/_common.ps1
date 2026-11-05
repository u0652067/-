# Shared helpers for the local deployment scripts (Windows PowerShell 5.1+). ASCII only.
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Test-Native {
    # run a native command quietly, return its exit code (PS 5.1 turns redirected stderr into errors under Stop)
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { & $args[0] @($args | Select-Object -Skip 1) *> $null; return $LASTEXITCODE }
    finally { $ErrorActionPreference = $prev }
}

function Assert-Docker {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "docker not found. Install Docker Desktop (needs WSL2: 'wsl --install' as administrator, reboot), start it once, then retry."
    }
    if ((Test-Native docker info) -ne 0) { throw "Docker Desktop is installed but not running. Start Docker Desktop and wait until it says 'Engine running'." }
}

function Invoke-Compose {
    # usage: Invoke-Compose up -d db   (throws on failure)
    & docker compose @args
    if ($LASTEXITCODE -ne 0) { throw "docker compose $($args -join ' ') failed (exit $LASTEXITCODE)" }
}

function Get-EnvValue([string]$Name, [string]$Default = '') {
    if (Test-Path .env) {
        foreach ($line in Get-Content .env) {
            if ($line -match "^\s*$Name\s*=\s*(.*)\s*$") { return $Matches[1] }
        }
    }
    return $Default
}

function Invoke-Cli {
    # one-off CLI command in a fresh app container (no ports, same volumes/env as the services)
    & docker compose run --rm --no-deps api python -m aqt.cli @args
    if ($LASTEXITCODE -ne 0) { throw "aqt.cli $($args -join ' ') failed (exit $LASTEXITCODE)" }
}

function Get-ImageRevision([string]$Image) {
    # JSON, because PowerShell 5.1 strips the inner quotes of a format string naming the label
    $labels = & docker image inspect $Image --format '{{json .Config.Labels}}'
    if ($LASTEXITCODE -ne 0) { return $null }
    return ($labels | ConvertFrom-Json).'org.opencontainers.image.revision'
}

function Assert-ReleaseImage {
    # AQT_IMAGE must be an immutable tag of a commit on main, and the image must say the same commit
    $image = Get-EnvValue 'AQT_IMAGE'
    if ($image -notmatch '^aqt-backend:main-([0-9a-f]{12})$') {
        throw "AQT_IMAGE='$image' is not an immutable release tag (aqt-backend:main-<12 hex>). Build one with scripts\build-release.ps1 and put it in .env."
    }
    $short = $Matches[1]
    $rev = Get-ImageRevision $image
    if ($null -eq $rev) { throw "Image $image does not exist on this machine or has no revision label. Build it with scripts\build-release.ps1." }
    if (-not $rev.StartsWith($short)) { throw "Image $image is labelled with commit $rev, not ${short}: refusing it." }
    return $image
}

function Get-SchemaState {
    # Asks the database and the image directly, so it also works with release images older than schema-status.
    $user = Get-EnvValue 'POSTGRES_USER' 'aqt'
    $db = (& docker compose exec -T db psql -U $user -d aqt -At -c 'SELECT version_num FROM alembic_version')
    if ($LASTEXITCODE -ne 0) { throw "Could not read alembic_version from the database" }
    $image = Get-EnvValue 'AQT_IMAGE'
    $heads = & docker run --rm $image python -m alembic heads
    if ($LASTEXITCODE -ne 0) { throw "Could not read the migration head of $image" }
    $head = (($heads | Select-Object -Last 1) -split ' ')[0]
    return [pscustomobject]@{ Database = "$db".Trim(); Image = $image; ImageHead = $head; Match = ("$db".Trim() -eq $head) }
}

function Wait-Db {
    for ($i = 0; $i -lt 60; $i++) {
        if ((Test-Native docker compose exec -T db pg_isready -U (Get-EnvValue 'POSTGRES_USER' 'aqt') -d aqt) -eq 0) { return }
        Start-Sleep -Seconds 2
    }
    throw "PostgreSQL did not become ready"
}

function New-DataDirs {
    foreach ($d in 'data\raw', 'data\logs', 'data\reports', 'backups') {
        New-Item -ItemType Directory -Force (Join-Path $Root $d) | Out-Null
    }
}
