# =============================================================================
# Q-ALPHA - candidates/register_tsd_tasks.ps1
# Register TSD pipeline Task Scheduler jobs (scheduler tick + trail monitor +
# Friday weekly reports).
#
# Aaron runs this when tasks drift or after a machine rebuild / path move.
# Agents do NOT auto-create schtasks.
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

# Scheduler tick every 5 min, 24h duration, all days (extended-hours 3H bars)
schtasks /Create /F /TN "QAlpha TSD Scheduler" /TR $trSched /SC WEEKLY /D MON,TUE,WED,THU,FRI,SAT,SUN /ST 00:00 /RI 5 /DU 24:00 /RL LIMITED
$results["QAlpha TSD Scheduler"] = ($LASTEXITCODE -eq 0)

# Trail monitor loop — restart daily at 04:00 ET, run 24h (TWS must be open)
schtasks /Create /F /TN "QAlpha TSD Trail Monitor" /TR $trTrail /SC DAILY /ST 04:00 /RL LIMITED
$results["QAlpha TSD Trail Monitor"] = ($LASTEXITCODE -eq 0)

# Weekly scorecard + options study — Friday 5:00 PM local (= ET on this PC)
schtasks /Create /F /TN "QAlpha TSD Weekly Reports" /TR $trWeekly /SC WEEKLY /D FRI /ST 17:00 /RL LIMITED
$results["QAlpha TSD Weekly Reports"] = ($LASTEXITCODE -eq 0)

Write-Host ""
Write-Host "Results:"
foreach ($name in @("QAlpha TSD Scheduler", "QAlpha TSD Trail Monitor", "QAlpha TSD Weekly Reports")) {
    $status = if ($results[$name]) { "OK" } else { "FAILED" }
    Write-Host "  $name : $status"
}

if ($results.Values -contains $false) {
    exit 1
}

Write-Host ""
Write-Host "Verify:"
Write-Host "  schtasks /Query /TN `"QAlpha TSD Scheduler`" /XML"
Write-Host "  schtasks /Query /TN `"QAlpha TSD Trail Monitor`" /XML"
Write-Host "  schtasks /Query /TN `"QAlpha TSD Weekly Reports`" /V /FO LIST"
Write-Host ""
Write-Host "Logs: candidates\logs\tsd_scheduler_YYYY-MM-DD.log"
Write-Host "      candidates\logs\tsd_trail_YYYY-MM-DD.log"
Write-Host "      candidates\logs\tsd_weekly_reports_YYYY-MM-DD.log"
Write-Host ""
Write-Host "Weekly outputs: candidates\tsd_scan_pipeline\results\scorecard_*.md"
Write-Host "                candidates\tsd_scan_pipeline\results\options_study_*.md"
