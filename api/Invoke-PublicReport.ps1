<#
.SYNOPSIS
    Call the PublicReports API and record what each report returns.

.DESCRIPTION
    Answers two questions the Swagger definition cannot: whether the
    endpoints require authentication at all, and what the reports
    actually return, since every response is declared as a bare object.

    Credentials are typed at the prompt and held as a SecureString.
    Nothing is stored, nothing is written to disk, and nothing goes on
    the command line, where PowerShell would keep it in history.

    Writes two things per endpoint:

      <name>.json   the raw response. May contain real lead data.
                    Treat as restricted and do not send it on.
      <name>.shape.txt  field names, types and numeric values, with every
                    string redacted to its type and length. This is the
                    file that is safe to share.

.EXAMPLE
    .\Invoke-PublicReport.ps1

.EXAMPLE
    .\Invoke-PublicReport.ps1 -Days 7 -OutDir C:\discovery\api

.NOTES
    Windows PowerShell 5.1 or PowerShell 7. No modules needed.
#>
[CmdletBinding()]
param(
    [string] $BaseUrl  = "https://admin.overflowhub.com.au",
    [int]    $Days     = 7,
    [string] $OutDir   = "C:\discovery\api",
    [string] $Username
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }

# --- 1. Does this need credentials at all? -------------------------
# Asked before authenticating, because the answer only means something
# when no credentials are sent.
Write-Host "Checking whether the API requires authentication..." -ForegroundColor Cyan
$anonUrl = "$BaseUrl/api/PublicReports/Affiliates"
try {
    $anon = Invoke-WebRequest -Uri $anonUrl -Method GET -UseBasicParsing -TimeoutSec 30
    $anonCode = $anon.StatusCode
} catch {
    $anonCode = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { -1 }
}

if ($anonCode -eq 200) {
    Write-Host ""
    Write-Host "  *** $anonCode - the endpoint answered WITHOUT credentials. ***" -ForegroundColor Red
    Write-Host "  Anyone who knows the URL can read it. Raise this before anything else." -ForegroundColor Red
    Write-Host ""
} elseif ($anonCode -in 401, 403) {
    Write-Host "  $anonCode - authentication required. That is the answer we wanted." -ForegroundColor Green
} else {
    Write-Host "  $anonCode - inconclusive; note it and carry on." -ForegroundColor Yellow
}

# --- 2. Credentials ------------------------------------------------
if (-not $Username) { $Username = Read-Host "API username" }
$securePassword = Read-Host "Password for '$Username'" -AsSecureString

$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
try {
    $plain  = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    $pair   = [Text.Encoding]::UTF8.GetBytes("${Username}:${plain}")
    $header = @{ Authorization = "Basic " + [Convert]::ToBase64String($pair) }
} finally {
    # Clear the plaintext as soon as the header exists.
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    Remove-Variable plain -ErrorAction SilentlyContinue
}

# --- 3. Describe a response without disclosing it ------------------
function Get-Shape {
    <#  Walks a parsed JSON response and reports structure only.
        Numbers are kept: they are the counts we need to compare.
        Strings are reduced to a type and a length, because this is
        where a name, an email or an address would appear. #>
    param($Node, [string] $Path = "$", [int] $Depth = 0, [System.Collections.ArrayList] $Lines)

    if ($Depth -gt 6) { [void]$Lines.Add("$Path  (deeper levels not walked)"); return }
    if ($null -eq $Node) { [void]$Lines.Add("$Path : null"); return }

    if ($Node -is [System.Collections.IEnumerable] -and $Node -isnot [string]) {
        $items = @($Node)
        [void]$Lines.Add("$Path : array[$($items.Count)]")
        if ($items.Count -gt 0) { Get-Shape -Node $items[0] -Path "$Path[0]" -Depth ($Depth+1) -Lines $Lines }
        return
    }

    if ($Node.PSObject -and $Node.PSObject.Properties.Name.Count -gt 0 -and
        $Node -isnot [string] -and $Node -isnot [ValueType]) {
        foreach ($prop in $Node.PSObject.Properties) {
            Get-Shape -Node $prop.Value -Path "$Path.$($prop.Name)" -Depth ($Depth+1) -Lines $Lines
        }
        return
    }

    switch ($Node.GetType().Name) {
        'String' {
            $v = [string]$Node
            $kind = if ($v -match '^[0-9a-fA-F-]{36}$') { 'guid' }
                    elseif ($v -match '^\d{4}-\d{2}-\d{2}') { 'date-like' }
                    else { 'string' }
            [void]$Lines.Add("$Path : $kind (len $($v.Length)) [value withheld]")
        }
        'Boolean' { [void]$Lines.Add("$Path : bool = $Node") }
        default   { [void]$Lines.Add("$Path : number = $Node") }
    }
}

# --- 4. The calls --------------------------------------------------
$end   = (Get-Date).Date
$start = $end.AddDays(-$Days)
$fmt   = "yyyy-MM-ddTHH:mm:ss"
$range = "startDate=$($start.ToString($fmt))&endDate=$($end.ToString($fmt))"

Write-Host ""
Write-Host "Window: $($start.ToString('yyyy-MM-dd')) to $($end.ToString('yyyy-MM-dd'))" -ForegroundColor Cyan

$calls = @(
    @{ Name = "Affiliates";            Url = "$BaseUrl/api/PublicReports/Affiliates" }
    @{ Name = "LenderTiers";           Url = "$BaseUrl/api/PublicReports/LenderTiers" }
    @{ Name = "LenderLeadCountReport"; Url = "$BaseUrl/api/PublicReports/LenderLeadCountReport?$range" }
    @{ Name = "LeadQualityReport";     Url = "$BaseUrl/api/PublicReports/LeadQualityReport?$range" }
)

foreach ($call in $calls) {
    Write-Host ""
    Write-Host "GET $($call.Name)" -ForegroundColor Cyan
    try {
        $sw  = [Diagnostics.Stopwatch]::StartNew()
        $res = Invoke-WebRequest -Uri $call.Url -Method GET -Headers $header `
                                 -UseBasicParsing -TimeoutSec 300
        $sw.Stop()

        $rawPath = Join-Path $OutDir "$($call.Name).json"
        $res.Content | Set-Content -Path $rawPath -Encoding UTF8
        Write-Host ("  {0} in {1}s, {2:N0} bytes -> {3}" -f `
            $res.StatusCode, [math]::Round($sw.Elapsed.TotalSeconds,2), `
            $res.Content.Length, $rawPath) -ForegroundColor Green

        $lines = New-Object System.Collections.ArrayList
        try {
            Get-Shape -Node ($res.Content | ConvertFrom-Json) -Lines $lines
        } catch {
            [void]$lines.Add("response is not JSON: $($_.Exception.Message)")
        }
        $shapePath = Join-Path $OutDir "$($call.Name).shape.txt"
        $lines | Set-Content -Path $shapePath -Encoding UTF8
        Write-Host "  shape -> $shapePath ($($lines.Count) field(s))" -ForegroundColor Green
    }
    catch {
        $code = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { "-" }
        Write-Host "  failed: $code $($_.Exception.Message)" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "Send the .shape.txt files. Keep the .json files local - they may" -ForegroundColor Yellow
Write-Host "hold real lead data." -ForegroundColor Yellow
