# =============================================================================
# Q-ALPHA — strategy_lab/start_lab_scheduled.ps1
# Unattended launcher for Strategy Lab live_forward.py (Polygon-paper).
#
# Mirrors the main agent Task Scheduler pattern:
#   - Explicit venv\Scripts\python.exe (never system `py`)
#   - Working directory = repo root
#   - PYTHONIOENCODING / PYTHONUTF8 for emoji Telegram lines
#   - Daily log under strategy_lab/logs/
#
# Register (YOU run this — do not auto-create from an agent):
#   See comments at bottom, or the chat reply that shipped with this file.
#
# Manual test from repo root:
#   .\strategy_lab\start_lab_scheduled.ps1
# =============================================================================
$ErrorActionPreference = "Continue"

$LabDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $LabDir
Set-Location -LiteralPath $Root

# Match autonomous_agent Task Scheduler env (cp1252 consoles + emoji).
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
# Ensure .env next to repo is discoverable if dotenv looks at cwd.
$env:PYTHONPATH = $Root

$Python = Join-Path $Root "venv\Scripts\python.exe"
$Runner = Join-Path $LabDir "live_forward.py"

if (-not (Test-Path -LiteralPath $Python)) {
    Write-Error "venv python not found at $Python"
    exit 1
}
if (-not (Test-Path -LiteralPath $Runner)) {
    Write-Error "live_forward.py not found at $Runner"
    exit 1
}

$LogDir = Join-Path $LabDir "logs"
if (-not (Test-Path -LiteralPath $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

# Log filename uses US/Eastern calendar date (same TZ as is_trading_day).
try {
    $etTz = [System.TimeZoneInfo]::FindSystemTimeZoneById("Eastern Standard Time")
    $etNow = [System.TimeZoneInfo]::ConvertTime([datetime]::Now, $etTz)
    $etDate = $etNow.ToString("yyyy-MM-dd")
} catch {
    $etDate = Get-Date -Format "yyyy-MM-dd"
}
$LogFile = Join-Path $LogDir "lab_$etDate.log"

$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
Add-Content -LiteralPath $LogFile -Value ""
Add-Content -LiteralPath $LogFile -Value "======== LAB START $stamp ========"
Add-Content -LiteralPath $LogFile -Value "Root=$Root"
Add-Content -LiteralPath $LogFile -Value "Python=$Python"
Add-Content -LiteralPath $LogFile -Value "Cmd=live_forward.py (LIVE mode)"

# LIVE only — never --replay from the scheduled task.
# Redirect stdout+stderr into the daily log (append).
& $Python $Runner *>> $LogFile
$exitCode = $LASTEXITCODE
if ($null -eq $exitCode) { $exitCode = 0 }

$endStamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
Add-Content -LiteralPath $LogFile -Value "======== LAB END exit=$exitCode $endStamp ========"
Add-Content -LiteralPath $LogFile -Value ""

exit $exitCode

# -----------------------------------------------------------------------------
# REGISTER — use register_lab_tasks.ps1 (preferred) or run manually:
#
#   .\strategy_lab\register_lab_tasks.ps1
#
# Entry only (unquoted -File path — quoted paths fail with Last Result -65536):
#   schtasks /Create /F /TN "QAlpha Strategy Lab" /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\ajkle\Documents\Q-ALPHA\strategy_lab\start_lab_scheduled.ps1" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 09:35 /RL LIMITED
#
# VERIFY:
#   schtasks /Query /TN "QAlpha Strategy Lab" /V /FO LIST
#   schtasks /Run /TN "QAlpha Strategy Lab"
# -----------------------------------------------------------------------------
