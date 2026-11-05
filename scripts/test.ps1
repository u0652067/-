# Run the automated test suite against a throwaway PostgreSQL (tmpfs). Never touches the real database.
. "$PSScriptRoot\_common.ps1"
Assert-Docker
$env:AQT_IMAGE = 'aqt-backend:test'  # only satisfies compose; api and scheduler are not started
try {
    & docker compose --profile test run --rm --build test python -m pytest -q @args
    $code = $LASTEXITCODE
} finally {
    Test-Native docker compose --profile test rm -sf testdb | Out-Null
}
exit $code
