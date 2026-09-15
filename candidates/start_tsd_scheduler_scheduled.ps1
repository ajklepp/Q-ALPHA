# =============================================================================
# Q-ALPHA — candidates/start_tsd_scheduler_scheduled.ps1
# TSD pipeline scheduler tick (UTS v2.6 launch :15 slots via --tick every 5 min).
#
# Register (Aaron runs once, and again after hour-8 guard):
#   .\candidates\register_tsd_tasks.ps1
# Task must be "Do not start a new instance" + 2h limit so a 5-min tick
# cannot kill an in-flight 1H LAUNCH (2026-09-14 hour-8 exit=-1).
# =============================================================================
$ErrorActionPreference = "Continue"

$CandDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $CandDir
Set-Location -LiteralPath $Root

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"
$env:PYTHONPATH = $Root

$Python = Join-Path $Root "venv\Scripts\python.exe"
$Runner = Join-Path $CandDir "tsd_scan_pipeline\scheduler.py"

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
$LogFile = Join-Path $LogDir "tsd_scheduler_${etDate}.log"

$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
Add-Content -LiteralPath $LogFile -Value ""
Add-Content -LiteralPath $LogFile -Value "======== TSD SCHEDULER TICK START $stamp ========"

# 2026-09-14 hour-8: a second 5-min Task Scheduler instance can terminate the
# in-flight 1H LAUNCH (TICK END exit=-1, no SCAN). Refuse to start a second
# starter; do not kill the owner. Task policy must also be IgnoreNew.
$starterName = "start_tsd_scheduler_scheduled.ps1"
$starters = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -and ($_.CommandLine -match [regex]::Escape($starterName))
})
$otherStarters = @($starters | Where-Object { $_.ProcessId -ne $PID })
if ($otherStarters.Count -gt 0) {
    $msg = "SKIP: another TSD scheduler tick is in flight (PIDs=$($otherStarters.ProcessId -join ',')). Do not start a new instance."
    Write-Host $msg
    Add-Content -LiteralPath $LogFile -Value $msg
    $endStamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
    Add-Content -LiteralPath $LogFile -Value "======== TSD SCHEDULER TICK END exit=0 $endStamp ========"
    exit 0
}

& $Python $Runner --tick --live 2>&1 | Out-File -FilePath $LogFile -Append -Encoding utf8
$exitCode = $LASTEXITCODE
if ($null -eq $exitCode) { $exitCode = 0 }

$endStamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
Add-Content -LiteralPath $LogFile -Value "======== TSD SCHEDULER TICK END exit=$exitCode $endStamp ========"
exit $exitCode
