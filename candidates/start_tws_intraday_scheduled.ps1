# =============================================================================
# Q-ALPHA — candidates/start_tws_intraday_scheduled.ps1
# Local Live TWS sync (Mon–Fri): marks + filled-flat→CLOSED → Supabase.
# Modal CANNOT reach TWS — this must run on the PC with TWS paper open.
#
# Syncs each run (via tws_intraday_sync.py → sync_tsd_positions_to_supabase):
#   TSD open legs + TWS marks, closed legs, pool snapshot, watchlist, watch queue
#   + gap-agent paper marks (unchanged)
#
# Peak Hour on-fill also pushes Supabase immediately (execute_live_entries /
# trail exits). This job is the 30-min refresh for marks/closes through the day.
#
# Register / refresh (Aaron — agent does NOT create the task):
#   Prefer 07:00 ET start so 07:15 Peak Hour fills get mark refreshes:
#   schtasks /Create /F /TN "QAlpha Live TWS Sync" /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"C:\Users\ajkle\Documents\Q-ALPHA\candidates\start_tws_intraday_scheduled.ps1\"" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 07:00 /RI 30 /DU 09:30
#
# Legacy 09:40 / DU 06:30 still OK if on-fill sync is live; prefer 07:00.
#
# ClientId 96 (not agent 5 / connector 1 / flatten 97).
# =============================================================================
$ErrorActionPreference = "Continue"

$CandDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $CandDir
Set-Location -LiteralPath $Root

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:PYTHONPATH = $Root

$Python = Join-Path $Root "venv\Scripts\python.exe"
$Runner = Join-Path $CandDir "tws_intraday_sync.py"

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
$LogFile = Join-Path $LogDir "tws_sync_${etDate}.log"

$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
Add-Content -LiteralPath $LogFile -Value ""
Add-Content -LiteralPath $LogFile -Value "======== LIVE TWS SYNC START $stamp ========"

& $Python $Runner *>> $LogFile
$exitCode = $LASTEXITCODE
if ($null -eq $exitCode) { $exitCode = 0 }

$endStamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
Add-Content -LiteralPath $LogFile -Value "======== LIVE TWS SYNC END exit=$exitCode $endStamp ========"
exit $exitCode
