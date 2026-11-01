# Source health check from this machine's network: TWSE, TPEx, OpenAPI, ISIN, MOPS (+ MIS).
# Report saved to .\data\reports\health\health_<UTC time>.json
. "$PSScriptRoot\_common.ps1"
Assert-Docker
New-DataDirs
Invoke-Cli health-check
