# Consistent PostgreSQL backup (pg_dump custom format) into .\backups, plus sha256 and row-count manifest.
#   -Verify   restore the dump into a scratch database and compare row counts with the live database
param([switch]$Verify, [string]$Label = '')
. "$PSScriptRoot\_common.ps1"
Assert-Docker
New-DataDirs
$user = Get-EnvValue 'POSTGRES_USER' 'aqt'
$ts = Get-Date -Format 'yyyyMMdd_HHmmss'
$name = "aqt_$ts$(if ($Label) { '_' + $Label }).dump"

function Get-Counts([string]$db) {
    $tables = 'securities','daily_prices','raw_payloads','institutional_trades','margin_trades','valuations',
              'monthly_revenues','corporate_actions','technical_indicators','market_indices','job_runs','trading_calendar'
    $sql = ($tables | ForEach-Object { "SELECT '$_' t, count(*) n FROM $_" }) -join ' UNION ALL '
    $out = docker compose exec -T db psql -U $user -d $db -At -F '=' -c $sql
    if ($LASTEXITCODE -ne 0) { throw "row count on $db failed" }
    $h = [ordered]@{}; foreach ($l in $out) { $k, $v = $l -split '='; $h[$k] = [int64]$v }; return $h
}

Write-Host "Dumping database to backups\$name ..."
Invoke-Compose exec -T db pg_dump -U $user -d aqt -Fc -Z 6 -f "/backups/$name"
$live = Get-Counts 'aqt'
Invoke-Compose exec -T db pg_restore --list "/backups/$name" | Out-Null

$file = Join-Path $Root "backups\$name"
$sha = (Get-FileHash -Algorithm SHA256 $file).Hash.ToLower()
$manifest = [ordered]@{ file = $name; created_at = (Get-Date).ToString('o'); bytes = (Get-Item $file).Length
                        sha256 = $sha; row_counts = $live; verified_restore = $null }

if ($Verify) {
    Write-Host "Verifying: restoring into scratch database aqt_verify ..."
    Invoke-Compose exec -T db dropdb -U $user --if-exists aqt_verify
    Invoke-Compose exec -T db createdb -U $user aqt_verify
    Invoke-Compose exec -T db pg_restore -U $user -d aqt_verify --no-owner "/backups/$name"
    $restored = Get-Counts 'aqt_verify'
    Invoke-Compose exec -T db dropdb -U $user aqt_verify
    $diff = @($live.Keys | Where-Object { $live[$_] -ne $restored[$_] })
    $manifest.verified_restore = [ordered]@{ ok = ($diff.Count -eq 0); restored_counts = $restored; differs = $diff }
    if ($diff.Count) { Write-Warning "Row counts differ after restore (live db may have changed meanwhile): $($diff -join ', ')" }
    else { Write-Host "Restore verified: row counts identical." }
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8 "$file.json"
Write-Host "Backup OK: $file ($([math]::Round($manifest.bytes/1MB,1)) MB, sha256 $sha)"
