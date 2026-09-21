<#
.SYNOPSIS
    Run a SQL query or .sql file against a source database from PowerShell.

.DESCRIPTION
    For ad-hoc questions without opening SSMS. SQL cannot be pasted
    straight into a PowerShell prompt - PowerShell tries to run SELECT as
    a command - so this wraps it properly.

    Read-only by intent: it sets READ UNCOMMITTED and is meant for the
    reporting replica. It will run whatever you give it, so give it
    queries.

    Prompts for the password and holds it as a SecureString. Nothing is
    stored.

.EXAMPLE
    .\Invoke-Sql.ps1 -Database OverflowReporting -Query "SELECT TOP 5 * FROM dbo.LeadMetrics"

.EXAMPLE
    .\Invoke-Sql.ps1 -Database OverflowReporting -File .\stage_ids.sql

.EXAMPLE
    # Straight to a CSV you can send on
    .\Invoke-Sql.ps1 -Database OverflowReporting -File .\stage_ids.sql -OutCsv C:\discovery\stages.csv

.NOTES
    Needs Windows PowerShell 5.1 (the blue console).
#>
[CmdletBinding(DefaultParameterSetName = 'Query')]
param(
    [Parameter(Mandatory = $true)]
    [string] $Database,

    [Parameter(Mandatory = $true, ParameterSetName = 'Query')]
    [string] $Query,

    [Parameter(Mandatory = $true, ParameterSetName = 'File')]
    [string] $File,

    [string] $ServerName = 'fw04-sqlreporting01.database.windows.net',
    [string] $Username,
    [string] $OutCsv,
    [int]    $TimeoutSeconds = 600
)

$ErrorActionPreference = 'Stop'

if ($PSVersionTable.PSEdition -eq 'Core') {
    throw ("Needs Windows PowerShell 5.1. Re-run with: " +
           "powershell.exe -File .\Invoke-Sql.ps1 ...")
}

if ($PSCmdlet.ParameterSetName -eq 'File') {
    if (-not (Test-Path $File)) { throw "no such file: $File" }
    $sql = Get-Content -Path $File -Raw
} else {
    $sql = $Query
}

if (-not $Username) { $Username = Read-Host "SQL login" }
$securePassword = Read-Host "Password for '$Username'" -AsSecureString
$securePassword.MakeReadOnly()

$builder = New-Object System.Data.SqlClient.SqlConnectionStringBuilder
$builder['Data Source']            = "tcp:$ServerName,1433"
$builder['Initial Catalog']        = $Database
$builder['Encrypt']                = $true
$builder['TrustServerCertificate'] = $false
$builder['Connect Timeout']        = 30
$builder['Application Name']       = 'AUS-Reporting-Adhoc'

$connection = New-Object System.Data.SqlClient.SqlConnection
$connection.ConnectionString = $builder.ConnectionString
$connection.Credential = New-Object System.Data.SqlClient.SqlCredential(
    $Username, $securePassword)

try {
    $connection.Open()
    Write-Host "Connected to $Database. Running..." -ForegroundColor Green

    $command = $connection.CreateCommand()
    # Reporting reads must never block the replication subscriber.
    $command.CommandText = "SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;`n" + $sql
    $command.CommandTimeout = $TimeoutSeconds

    $adapter = New-Object System.Data.SqlClient.SqlDataAdapter $command
    $dataSet = New-Object System.Data.DataSet
    [void]$adapter.Fill($dataSet)

    $setNumber = 0
    foreach ($table in $dataSet.Tables) {
        $setNumber++
        if ($table.Columns.Count -eq 0) { continue }

        # Select the real columns: piping DataRow objects straight out
        # would add RowError, RowState and friends.
        $columns = @($table.Columns | ForEach-Object { $_.ColumnName })
        $rows = $table.Rows | Select-Object -Property $columns

        if ($dataSet.Tables.Count -gt 1) {
            Write-Host ""
            Write-Host "--- result set $setNumber ($($table.Rows.Count) row(s)) ---" -ForegroundColor Cyan
        }

        if ($OutCsv) {
            $path = if ($dataSet.Tables.Count -gt 1) {
                [System.IO.Path]::ChangeExtension($OutCsv, "set$setNumber.csv")
            } else { $OutCsv }
            $rows | Export-Csv -Path $path -NoTypeInformation -Encoding UTF8
            Write-Host "wrote $path" -ForegroundColor Green
        } else {
            $rows | Format-Table -AutoSize
        }
    }
}
finally {
    if ($connection.State -ne 'Closed') { $connection.Close() }
    $connection.Dispose()
}
