# EXP-0026 research runner. No orders. Does not change Peak Hour or Track 100.
$ErrorActionPreference = "Stop"
$repo = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $repo
$py = Join-Path $repo "venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    throw "Expected laptop venv at $py"
}
Write-Host "Modal secrets required: polygon-api-key (POLYGON_API_KEY), q-alpha-secrets"
& $py -m modal run experiments/EXP-0026/study_itm_long_call_modal.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Read-only bridge probe at 127.0.0.1:8787 (no orders, no P&L)."
& $py experiments/EXP-0026/probe_options_bridge.py
exit $LASTEXITCODE
