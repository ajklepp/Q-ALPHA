# =============================================================================
# Q-ALPHA - candidates/register_tsd_tasks.ps1
# Register Peak Hour Live Paper Task Scheduler jobs.
#
# LIVE PAPER (KEEP):
#   QAlpha TSD Scheduler      — --tick --live → 1H LAUNCH @ :15
#   QAlpha TSD Trail Monitor  — kill / BE / trail
#   QAlpha TSD Weekly Reports — optional Friday reports
#
# DISABLED (NOT Live Paper):
#   QAlpha TSD Setup Watch    — legacy 3H confirm/enter bot (second entry authority)
#   Do NOT re-register Setup Watch. Research-only: setup_watch_agent.py
#
# Aaron runs this when tasks drift or after a machine rebuild / path move.
# Agents do NOT auto-create schtasks unless explicitly asked.
#
# Usage (from repo root):
#   .\candidates\register_tsd_tasks.ps1
# =============================================================================
$ErrorActionPreference = "Continue"

$CandDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SchedPs1 = Join-Path $CandDir "start_tsd_scheduler_scheduled.ps1"
$TrailPs1 = Join-Path $CandDir "start_tsd_trail_monitor_scheduled.ps1"
$WeeklyPs1 = Join-Path $CandDir "start_tsd_weekly_reports_scheduled.ps1"

foreach ($p in @($SchedPs1, $TrailPs1, $WeeklyPs1)) {
    if (-not (Test-Path -LiteralPath $p)) {
        Write-Error "Missing: $p"
        exit 1
    }
}

$trSched = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SchedPs1"
$trTrail = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File $TrailPs1"
$trWeekly = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File $WeeklyPs1"

$results = @{}

# Scheduler tick every 5 min — Peak Hour 1H @ :15 is the sole live entry authority
schtasks /Create /F /TN "QAlpha TSD Scheduler" /TR $trSched /SC WEEKLY /D MON,TUE,WED,THU,FRI,SAT,SUN /ST 00:00 /RI 5 /DU 24:00 /RL LIMITED
$results["QAlpha TSD Scheduler"] = ($LASTEXITCODE -eq 0)

# Trail monitor loop — restart daily at 04:00 ET (TWS must be open)
schtasks /Create /F /TN "QAlpha TSD Trail Monitor" /TR $trTrail /SC DAILY /ST 04:00 /RL LIMITED
$results["QAlpha TSD Trail Monitor"] = ($LASTEXITCODE -eq 0)

# Weekly scorecard + options study — Friday 5:00 PM local (= ET on this PC)
schtasks /Create /F /TN "QAlpha TSD Weekly Reports" /TR $trWeekly /SC WEEKLY /D FRI /ST 17:00 /RL LIMITED
$results["QAlpha TSD Weekly Reports"] = ($LASTEXITCODE -eq 0)

# Legacy Setup Watch — disable if present; never re-create as Live Paper
schtasks /Change /TN "QAlpha TSD Setup Watch" /DISABLE 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "  DISABLED legacy: QAlpha TSD Setup Watch"
} else {
    Write-Host "  (no QAlpha TSD Setup Watch task — OK)"
}

Write-Host ""
Write-Host "Results (Peak Hour Live Paper):"
foreach ($name in @("QAlpha TSD Scheduler", "QAlpha TSD Trail Monitor", "QAlpha TSD Weekly Reports")) {
    $status = if ($results[$name]) { "OK" } else { "FAILED" }
    Write-Host "  $name : $status"
}

if ($results.Values -contains $false) {
    exit 1
}

# Laptop paper desk: do not skip ticks when on battery
foreach ($tn in @("QAlpha TSD Scheduler", "QAlpha TSD Trail Monitor", "QAlpha TSD Weekly Reports")) {
    $task = Get-ScheduledTask -TaskName $tn -ErrorAction SilentlyContinue
    if (-not $task) { continue }
    $settings = $task.Settings
    $settings.DisallowStartIfOnBatteries = $false
    $settings.StopIfGoingOnBatteries = $false
    Set-ScheduledTask -TaskName $tn -Settings $settings | Out-Null
    Write-Host "  Battery OK: $tn"
}

Write-Host ""
Write-Host "POLICY: Sole live entry = TSD Scheduler → 1H LAUNCH. Setup Watch / gap agent = off."
Write-Host "Verify:"
Write-Host "  schtasks /Query /TN `"QAlpha TSD Scheduler`" /FO LIST /V"
Write-Host "  schtasks /Query /TN `"QAlpha TSD Trail Monitor`" /FO LIST /V"
Write-Host "  schtasks /Query /TN `"QAlpha Live TWS Sync`" /FO LIST /V"
Write-Host ""
Write-Host "Logs: candidates\logs\tsd_scheduler_YYYY-MM-DD.log"
Write-Host "      candidates\logs\tsd_trail_YYYY-MM-DD.log"
Write-Host "      candidates\logs\tsd_weekly_reports_YYYY-MM-DD.log"
