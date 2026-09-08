# =============================================================================
# Q-ALPHA — candidates/start_approval_runner_scheduled.ps1
# Local approval runner launcher for Task Scheduler (daily 9:25 AM ET).
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
$Runner = Join-Path $CandDir "local_approval_runner.py"

if (-not (Test-Path -LiteralPath $Python)) {
    Write-Error "venv python not found at $Python"
    exit 1
}

& $Python $Runner
exit $LASTEXITCODE
