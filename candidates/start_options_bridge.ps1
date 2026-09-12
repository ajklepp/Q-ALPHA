# =============================================================================
# Q-ALPHA — candidates/start_options_bridge.ps1
# Local READ-ONLY options data bridge for Best Strategy Finder (Phase 9A).
#
# Binds 127.0.0.1:8787 (never 0.0.0.0). TWS paper clientId 71.
# Requires laptop TWS paper API enabled on 127.0.0.1:7497.
# Does NOT place / modify / cancel orders. No IBKR credentials in this process.
#
# Usage (from repo root):
#   .\candidates\start_options_bridge.ps1
#   .\candidates\start_options_bridge.ps1 -Port 8787
# Stop:
#   .\candidates\stop_options_bridge.ps1
# =============================================================================
param(
    [int]$Port = 8787
)

$ErrorActionPreference = "Stop"
$CandDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $CandDir
Set-Location -LiteralPath $Root

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:PYTHONPATH = $CandDir

$Python = Join-Path $Root "venv\Scripts\python.exe"
$Runner = Join-Path $CandDir "options_bridge\server.py"

if (-not (Test-Path -LiteralPath $Python)) {
    Write-Error "venv python not found at $Python - create/activate repo venv first."
    exit 1
}
if (-not (Test-Path -LiteralPath $Runner)) {
    Write-Error "bridge server not found at $Runner"
    exit 1
}

$LogDir = Join-Path $CandDir "logs"
if (-not (Test-Path -LiteralPath $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

$PidFile = Join-Path $LogDir "options_bridge.pid"
if (Test-Path -LiteralPath $PidFile) {
    $oldPid = 0
    try { $oldPid = [int]((Get-Content -LiteralPath $PidFile -ErrorAction Stop | Select-Object -First 1).Trim()) } catch { $oldPid = 0 }
    if ($oldPid -gt 0) {
        $existing = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
        if ($existing) {
            Write-Host "options_bridge already running (PID $oldPid)"
            Write-Host "URL: http://127.0.0.1:$Port"
            Write-Host "Stop with: .\candidates\stop_options_bridge.ps1"
            exit 0
        }
    }
}

try {
    $listen = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($listen) {
        $pids = ($listen.OwningProcess | Sort-Object -Unique) -join ", "
        Write-Host "Already listening on 127.0.0.1:$Port (PID(s): $pids)"
        Write-Host "Stop with: .\candidates\stop_options_bridge.ps1 -Port $Port"
        exit 0
    }
} catch {
    # Get-NetTCPConnection may be unavailable; PID file is the primary lock.
}

try {
    $etTz = [System.TimeZoneInfo]::FindSystemTimeZoneById("Eastern Standard Time")
    $etNow = [System.TimeZoneInfo]::ConvertTime([datetime]::Now, $etTz)
    $etDate = $etNow.ToString("yyyy-MM-dd")
} catch {
    $etDate = Get-Date -Format "yyyy-MM-dd"
}
$LogFile = Join-Path $LogDir "options_bridge_${etDate}.log"

$argList = @(
    "-u", $Runner,
    "--host", "127.0.0.1",
    "--port", "$Port",
    "--tws-host", "127.0.0.1",
    "--tws-port", "7497",
    "--client-id", "71"
)

# Windows Start-Process cannot redirect stdout and stderr to the same path.
$ErrLog = Join-Path $LogDir "options_bridge_${etDate}.err.log"

$proc = Start-Process -FilePath $Python `
    -ArgumentList $argList `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $LogFile `
    -RedirectStandardError $ErrLog `
    -PassThru

$proc.Id | Set-Content -LiteralPath $PidFile -Encoding ascii
Write-Host "options_bridge started DETACHED (PID $($proc.Id))"
Write-Host "URL: http://127.0.0.1:$Port"
Write-Host "TWS: 127.0.0.1:7497 clientId=71 (READ-ONLY)"
Write-Host "Log: $LogFile"
Write-Host "Stderr: $ErrLog"
Write-Host "PID: $PidFile"
Write-Host "Stop: .\candidates\stop_options_bridge.ps1"
exit 0
