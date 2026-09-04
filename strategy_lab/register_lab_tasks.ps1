# =============================================================================
# Q-ALPHA - strategy_lab/register_lab_tasks.ps1
# Gap Strategy Lab SIM is MOTHBALLED (2026-09-04).
# Peak Hour Performers is Live Paper — do not re-enable these unless researching gaps.
#
# This script creates then DISABLES Entry / Mark / Settle so rebuilds stay off.
# Usage (from repo root):
#   .\strategy_lab\register_lab_tasks.ps1
# =============================================================================
$ErrorActionPreference = "Continue"

$LabDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $LabDir

$EntryPs1 = Join-Path $LabDir "start_lab_scheduled.ps1"
$MarkPs1 = Join-Path $LabDir "start_lab_mark_scheduled.ps1"
$SettlePs1 = Join-Path $LabDir "start_lab_settle_scheduled.ps1"

foreach ($p in @($EntryPs1, $MarkPs1, $SettlePs1)) {
    if (-not (Test-Path -LiteralPath $p)) {
        Write-Error "Missing launcher: $p"
        exit 1
    }
}

try {
    & schtasks /Delete /TN "QAlpha Strategy Lab Settle Backup" /F 2>&1 | Out-Null
} catch {}

$trEntry = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File $EntryPs1"
$trMark = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File $MarkPs1"
$trSettle = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SettlePs1"

schtasks /Create /F /TN "QAlpha Strategy Lab" /TR $trEntry /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 09:35 /RL LIMITED
schtasks /Create /F /TN "QAlpha Strategy Lab Mark" /TR $trMark /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 10:00 /RI 30 /DU 06:00 /RL LIMITED
schtasks /Create /F /TN "QAlpha Strategy Lab Settle" /TR $trSettle /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 16:20 /RL LIMITED

schtasks /Change /TN "QAlpha Strategy Lab" /DISABLE 2>$null
schtasks /Change /TN "QAlpha Strategy Lab Mark" /DISABLE 2>$null
schtasks /Change /TN "QAlpha Strategy Lab Settle" /DISABLE 2>$null

Write-Host ""
Write-Host "POLICY: Strategy Lab gap SIM is MOTHBALLED (Disabled)."
Write-Host "  Live Paper = TSD Scheduler + Trail + Live TWS Sync only."
Write-Host "  Dashboard = Weekly Research tab (Peak Hour funnel / hitch)."
Write-Host ""
Write-Host "Tasks created then DISABLED:"
Write-Host "  QAlpha Strategy Lab / Mark / Settle"
