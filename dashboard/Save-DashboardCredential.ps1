<#
.SYNOPSIS
    Store the SQL password for unattended builds. Run once.

.DESCRIPTION
    A scheduled task cannot answer a password prompt, so the password has
    to be readable by the build without a person present.

    It is encrypted with Windows DPAPI, which ties the ciphertext to BOTH
    this Windows account and this machine. Copying the file to another
    machine, or reading it as another user, produces nothing usable. It
    is not a secret you can accidentally email.

    Re-run this whenever the password changes.

.EXAMPLE
    .\Save-DashboardCredential.ps1
#>
[CmdletBinding()]
param(
    [string] $Username,
    [string] $Path = (Join-Path $env:LOCALAPPDATA "AUS-Reporting\source.cred")
)

$ErrorActionPreference = 'Stop'

if ($PSVersionTable.PSEdition -eq 'Core') {
    throw "Use Windows PowerShell 5.1 - DPAPI protection differs on PowerShell 7."
}

if (-not $Username) { $Username = Read-Host "SQL login" }
$password = Read-Host "Password for '$Username'" -AsSecureString

$directory = Split-Path -Parent $Path
if (-not (Test-Path $directory)) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}

# ConvertFrom-SecureString with no key uses DPAPI: user + machine scoped.
[pscustomobject]@{
    Username = $Username
    Password = ($password | ConvertFrom-SecureString)
    SavedUtc = (Get-Date).ToUniversalTime().ToString("s") + "Z"
    SavedBy  = "$env:USERDOMAIN\$env:USERNAME"
    Machine  = $env:COMPUTERNAME
} | ConvertTo-Json | Set-Content -Path $Path -Encoding UTF8

Write-Host ""
Write-Host "Saved to $Path" -ForegroundColor Green
Write-Host "Readable only by $env:USERDOMAIN\$env:USERNAME on $env:COMPUTERNAME." -ForegroundColor Yellow
Write-Host "The scheduled task must run as that same account, or it will not decrypt." -ForegroundColor Yellow
