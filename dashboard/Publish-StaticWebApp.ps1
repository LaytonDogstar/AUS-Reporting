<#
.SYNOPSIS
    Publish the built dashboard to Azure Static Web Apps.

.DESCRIPTION
    Stages the page as index.html with an auth config, then deploys it
    with the Azure Static Web Apps CLI.

    The CLI is run through npx, so nothing is installed globally - but it
    does need Node.js on this machine. An earlier version of this script
    tried a plain REST call to avoid that; the deployment endpoint is not
    documented for this use and the app's hostname is auto-generated
    rather than derived from its name, so that approach was guesswork.
    This is the supported path.

    The deployment token is a credential: it can publish to your site.
    Treat it like a password, and rotate it if it is ever exposed.

.EXAMPLE
    .\Publish-StaticWebApp.ps1 -File C:\reports\dashboard.html -DeploymentToken "..."

.EXAMPLE
    # Token from the environment, for a scheduled run
    [Environment]::SetEnvironmentVariable("AUS_SWA_TOKEN", "...", "User")
    .\Publish-StaticWebApp.ps1 -File C:\reports\dashboard.html
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $File,

    [string] $DeploymentToken = $env:AUS_SWA_TOKEN,

    # Must match the role assigned when inviting people in the Portal.
    [string] $RequiredRole = "reader",

    [string] $Environment = "production"
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $File)) { throw "no such file: $File" }
if (-not $DeploymentToken) {
    throw ("No deployment token. Pass -DeploymentToken, or set " +
           "`$env:AUS_SWA_TOKEN. Portal > the Static Web App > Overview > " +
           "Manage deployment token.")
}

# --- prerequisite -----------------------------------------------------
$npx = Get-Command npx -ErrorAction SilentlyContinue
if (-not $npx) {
    throw @"
Node.js is not installed, and the Static Web Apps CLI needs it.

Install it from https://nodejs.org (the LTS build), reopen PowerShell,
and run this again. Nothing else is required - the CLI itself is fetched
on demand by npx and is not installed permanently.
"@
}

# --- stage the site ---------------------------------------------------
$staging = Join-Path ([System.IO.Path]::GetTempPath()) ("swa-" + [guid]::NewGuid().ToString("N"))
$siteDir = Join-Path $staging "site"
New-Item -ItemType Directory -Path $siteDir -Force | Out-Null

try {
    Copy-Item -Path $File -Destination (Join-Path $siteDir "index.html") -Force

    # This file is what enforces the sign-in. Without it the site is
    # public, whatever the Portal shows.
    #
    # The role is a CUSTOM one, not the built-in "authenticated".
    # "authenticated" means signed in with any Microsoft account - any
    # account anywhere, not just someone in this tenant. A custom role
    # is held only by people explicitly invited under Role management.
    $config = @{
        routes = @(
            @{ route = "/*"; allowedRoles = @($RequiredRole) }
        )
        responseOverrides = @{
            "401" = @{ statusCode = 302; redirect = "/.auth/login/aad" }
        }
        globalHeaders = @{
            # Rebuilt on a schedule, so a cached copy is worse than a
            # re-fetch.
            "cache-control" = "no-cache, max-age=0"
        }
    } | ConvertTo-Json -Depth 6

    Set-Content -Path (Join-Path $siteDir "staticwebapp.config.json") `
        -Value $config -Encoding UTF8

    $sizeMb = [math]::Round((Get-Item (Join-Path $siteDir "index.html")).Length / 1MB, 2)
    Write-Host "Staged index.html ($sizeMb MB) and the auth config" -ForegroundColor DarkGray
    Write-Host "Deploying..." -ForegroundColor Cyan
    Write-Host "(the first run downloads the CLI, so it takes a minute longer)" -ForegroundColor DarkGray
    Write-Host ""

    # --deployment-token keeps the token off the command line where
    # possible; npx passes it through to the CLI.
    & npx --yes @azure/static-web-apps-cli deploy $siteDir `
        --deployment-token $DeploymentToken `
        --env $Environment

    if ($LASTEXITCODE -ne 0) {
        throw ("swa deploy exited with code $LASTEXITCODE. A 401 or 403 usually " +
               "means the deployment token is wrong or has been rotated.")
    }

    Write-Host ""
    Write-Host "Published." -ForegroundColor Green
    Write-Host ""
    Write-Host "Access requires the '$RequiredRole' role." -ForegroundColor Yellow
    Write-Host "Portal > Role management > Invite > assign '$RequiredRole'." -ForegroundColor Yellow
    Write-Host "Invite yourself first, or your own site will refuse you." -ForegroundColor Yellow
}
finally {
    if (Test-Path $staging) { Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue }
}
