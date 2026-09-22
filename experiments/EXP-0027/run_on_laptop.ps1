# EXP-0027 — one laptop command for the ITM Black–Scholes proxy.
# Research only. No orders. Starts the read-only options bridge when it is down,
# then runs the study. Polygon comes from POLYGON_API_KEY or the Modal secret
# polygon-api-key. If the bridge stays down, IV falls back to 10-day realized vol.
# After hours, a null underlying last/mid is expected. The study uses the last
# stock-hist close (then the study's last daily close) and call hist/quote marks.

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $Root

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
        Write-Host "options bridge did not start. The study will use realized-vol fallback."
    }
}

& $Py (Join-Path $Root "experiments\EXP-0027\study_itm_bs_proxy.py")
exit $LASTEXITCODE
