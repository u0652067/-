# Historical backfill control.
#   smoke  -Start 2024-01-02 -End 2024-01-12   small acceptance batch (separate progress record)
#   start                                       verified backup + integrity baseline, then start the full
#                                               2019-> backfill in the background (resumable)
#   status                                      progress, ETA, missing datasets, container state
#   stop                                        stop gracefully (progress is kept; `start` resumes)
#   report                                      integrity report (row counts, gaps, failed sources)
#   logs                                        follow the backfill log
param([Parameter(Mandatory = $true)][ValidateSet('smoke', 'start', 'status', 'stop', 'report', 'logs')][string]$Action,
      [string]$Start = '2024-01-02', [string]$End = '2024-01-12', [switch]$SkipBackup)
. "$PSScriptRoot\_common.ps1"
Assert-Docker
New-DataDirs

switch ($Action) {
    'smoke' {
        Invoke-Cli sync-securities
        Invoke-Cli backfill-full --start $Start --end $End --state-key backfill_smoke
        Invoke-Cli integrity-check --start $Start --end $End
    }
    'start' {
        if (-not $SkipBackup) {
            & "$PSScriptRoot\backup.ps1" -Verify -Label 'pre_backfill'
            Invoke-Cli integrity-check
        }
        Invoke-Cli sync-securities
        Invoke-Compose --profile backfill up -d backfill
        Write-Host "Backfill running in background. Check with: .\scripts\backfill.ps1 status"
    }
    'status' {
        Invoke-Compose --profile backfill ps backfill
        Invoke-Cli backfill-status
    }
    'stop' {
        Invoke-Compose --profile backfill stop backfill
        Invoke-Cli backfill-status
    }
    'report' { Invoke-Cli integrity-check }
    'logs' { Get-Content -Wait -Tail 50 (Join-Path $Root 'data\logs\backfill-full.log') }
}
