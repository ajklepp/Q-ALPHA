# =============================================================================
# Q-ALPHA - strategy_lab/register_lab_tasks.ps1
# Register Strategy Lab Task Scheduler jobs (Entry + Mark + Settle).
# NO 16:40 settle backup - duplicate Telegram on same --settle run.
#
# Aaron runs this when tasks drift or after a machine rebuild.
# Agents do NOT auto-create schtasks.
#
# Usage (from repo root):
#   .\strategy_lab\register_lab_tasks.ps1
# =============================================================================
$ErrorActionPreference = "Stop"

$LabDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $LabDir

$EntryPs1 = Join-Path $LabDir "start_lab_scheduled.ps1"
$MarkPs1 = Join-Path $LabDir "start_lab_mark_scheduled.ps1"
$SettlePs1 = Join-Path $LabDir "start_lab_settle_scheduled.ps1"

foreach ($p in @($EntryPs1, $MarkPs1, $SettlePs1)) {
    if (-not (Test-Path -LiteralPath $p)) {
        Write-Error "Missing launcher: $p"
    }
}

# Remove duplicate settle backup if present (Issue 1 - Aug 2026).
try {
    & schtasks /Delete /TN "QAlpha Strategy Lab Settle Backup" /F 2>&1 | Out-Null
} catch {
    # Already deleted or never existed.
}
Write-Host "Ensured QAlpha Strategy Lab Settle Backup is removed."

# Unquoted -File paths (Mark/Settle pattern) - inner quotes break 9:35 entry launch.
$trEntry = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File $EntryPs1"
$trMark = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File $MarkPs1"
$trSettle = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SettlePs1"

schtasks /Create /F /TN "QAlpha Strategy Lab" /TR $trEntry /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 09:35 /RL LIMITED
schtasks /Create /F /TN "QAlpha Strategy Lab Mark" /TR $trMark /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 10:00 /RI 30 /DU 06:00 /RL LIMITED
schtasks /Create /F /TN "QAlpha Strategy Lab Settle" /TR $trSettle /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 16:20 /RL LIMITED

Write-Host ""
Write-Host "Registered:"
Write-Host "  QAlpha Strategy Lab        Mon-Fri 09:35 ET  (entry)"
Write-Host "  QAlpha Strategy Lab Mark   Mon-Fri 10:00-16:00 every 30m"
Write-Host "  QAlpha Strategy Lab Settle Mon-Fri 16:20 ET  (primary EOD only)"
Write-Host ""
Write-Host "Verify:"
Write-Host "  schtasks /Query /TN `"QAlpha Strategy Lab`" /V /FO LIST"
Write-Host "  schtasks /Query /TN `"QAlpha Strategy Lab Mark`" /FO LIST"
Write-Host "  schtasks /Query /TN `"QAlpha Strategy Lab Settle`" /FO LIST"
