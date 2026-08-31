# =============================================================================
# Q-ALPHA - candidates/register_candidate_tasks.ps1
# Register candidate Task Scheduler jobs (Approval Runner + Autonomous Agent + TWS Sync).
#
# Aaron runs this when tasks drift or after a machine rebuild / path move.
# Agents do NOT auto-create schtasks.
#
# Usage (from repo root):
#   .\candidates\register_candidate_tasks.ps1
#
# If "QAlpha Approval Runner" returns Access is denied, re-run PowerShell
# as Administrator (that task was created with elevated permissions).
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

schtasks /Create /F /TN "QAlpha Approval Runner" /TR $trApproval /SC DAILY /ST 09:25 /RL LIMITED
$results["QAlpha Approval Runner"] = ($LASTEXITCODE -eq 0)

schtasks /Create /F /TN "QAlpha Autonomous Agent" /TR $trAgent /SC DAILY /ST 09:20 /RL LIMITED
$results["QAlpha Autonomous Agent"] = ($LASTEXITCODE -eq 0)

schtasks /Create /F /TN "QAlpha Live TWS Sync" /TR $trTws /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 09:40 /RI 30 /DU 06:30 /RL LIMITED
$results["QAlpha Live TWS Sync"] = ($LASTEXITCODE -eq 0)

Write-Host ""
Write-Host "Results:"
foreach ($name in @("QAlpha Approval Runner", "QAlpha Autonomous Agent", "QAlpha Live TWS Sync")) {
    $status = if ($results[$name]) { "OK" } else { "FAILED" }
    Write-Host "  $name : $status"
}

if ($results.Values -contains $false) {
    Write-Host ""
    if (-not $results["QAlpha Approval Runner"]) {
        if ($isAdmin) {
            Write-Host "Approval Runner still failed even as Administrator."
            Write-Host "Try: .\candidates\register_approval_runner_admin.ps1"
        } else {
            Write-Host "Approval Runner is locked (legacy OneDrive task). Open PowerShell as Administrator and run:"
            Write-Host "  cd C:\Users\ajkle\Documents\Q-ALPHA"
            Write-Host "  .\candidates\register_approval_runner_admin.ps1"
        }
    }
    exit 1
}

Write-Host ""
Write-Host "Verify:"
Write-Host "  schtasks /Query /TN `"QAlpha Approval Runner`" /XML"
Write-Host "  schtasks /Query /TN `"QAlpha Autonomous Agent`" /XML"
Write-Host "  schtasks /Query /TN `"QAlpha Live TWS Sync`" /XML"
