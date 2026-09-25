# EXP-0027 — one laptop command for the Track 100-fills Black–Scholes proxy.
# Research only. No orders. Uses the ops-stack tape already on disk.
# Starts the read-only options bridge when it is down, then prices those fills.

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $Root

if (-not $env:TRACK100_ROOT) {
    $env:TRACK100_ROOT = "C:\Users\ajkle\Documents\Track 100"
}

$Py = Join-Path $Root "venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    throw "Missing $Py"
}

$healthy = $false
try {
    $resp = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8787/v1/health" -TimeoutSec 3
    if ($resp.StatusCode -eq 200) { $healthy = $true }
} catch {
    $healthy = $false
}

if (-not $healthy) {
    Write-Host "options bridge is down; starting candidates\start_options_bridge.ps1"
    try {
        & (Join-Path $Root "candidates\start_options_bridge.ps1")
        Start-Sleep -Seconds 3
    } catch {
        Write-Host "options bridge did not start. IV falls back to 10-day realized vol."
    }
}

& $Py (Join-Path $Root "experiments\EXP-0027\study_t100_fills_bs_proxy.py")
exit $LASTEXITCODE
