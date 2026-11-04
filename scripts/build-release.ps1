# Build the immutable release image of a commit on origin/main: aqt-backend:main-<12 hex of the commit>.
# Built from `git archive` of that commit, so the checked-out branch and local edits cannot get in.
# A tag is never rebuilt with other content. Prints the tag and image id; put the tag in .env as AQT_IMAGE.
param([string]$Commit = 'origin/main')
. "$PSScriptRoot\_common.ps1"
Assert-Docker
Test-Native git fetch -q origin | Out-Null
$full = (& git rev-parse "$Commit^{commit}").Trim()
if ($LASTEXITCODE -ne 0) { throw "Unknown commit $Commit" }
if ((Test-Native git merge-base --is-ancestor $full origin/main) -ne 0) { throw "$full is not on origin/main: only main commits become release images." }
$short = $full.Substring(0, 12)
$tag = "aqt-backend:main-$short"
if ((Test-Native docker image inspect $tag) -eq 0) {
    $rev = & docker image inspect $tag --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}'
    if ($rev -ne $full) { throw "$tag exists with revision $rev" }
    Write-Host "$tag already exists (same commit); not rebuilding."
} else {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("aqt-release-" + $short)
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp -Confirm:$false }
    New-Item -ItemType Directory -Force $tmp | Out-Null
    $tar = Join-Path $tmp 'src.tar'
    & git archive --format=tar -o $tar $full backend
    if ($LASTEXITCODE -ne 0) { throw "git archive failed" }
    & tar -xf $tar -C $tmp
    if ($LASTEXITCODE -ne 0) { throw "tar failed" }
    # the label is set here as well, so commits older than the Dockerfile ARG (rollback images) carry it too
    & docker build -t $tag --build-arg "AQT_BUILD_COMMIT=$full" --label "org.opencontainers.image.revision=$full" (Join-Path $tmp 'backend')
    $code = $LASTEXITCODE
    Remove-Item -Recurse -Force $tmp -Confirm:$false
    if ($code -ne 0) { throw "docker build failed" }
}
$id = & docker image inspect $tag --format '{{.Id}}'
New-DataDirs
$manifest = [ordered]@{ tag = $tag; commit = $full; image_id = $id; built_at = (Get-Date).ToString('o') }
$dir = Join-Path $Root 'data\reports\releases'
New-Item -ItemType Directory -Force $dir | Out-Null
$manifest | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $dir "main-$short.json")
Write-Host "Release image: $tag"
Write-Host "Commit:        $full"
Write-Host "Image id:      $id"
Write-Host "Set AQT_IMAGE=$tag in .env to use it."
