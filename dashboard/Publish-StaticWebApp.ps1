<#
.SYNOPSIS
    Publish the built dashboard to Azure Static Web Apps.

.DESCRIPTION
    Uploads a built dashboard.html to a Static Web App using its
    deployment token, so the page gets a URL behind a real Entra sign-in.

    Deliberately avoids the SWA CLI, which needs Node.js - one more thing
    to install on a machine where installing things is the problem.
    It posts a zip to the Static Web App's deployment endpoint instead.

    *** This route is less well-trodden than the CLI or GitHub Actions.
    If it fails, see HOSTING.md for the App Service alternative, which
    reaches the same place - a URL with Entra login - over a much better
    documented deployment API. ***

    The deployment token is a credential: it can publish to your site.
    Treat it like a password.

.EXAMPLE
    .\Publish-StaticWebApp.ps1 -File C:\reports\dashboard.html -DeploymentToken "..."

.EXAMPLE
    # Token from the environment, for a scheduled run
    $env:AUS_SWA_TOKEN = "..."
    .\Publish-StaticWebApp.ps1 -File C:\reports\dashboard.html -AppName aus-reporting
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $File,

    [Parameter(Mandatory = $true)]
    [string] $AppName,

    [string] $DeploymentToken = $env:AUS_SWA_TOKEN,

    # Must match the role you assign when inviting people in the Portal.
    [string] $RequiredRole = "reader",

    [int] $TimeoutSeconds = 300
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $File)) { throw "no such file: $File" }
if (-not $DeploymentToken) {
    throw ("No deployment token. Pass -DeploymentToken, or set " +
           "`$env:AUS_SWA_TOKEN. Find it in the Azure Portal under the " +
           "Static Web App > Overview > Manage deployment token.")
}

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# --- stage the site ---------------------------------------------------
# A Static Web App serves a folder. Ours is one file, named index.html so
# it is served at the root.
$staging = Join-Path ([System.IO.Path]::GetTempPath()) ("swa-" + [guid]::NewGuid().ToString("N"))
$siteDir = Join-Path $staging "site"
New-Item -ItemType Directory -Path $siteDir -Force | Out-Null

try {
    Copy-Item -Path $File -Destination (Join-Path $siteDir "index.html") -Force

    # staticwebapp.config.json is what turns on the sign-in requirement.
    # Without it the site is public, whatever the portal shows.
    #
    # The role is a CUSTOM one, not the built-in "authenticated".
    # "authenticated" means "signed in with any Microsoft account" - on
    # the Free tier that is any Microsoft account in the world, not just
    # people in this tenant. A custom role is only held by someone
    # explicitly invited under Role management, so everyone else is
    # refused after signing in.
    $config = @{
        routes = @(
            @{ route = "/*"; allowedRoles = @($RequiredRole) }
        )
        responseOverrides = @{
            "401" = @{ statusCode = 302; redirect = "/.auth/login/aad" }
        }
        globalHeaders = @{
            # The page is rebuilt on a schedule; a cached copy is worse
            # than a re-fetch.
            "cache-control" = "no-cache, max-age=0"
        }
    } | ConvertTo-Json -Depth 6

    Set-Content -Path (Join-Path $siteDir "staticwebapp.config.json") `
        -Value $config -Encoding UTF8

    $zipPath = Join-Path $staging "site.zip"
    Compress-Archive -Path (Join-Path $siteDir "*") -DestinationPath $zipPath -Force

    $sizeKb = [math]::Round((Get-Item $zipPath).Length / 1KB, 1)
    Write-Host "Staged $sizeKb KB (index.html + auth config)" -ForegroundColor DarkGray

    # --- deploy -------------------------------------------------------
    $uri = "https://$AppName.scm.azurestaticapps.net/api/zipdeploy"
    Write-Host "Deploying to $AppName..." -ForegroundColor Cyan

    $headers = @{
        Authorization = "Bearer $DeploymentToken"
        "Content-Type" = "application/zip"
    }

    try {
        Invoke-RestMethod -Uri $uri -Method Post -Headers $headers `
            -InFile $zipPath -TimeoutSec $TimeoutSeconds | Out-Null
    }
    catch {
        $status = $null
        if ($_.Exception.Response) { $status = $_.Exception.Response.StatusCode.value__ }
        $hint = switch ($status) {
            401 { "the deployment token is wrong or has been rotated" }
            403 { "the token is valid but not for this app" }
            404 { "no Static Web App named '$AppName' - check the name, it is the resource name not the URL" }
            default { "this deployment route is less well-trodden than the SWA CLI; see HOSTING.md for the App Service alternative" }
        }
        throw "Deploy failed$(if ($status) { " (HTTP $status)" }): $hint`n$($_.Exception.Message)"
    }

    Write-Host ""
    Write-Host "Published." -ForegroundColor Green
    Write-Host "  https://$AppName.azurestaticapps.net" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Access requires the '$RequiredRole' role." -ForegroundColor Yellow
    Write-Host "In the Portal: Role management > Invite > assign '$RequiredRole'." -ForegroundColor Yellow
    Write-Host "Until someone holds that role, they will sign in and then be refused" -ForegroundColor Yellow
    Write-Host "- including you. Invite yourself first." -ForegroundColor Yellow
}
finally {
    if (Test-Path $staging) { Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue }
}
