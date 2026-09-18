<#
.SYNOPSIS
    Runs the AUS reporting schema-discovery pack and writes one CSV per
    result set.

.DESCRIPTION
    Run this from FLOWWEB4, or anywhere with network access to the
    instance. It uses ADO.NET, which ships with Windows - there is no
    module to install and no dependency on sqlcmd or SSMS.

    The password is prompted for at runtime and held as a SecureString.
    It is never written to disk, never echoed, and never stored in this
    script. Do not add it here.

.EXAMPLE
    .\run-discovery.ps1 -ServerName fw04-sqlreporting01.database.windows.net -Database OverflowReporting

.EXAMPLE
    # Both databases, one after the other:
    .\run-discovery.ps1 -ServerName fw04-sqlreporting01.database.windows.net -Database Overflow
    .\run-discovery.ps1 -ServerName fw04-sqlreporting01.database.windows.net -Database OverflowReporting

.NOTES
    Requires Windows PowerShell 5.1 (the blue console, or "powershell.exe").
    PowerShell 7 does not load System.Data.SqlClient by default and this
    script will stop with a clear message if run there.

    Azure SQL Database cannot switch databases mid-connection, so the
    pack must be run once per database.

    05_date_coverage.sql is the only script that reads data, and the
    only slow one. Skip it with -SkipDateCoverage on a first pass.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $ServerName,

    [Parameter(Mandatory = $true)]
    [string] $Database,

    [Parameter(Mandatory = $false)]
    [string] $Username,

    [Parameter(Mandatory = $false)]
    [string] $OutputPath,

    [Parameter(Mandatory = $false)]
    [int] $CommandTimeoutSeconds = 900,

    [switch] $SkipDateCoverage
)

$ErrorActionPreference = 'Stop'

# --- Environment check ----------------------------------------------
if ($PSVersionTable.PSEdition -eq 'Core') {
    throw ("This script needs Windows PowerShell 5.1 (System.Data.SqlClient). " +
           "You are on PowerShell $($PSVersionTable.PSVersion). " +
           "Re-run it with: powershell.exe -File .\run-discovery.ps1 ...")
}

$scriptDir = if ($PSScriptRoot) { $PSScriptRoot }
             else { Split-Path -Parent $MyInvocation.MyCommand.Definition }

if (-not $OutputPath) {
    $stamp      = Get-Date -Format 'yyyyMMdd-HHmmss'
    $OutputPath = Join-Path $scriptDir "output\$Database-$stamp"
}
New-Item -ItemType Directory -Path $OutputPath -Force | Out-Null

Write-Host ""
Write-Host "AUS Reporting - schema discovery" -ForegroundColor Cyan
Write-Host "  Server   : $ServerName"
Write-Host "  Database : $Database"
Write-Host "  Output   : $OutputPath"
Write-Host ""

# --- Credentials -----------------------------------------------------
# Prompted every run. Nothing is persisted.
if (-not $Username) { $Username = Read-Host "SQL login" }
$securePassword = Read-Host "Password for '$Username'" -AsSecureString
$securePassword.MakeReadOnly()

$builder = New-Object System.Data.SqlClient.SqlConnectionStringBuilder
$builder['Data Source']            = "tcp:$ServerName,1433"
$builder['Initial Catalog']        = $Database
$builder['Encrypt']                = $true
$builder['TrustServerCertificate'] = $false
$builder['Connect Timeout']        = 30
$builder['Application Name']       = 'AUS-Reporting-Discovery'

$sqlCredential = New-Object System.Data.SqlClient.SqlCredential($Username, $securePassword)

# --- Which scripts to run -------------------------------------------
$scripts = @(Get-ChildItem -Path $scriptDir -Filter '*.sql' | Sort-Object Name)

if ($SkipDateCoverage) {
    $scripts = @($scripts | Where-Object { $_.Name -notlike '05_*' })
    Write-Host "Skipping 05_date_coverage.sql (-SkipDateCoverage)" -ForegroundColor Yellow
    Write-Host ""
}

if ($scripts.Count -eq 0) { throw "No .sql files found in $scriptDir" }

$connection = New-Object System.Data.SqlClient.SqlConnection
$connection.ConnectionString = $builder.ConnectionString
$connection.Credential       = $sqlCredential

$summary = @()

try {
    $connection.Open()
    Write-Host "Connected." -ForegroundColor Green
    Write-Host ""

    foreach ($script in $scripts) {
        $name = [System.IO.Path]::GetFileNameWithoutExtension($script.Name)
        Write-Host ("Running {0} ..." -f $script.Name) -NoNewline

        $sqlText = Get-Content -Path $script.FullName -Raw

        # Defensive: honour GO batch separators if any are ever added.
        $batches = @([System.Text.RegularExpressions.Regex]::Split($sqlText, '(?im)^\s*GO\s*$') |
                     Where-Object { $_.Trim().Length -gt 0 })

        $setIndex = 0
        $started  = Get-Date

        try {
            foreach ($batch in $batches) {
                $command = $connection.CreateCommand()
                $command.CommandText    = $batch
                $command.CommandTimeout = $CommandTimeoutSeconds

                $adapter = New-Object System.Data.SqlClient.SqlDataAdapter $command
                $dataSet = New-Object System.Data.DataSet
                try {
                    [void]$adapter.Fill($dataSet)
                }
                finally {
                    $command.Dispose()
                }

                foreach ($table in $dataSet.Tables) {
                    if ($table.Columns.Count -eq 0) { continue }
                    $setIndex++

                    $csvPath    = Join-Path $OutputPath ("{0}_set{1:d2}.csv" -f $name, $setIndex)
                    $columnList = @($table.Columns | ForEach-Object { $_.ColumnName })

                    if ($table.Rows.Count -gt 0) {
                        # Select only the real columns: piping DataRow objects
                        # straight to Export-Csv would add RowError, RowState,
                        # ItemArray and friends.
                        $table.Rows |
                            Select-Object -Property $columnList |
                            Export-Csv -Path $csvPath -NoTypeInformation -Encoding UTF8
                    }
                    else {
                        # Header-only file, so an empty result is obvious
                        # rather than looking like a failed export.
                        $header = ($columnList | ForEach-Object { '"' + ($_ -replace '"','""') + '"' }) -join ','
                        Set-Content -Path $csvPath -Value $header -Encoding UTF8
                    }

                    $summary += [pscustomobject]@{
                        Script    = $script.Name
                        ResultSet = $setIndex
                        Rows      = $table.Rows.Count
                        File      = Split-Path $csvPath -Leaf
                    }
                }

                $dataSet.Dispose()
            }

            $elapsed = [int]((Get-Date) - $started).TotalSeconds
            Write-Host (" ok ({0} result set(s), {1}s)" -f $setIndex, $elapsed) -ForegroundColor Green
        }
        catch {
            Write-Host " FAILED" -ForegroundColor Red
            Write-Host ("    {0}" -f $_.Exception.Message) -ForegroundColor Red
            $summary += [pscustomobject]@{
                Script    = $script.Name
                ResultSet = 0
                Rows      = 0
                File      = "ERROR: $($_.Exception.Message)"
            }
        }
    }
}
finally {
    if ($connection.State -ne 'Closed') { $connection.Close() }
    $connection.Dispose()
}

$summary | Export-Csv -Path (Join-Path $OutputPath '_run_summary.csv') -NoTypeInformation -Encoding UTF8

Write-Host ""
Write-Host "Done." -ForegroundColor Cyan
Write-Host "CSVs written to: $OutputPath"
Write-Host ""
Write-Host "Before sending these on, open 06_sensitive_columns_set01.csv and" -ForegroundColor Yellow
Write-Host "confirm nothing in the output set contains customer data." -ForegroundColor Yellow
Write-Host ""
$summary | Format-Table -AutoSize
