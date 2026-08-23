# =============================================================================
# Q-ALPHA — strategy_lab/start_lab_settle_scheduled.ps1
# Unattended SETTLE pass for Strategy Lab (after RTH close).
#
# Register weekdays 16:40 ET (same pattern as start_lab_scheduled.ps1):
#   schtasks /Create ... /ST 16:40 ... -File "...\start_lab_settle_scheduled.ps1"
# =============================================================================
$ErrorActionPreference = "Continue"

$LabDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $LabDir
Set-Location -LiteralPath $Root

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:PYTHONPATH = $Root

$Python = Join-Path $Root "venv\Scripts\python.exe"
$Runner = Join-Path $LabDir "live_forward.py"

if (-not (Test-Path -LiteralPath $Python)) {
    Write-Error "venv python not found at $Python"
    exit 1
}

$LogDir = Join-Path $LabDir "logs"
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
$LogFile = Join-Path $LogDir "lab_${etDate}_settle.log"

$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
Add-Content -LiteralPath $LogFile -Value ""
Add-Content -LiteralPath $LogFile -Value "======== LAB SETTLE START $stamp ========"

& $Python $Runner --settle *>> $LogFile
$exitCode = $LASTEXITCODE
if ($null -eq $exitCode) { $exitCode = 0 }

$endStamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
Add-Content -LiteralPath $LogFile -Value "======== LAB SETTLE END exit=$exitCode $endStamp ========"
exit $exitCode
