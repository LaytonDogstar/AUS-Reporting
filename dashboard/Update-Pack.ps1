<#
.SYNOPSIS
    Update the local copy of the scripts to the latest version.

.DESCRIPTION
    Replaces the six commands that updating used to take. Downloads the
    current branch, extracts it over C:\discovery, clears the
    downloaded-from-the-internet flag, and leaves you in the dashboard
    folder ready to run something.

    Safe to run from anywhere, including from inside the folder it is
    about to replace.

.EXAMPLE
    .\Update-Pack.ps1

.EXAMPLE
    # From anywhere, without having the file first:
    iwr -useb https://raw.githubusercontent.com/LaytonDogstar/AUS-Reporting/claude/code-review-tsyca6/dashboard/Update-Pack.ps1 | iex
#>
[CmdletBinding()]
param(
    [string] $Destination = "C:\discovery",
    [string] $Branch = "claude/code-review-tsyca6"
)

$ErrorActionPreference = 'Stop'

$repo     = "LaytonDogstar/AUS-Reporting"
$zipUrl   = "https://github.com/$repo/archive/refs/heads/$Branch.zip"
$folder   = Join-Path $Destination ("AUS-Reporting-" + ($Branch -replace "/", "-"))
$zipPath  = Join-Path $Destination "pack.zip"

# Step out of the folder about to be replaced, or the extract hits a
# file lock and fails halfway.
$startedIn = (Get-Location).Path
if ($startedIn -like "$folder*") { Set-Location $Destination }

if (-not (Test-Path $Destination)) {
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
}

Write-Host "Fetching $Branch..." -ForegroundColor Cyan
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$before = if (Test-Path $folder) {
    @(Get-ChildItem $folder -Recurse -File | ForEach-Object {
        "$($_.FullName)|$($_.Length)|$($_.LastWriteTimeUtc.Ticks)"
    })
} else { @() }

# -UseBasicParsing keeps this working where Internet Explorer's engine
# is unavailable, which is most server builds.
Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing
Expand-Archive -Path $zipPath -DestinationPath $Destination -Force
Remove-Item $zipPath -Force -ErrorAction SilentlyContinue

if (-not (Test-Path $folder)) {
    throw "Extracted, but $folder is not there. Has the branch name changed?"
}

Get-ChildItem $folder -Recurse -File | Unblock-File

$after = @(Get-ChildItem $folder -Recurse -File | ForEach-Object {
    "$($_.FullName)|$($_.Length)|$($_.LastWriteTimeUtc.Ticks)"
})
$changed = @(Compare-Object -ReferenceObject $before -DifferenceObject $after |
             Where-Object { $_.SideIndicator -eq "=>" }).Count

$dashboard = Join-Path $folder "dashboard"
if (Test-Path $dashboard) { Set-Location $dashboard }

# Per-window only; reverts when this window closes.
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force

Write-Host ""
if ($before.Count -eq 0) {
    Write-Host "Installed to $folder" -ForegroundColor Green
} elseif ($changed -eq 0) {
    Write-Host "Already up to date." -ForegroundColor Green
} else {
    Write-Host "Updated - $changed file(s) changed." -ForegroundColor Green
}
Write-Host "You are in $(Get-Location). Scripts are unblocked and ready to run." -ForegroundColor DarkGray
