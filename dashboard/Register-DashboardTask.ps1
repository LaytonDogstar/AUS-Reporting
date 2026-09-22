<#
.SYNOPSIS
    Register the scheduled task that rebuilds the dashboard.

.DESCRIPTION
    Creates a Windows scheduled task running Build-Dashboard.ps1 on an
    interval, as the account you run this under.

    That account matters: the SQL password is stored with DPAPI, which
    ties it to one Windows account on one machine. Run this as the same
    account that ran Save-DashboardCredential.ps1, or the build will
    start and fail to decrypt.

    Run Save-DashboardCredential.ps1 FIRST.

.EXAMPLE
    .\Register-DashboardTask.ps1 -Out C:\reports\dashboard.html

.EXAMPLE
    # Rebuild every 30 minutes and publish to Azure
    .\Register-DashboardTask.ps1 -Minutes 30 -PublishSasUrl "https://acct.blob.core.windows.net/`$web?sv=..."

.EXAMPLE
    .\Register-DashboardTask.ps1 -Remove
#>
[CmdletBinding()]
param(
    [string] $TaskName = "AUS Reporting Dashboard",
    [int]    $Minutes = 60,
    [int]    $Days = 90,
    [string] $Out = "C:\reports\dashboard.html",
    [string] $PublishSasUrl,
    [switch] $Remove
)

$ErrorActionPreference = 'Stop'

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed scheduled task '$TaskName'." -ForegroundColor Green
    } else {
        Write-Host "No scheduled task named '$TaskName'." -ForegroundColor Yellow
    }
    return
}

if ($Minutes -lt 5)   { throw "-Minutes must be at least 5. The source is about two minutes behind live; rebuilding faster than that buys nothing." }
if ($Minutes -gt 1440){ throw "-Minutes must be 1440 or less" }

$here = if ($PSScriptRoot) { $PSScriptRoot }
        else { Split-Path -Parent $MyInvocation.MyCommand.Definition }
$buildScript = Join-Path $here "Build-Dashboard.ps1"
if (-not (Test-Path $buildScript)) { throw "missing: $buildScript" }

$credentialPath = Join-Path $env:LOCALAPPDATA "AUS-Reporting\source.cred"
if (-not (Test-Path $credentialPath)) {
    throw ("No stored credential at $credentialPath. Run " +
           ".\Save-DashboardCredential.ps1 first, as this same account - " +
           "a scheduled task cannot answer a password prompt.")
}

# -File rather than -Command: no quoting games with the paths below.
$arguments = @(
    "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
    "-File", "`"$buildScript`"",
    "-Days", $Days,
    "-Out", "`"$Out`""
)
if ($PublishSasUrl) { $arguments += @("-PublishSasUrl", "`"$PublishSasUrl`"") }

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument ($arguments -join " ") -WorkingDirectory $here

# Repeats indefinitely from the next whole minute.
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $Minutes)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew    # a slow build must not stack up

# S4U: runs whether or not anyone is logged on, and keeps the DPAPI
# identity, without storing a Windows password anywhere.
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Replacing the existing task." -ForegroundColor DarkGray
}

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description "Rebuilds the AUS lead reporting dashboard." | Out-Null

Write-Host ""
Write-Host "Registered '$TaskName'" -ForegroundColor Green
Write-Host "  Every      : $Minutes minute(s)"
Write-Host "  Window     : $Days days"
Write-Host "  Output     : $Out"
Write-Host "  Runs as    : $env:USERDOMAIN\$env:USERNAME"
if ($PublishSasUrl) { Write-Host "  Publishing : yes" }
Write-Host ""
Write-Host "Run it now to check it works:" -ForegroundColor Yellow
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Then look at the result:" -ForegroundColor Yellow
Write-Host "  Get-ScheduledTaskInfo -TaskName '$TaskName'"
Write-Host "  (LastTaskResult 0 means success.)"
