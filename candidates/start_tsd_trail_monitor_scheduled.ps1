# =============================================================================
# Q-ALPHA — candidates/start_tsd_trail_monitor_scheduled.ps1
# TSD software trail monitor loop (clientId 95). Runs while TWS is open.
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
$Runner = Join-Path $CandDir "tsd_scan_pipeline\tsd_trail_monitor.py"

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
$LogFile = Join-Path $LogDir "tsd_trail_${etDate}.log"

$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
Add-Content -LiteralPath $LogFile -Value ""
Add-Content -LiteralPath $LogFile -Value "======== TSD TRAIL MONITOR START $stamp ========"

# 60s loop with adaptive RTH 30s / extended 5m polling (TWS must be open)
& $Python $Runner --loop --adaptive *>> $LogFile
$exitCode = $LASTEXITCODE
if ($null -eq $exitCode) { $exitCode = 0 }

$endStamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
Add-Content -LiteralPath $LogFile -Value "======== TSD TRAIL MONITOR END exit=$exitCode $endStamp ========"
exit $exitCode
