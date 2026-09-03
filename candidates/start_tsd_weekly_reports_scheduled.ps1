# =============================================================================
# Q-ALPHA — candidates/start_tsd_weekly_reports_scheduled.ps1
# Friday 5 PM ET — TSD weekly scorecard + options overlay study.
#
# Register (Aaron runs once):
#   .\candidates\register_tsd_tasks.ps1
# =============================================================================
$ErrorActionPreference = "Continue"

$CandDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $CandDir
Set-Location -LiteralPath $Root

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:PYTHONPATH = $Root

$Python = Join-Path $Root "venv\Scripts\python.exe"
$Scorecard = Join-Path $CandDir "tsd_scan_pipeline\tsd_scorecard.py"
$OptionsStudy = Join-Path $CandDir "tsd_scan_pipeline\tsd_options_study.py"

if (-not (Test-Path -LiteralPath $Python)) {
    Write-Error "venv python not found at $Python"
    exit 1
}

$LogDir = Join-Path $CandDir "logs"
if (-not (Test-Path -LiteralPath $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

try {
    $etTz = [System.TimeZoneInfo]::FindSystemTimeZoneById("Eastern Standard Time")
    $etNow = [System.TimeZoneInfo]::ConvertTime([datetime]::Now, $etTz)
    $etDate = $etNow.ToString("yyyy-MM-dd")
} catch {
    $etDate = Get-Date -Format "yyyy-MM-dd"
}
$LogFile = Join-Path $LogDir "tsd_weekly_reports_${etDate}.log"

$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
Add-Content -LiteralPath $LogFile -Value ""
Add-Content -LiteralPath $LogFile -Value "======== TSD WEEKLY REPORTS START $stamp ========"

Write-Host "TSD weekly scorecard..."
& $Python $Scorecard --days 5 --write *>> $LogFile
$rc1 = $LASTEXITCODE
if ($null -eq $rc1) { $rc1 = 0 }

Write-Host "Peak Hour weekly funnel..."
$PhpFunnel = Join-Path $CandDir "uts_v2\php_weekly_funnel.py"
& $Python $PhpFunnel --days 7 --write *>> $LogFile
$rcPhp = $LASTEXITCODE
if ($null -eq $rcPhp) { $rcPhp = 0 }

Write-Host "TSD options overlay study..."
& $Python $OptionsStudy --days 5 --write *>> $LogFile
$rc2 = $LASTEXITCODE
if ($null -eq $rc2) { $rc2 = 0 }

$exitCode = if ($rc1 -ne 0 -or $rc2 -ne 0 -or $rcPhp -ne 0) { 1 } else { 0 }
$endStamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
Add-Content -LiteralPath $LogFile -Value "======== TSD WEEKLY REPORTS END exit=$exitCode scorecard=$rc1 php_funnel=$rcPhp study=$rc2 $endStamp ========"
exit $exitCode
