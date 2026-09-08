# =============================================================================
# Q-ALPHA — candidates/start_autonomous_agent_scheduled.ps1
# Autonomous agent launcher for Task Scheduler (daily 9:20 AM ET).
#
# Register (Aaron runs once — agent does NOT create the task):
#   .\candidates\register_candidate_tasks.ps1
# =============================================================================
$ErrorActionPreference = "Continue"

$CandDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $CandDir
Set-Location -LiteralPath $Root

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:PYTHONPATH = $Root

$Python = Join-Path $Root "venv\Scripts\python.exe"
$Runner = Join-Path $CandDir "autonomous_agent.py"

if (-not (Test-Path -LiteralPath $Python)) {
    Write-Error "venv python not found at $Python"
    exit 1
}

& $Python $Runner
exit $LASTEXITCODE
