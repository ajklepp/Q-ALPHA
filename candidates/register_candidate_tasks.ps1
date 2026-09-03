# =============================================================================
# Q-ALPHA - candidates/register_candidate_tasks.ps1
# Register candidate Task Scheduler jobs (TWS Sync + gap leftovers).
#
# POLICY (2026-09-03): Peak Hour Performers 1H @ :15 is the PRIMARY Live Paper track.
# Gap Autonomous Agent + Approval Runner are NOT live — register then DISABLE.
# Live TWS Sync stays ENABLED (TSD marks/closes/pool + residual gap runoff).
#
#   schtasks /Change /TN "QAlpha Autonomous Agent" /DISABLE
#   schtasks /Change /TN "QAlpha Approval Runner" /DISABLE
# QALPHA_GAP_AGENT_LIVE=0 in .env is a second gate if a gap task is re-enabled.
#
# Aaron runs this when tasks drift or after a machine rebuild / path move.
#
# Usage (from repo root):
#   .\candidates\register_candidate_tasks.ps1
# =============================================================================
$ErrorActionPreference = "Continue"

$CandDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ApprovalPs1 = Join-Path $CandDir "start_approval_runner_scheduled.ps1"
$AgentPs1 = Join-Path $CandDir "start_autonomous_agent_scheduled.ps1"
$TwsPs1 = Join-Path $CandDir "start_tws_intraday_scheduled.ps1"

foreach ($p in @($ApprovalPs1, $AgentPs1, $TwsPs1)) {
    if (-not (Test-Path -LiteralPath $p)) {
        Write-Error "Missing: $p"
        exit 1
    }
}

$trApproval = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ApprovalPs1"
$trAgent = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File $AgentPs1"
$trTws = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File $TwsPs1"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)

$results = @{}

# Gap leftovers — create then DISABLE (not Live Paper)
schtasks /Create /F /TN "QAlpha Approval Runner" /TR $trApproval /SC DAILY /ST 09:25 /RL LIMITED
$results["QAlpha Approval Runner"] = ($LASTEXITCODE -eq 0)
schtasks /Change /TN "QAlpha Approval Runner" /DISABLE 2>$null

schtasks /Create /F /TN "QAlpha Autonomous Agent" /TR $trAgent /SC DAILY /ST 09:20 /RL LIMITED
$results["QAlpha Autonomous Agent"] = ($LASTEXITCODE -eq 0)
schtasks /Change /TN "QAlpha Autonomous Agent" /DISABLE 2>$null

# Peak Hour marks — 07:00 start / 30m / through RTH (matches start_tws_intraday_scheduled.ps1)
schtasks /Create /F /TN "QAlpha Live TWS Sync" /TR $trTws /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 07:00 /RI 30 /DU 09:30 /RL LIMITED
$results["QAlpha Live TWS Sync"] = ($LASTEXITCODE -eq 0)

Write-Host ""
Write-Host "Results:"
foreach ($name in @("QAlpha Approval Runner", "QAlpha Autonomous Agent", "QAlpha Live TWS Sync")) {
    $status = if ($results[$name]) { "OK" } else { "FAILED" }
    Write-Host "  $name : $status"
}
Write-Host "  (Approval Runner + Autonomous Agent forced DISABLED — not Live Paper)"

if ($results.Values -contains $false) {
    Write-Host ""
    if (-not $results["QAlpha Approval Runner"]) {
        if (-not $isAdmin) {
            Write-Host "Approval Runner may need Admin: .\candidates\register_approval_runner_admin.ps1"
        }
    }
    exit 1
}

Write-Host ""
Write-Host "POLICY: Live Paper = TSD Scheduler + Trail + Live TWS Sync only."
Write-Host "Verify:"
Write-Host "  schtasks /Query /TN `"QAlpha Live TWS Sync`" /FO LIST /V"
Write-Host "  schtasks /Query /TN `"QAlpha Autonomous Agent`" /FO LIST /V"
Write-Host "  schtasks /Query /TN `"QAlpha Approval Runner`" /FO LIST /V"
