<#
.SYNOPSIS
    Build the AUS reporting dashboard - no Python required.

.DESCRIPTION
    Runs the queries in dashboard\sql\*.sql against both source
    databases and writes a self-contained HTML file.

    This is the same job as dashboard\build.py. It exists because
    FLOWWEB4 has no Python, and installing it is a change to a machine
    someone else owns. Both read the SAME .sql files and the SAME
    stages.json, so the two cannot drift apart.

    Read-only throughout. Nothing is written to either source database.

.EXAMPLE
    .\Build-Dashboard.ps1 -Days 90 -Out C:\reports\dashboard.html

.EXAMPLE
    # Unattended, for Task Scheduler: credentials from the environment.
    $env:AUS_SOURCE_USERNAME = "svc_reporting"
    $env:AUS_SOURCE_PASSWORD = "..."
    .\Build-Dashboard.ps1 -Out C:\reports\dashboard.html

.NOTES
    Needs Windows PowerShell 5.1 (the blue console).
#>
[CmdletBinding()]
param(
    [int]    $Days = 90,
    [string] $Out = "dashboard.html",
    [string] $ServerName = $(if ($env:AUS_SOURCE_SERVER) { $env:AUS_SOURCE_SERVER }
                            else { "fw04-sqlreporting01.database.windows.net" }),
    [string] $Username,
    [int]    $TimeoutSeconds = 900
)

$ErrorActionPreference = 'Stop'

if ($PSVersionTable.PSEdition -eq 'Core') {
    throw ("This script needs Windows PowerShell 5.1 (System.Data.SqlClient). " +
           "Re-run with: powershell.exe -File .\Build-Dashboard.ps1 ...")
}
if ($Days -lt 1) { throw "-Days must be at least 1" }

$here = if ($PSScriptRoot) { $PSScriptRoot }
        else { Split-Path -Parent $MyInvocation.MyCommand.Definition }

$metaPath     = Join-Path $here "stages.json"
$templatePath = Join-Path $here "template.html"
foreach ($p in @($metaPath, $templatePath)) {
    if (-not (Test-Path $p)) { throw "missing file: $p" }
}

$meta = Get-Content $metaPath -Raw | ConvertFrom-Json

# --- credentials ------------------------------------------------------
if (-not $Username) {
    $Username = if ($env:AUS_SOURCE_USERNAME) { $env:AUS_SOURCE_USERNAME }
                else { Read-Host "SQL login" }
}
if ($env:AUS_SOURCE_PASSWORD) {
    $securePassword = ConvertTo-SecureString $env:AUS_SOURCE_PASSWORD -AsPlainText -Force
} else {
    $securePassword = Read-Host "Password for '$Username'" -AsSecureString
}
$securePassword.MakeReadOnly()

function New-SourceConnection {
    param([string] $Database)

    $builder = New-Object System.Data.SqlClient.SqlConnectionStringBuilder
    $builder['Data Source']            = "tcp:$ServerName,1433"
    $builder['Initial Catalog']        = $Database
    $builder['Encrypt']                = $true
    $builder['TrustServerCertificate'] = $false
    $builder['Connect Timeout']        = 30
    $builder['ApplicationIntent']      = 'ReadOnly'
    $builder['Application Name']       = 'AUS-Reporting-Dashboard'

    $connection = New-Object System.Data.SqlClient.SqlConnection
    $connection.ConnectionString = $builder.ConnectionString
    $connection.Credential = New-Object System.Data.SqlClient.SqlCredential(
        $Username, $securePassword)
    $connection.Open()
    # Reporting reads must never block the replication subscriber.
    $iso = $connection.CreateCommand()
    $iso.CommandText = "SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;"
    [void]$iso.ExecuteNonQuery()
    return $connection
}

function Get-QuerySql {
    param([string] $Name, [int] $Window)

    $path = Join-Path (Join-Path $here "sql") "$Name.sql"
    if (-not (Test-Path $path)) { throw "no such query: $path" }
    $sql = Get-Content $path -Raw

    # Integers only - these are substituted into SQL, not bound. See the
    # header comment in each .sql file for why they are tokens.
    $sql = $sql.Replace("{{AEST_SHIFT_HOURS}}", [string][int]$meta.aestShiftHours)
    $sql = $sql.Replace("{{WINDOW_DAYS}}", [string](-[Math]::Abs([int]$Window)))

    if ($sql -match '\{\{') { throw "$Name.sql still contains an unsubstituted token" }
    return $sql
}

function Invoke-Rows {
    param([System.Data.SqlClient.SqlConnection] $Connection, [string] $Sql)

    $command = $Connection.CreateCommand()
    $command.CommandText = $Sql
    $command.CommandTimeout = $TimeoutSeconds

    $adapter = New-Object System.Data.SqlClient.SqlDataAdapter $command
    $table = New-Object System.Data.DataTable
    [void]$adapter.Fill($table)

    $columns = @($table.Columns | ForEach-Object { $_.ColumnName })
    $rows = New-Object System.Collections.Generic.List[object]

    foreach ($dataRow in $table.Rows) {
        $obj = [ordered]@{}
        foreach ($column in $columns) {
            $value = $dataRow[$column]
            if ($value -is [System.DBNull])      { $value = $null }
            elseif ($value -is [datetime])       { $value = $value.ToString('yyyy-MM-dd') }
            elseif ($value -is [decimal])        { $value = [double]$value }
            $obj[$column] = $value
        }
        $rows.Add([pscustomobject]$obj)
    }
    $command.Dispose()
    return ,$rows.ToArray()
}

# --- run --------------------------------------------------------------
Write-Host ""
Write-Host "AUS Reporting - dashboard build" -ForegroundColor Cyan
Write-Host "  Server : $ServerName"
Write-Host "  Window : $Days days"
Write-Host ""

$started = Get-Date
$results = @{}

# Grouped by database: Azure SQL Database cannot join across the two, so
# they are fetched separately and related in the page by AffiliateId.
$byDatabase = @{}
foreach ($name in $meta.sources.PSObject.Properties.Name) {
    $database = $meta.sources.$name
    if (-not $byDatabase.ContainsKey($database)) { $byDatabase[$database] = @() }
    $byDatabase[$database] += $name
}

foreach ($database in $byDatabase.Keys) {
    Write-Host "$database" -ForegroundColor Green
    $connection = New-SourceConnection -Database $database
    try {
        foreach ($name in $byDatabase[$database]) {
            $window = if ($meta.windowed -contains $name) { $Days } else { 0 }
            $sql = Get-QuerySql -Name $name -Window $window
            $queryStart = Get-Date
            $rows = Invoke-Rows -Connection $connection -Sql $sql
            $results[$name] = $rows
            $elapsed = [math]::Round(((Get-Date) - $queryStart).TotalSeconds, 1)
            Write-Host ("  {0,-14} {1,8:N0} row(s)  {2}s" -f $name, $rows.Count, $elapsed)
        }
    }
    finally {
        if ($connection.State -ne 'Closed') { $connection.Close() }
        $connection.Dispose()
    }
}

# --- write ------------------------------------------------------------
$data = [ordered]@{
    generatedUtc   = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    windowDays     = $Days
    aestShiftHours = [int]$meta.aestShiftHours
    stages         = $meta.stages
    stage_counts   = $results['stage_counts']
    applications   = $results['applications']
    accepts        = $results['accepts']
    affiliates     = $results['affiliates']
}

# Depth matters: the default of 2 would silently flatten the rows.
$json = $data | ConvertTo-Json -Depth 10 -Compress
# A literal </script> inside the payload would close the tag early.
$json = $json.Replace("</", "<\/")

$html = Get-Content $templatePath -Raw
$placeholder = "/*__DASHBOARD_DATA__*/null"
if (-not $html.Contains($placeholder)) {
    throw "template.html: data placeholder not found"
}
$html = $html.Replace($placeholder, $json)

$outPath = if ([System.IO.Path]::IsPathRooted($Out)) { $Out }
           else { Join-Path (Get-Location).Path $Out }
$outDir = Split-Path -Parent $outPath
if ($outDir -and -not (Test-Path $outDir)) {
    New-Item -ItemType Directory -Path $outDir -Force | Out-Null
}

# UTF-8 without a BOM.
[System.IO.File]::WriteAllText($outPath, $html, (New-Object System.Text.UTF8Encoding($false)))

$seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 1)
$sizeMb  = [math]::Round((Get-Item $outPath).Length / 1MB, 2)

Write-Host ""
Write-Host "wrote $outPath ($sizeMb MB) in ${seconds}s" -ForegroundColor Cyan
Write-Host "Open it in a browser - it needs no internet connection." -ForegroundColor Yellow
