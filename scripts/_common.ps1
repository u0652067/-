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

function New-DataDirs {
    foreach ($d in 'data\raw', 'data\logs', 'data\reports', 'backups') {
        New-Item -ItemType Directory -Force (Join-Path $Root $d) | Out-Null
    }
}
