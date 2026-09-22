# EXP-0027 — one laptop command for the expanded-universe ITM call proxy.
# Research only. No orders.
# Uses C:\Users\ajkle\Documents\Track 100\results\universe_expand_movers_trades.*
# Starts the read-only options bridge when port 8787 is down (same as the
# other EXP-0027 proxy). IV falls back to 10-day realized vol if it stays down.
# Stock bars come from POLYGON_API_KEY or the Modal secret polygon-api-key.

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $Root

$Track = "C:\Users\ajkle\Documents\Track 100"
$env:TRACK100_ROOT = $Track
$Results = Join-Path $Track "results"
$env:TRACK100_TRADES = $Results
$env:TRACK100_BOOK = "expanded"

$Json = Join-Path $Results "universe_expand_movers_trades.json"
$Csv = Join-Path $Results "universe_expand_movers_trades.csv"
if (-not (Test-Path $Json) -and -not (Test-Path $Csv)) {
    Write-Host "Missing universe_expand_movers_trades.json and .csv under $Results"
    Write-Host "The proxy will write NOT_RUN and will not invent P&L."
    Write-Host "Re-run the expand study in Track 100 on the branch that has the universe-expand code (cursor/universe-expand-movers-3067):"
    Write-Host "  cd `"$Track`""
    Write-Host "  py -3 -m modal run cloud/universe_expand_modal.py"
    Write-Host "Then run this script again."
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

& $Py (Join-Path $Root "experiments\EXP-0027\study_expanded_book_itm_bs_proxy.py") --book expanded
exit $LASTEXITCODE
