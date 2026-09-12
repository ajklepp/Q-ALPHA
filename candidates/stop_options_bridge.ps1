# =============================================================================
# Q-ALPHA — candidates/stop_options_bridge.ps1
# Stop the local READ-ONLY options data bridge started by start_options_bridge.ps1.
#
# Prefers the PID file under candidates/logs/options_bridge.pid, then
# falls back to whatever is listening on 127.0.0.1:<Port> (default 8787).
# =============================================================================
param(
    [int]$Port = 8787
)

$ErrorActionPreference = "Continue"
$CandDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogDir = Join-Path $CandDir "logs"
$PidFile = Join-Path $LogDir "options_bridge.pid"

function Stop-PidSafe([int]$ProcId) {
    if ($ProcId -le 0) { return }
    try {
        $p = Get-Process -Id $ProcId -ErrorAction Stop
        Write-Host "Stopping PID $ProcId ($($p.ProcessName))..."
        Stop-Process -Id $ProcId -Force
    } catch {
        Write-Host "PID $ProcId already gone."
    }
}

$stopped = $false
if (Test-Path -LiteralPath $PidFile) {
    $raw = (Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    $oldPid = 0
    try { $oldPid = [int]("$raw".Trim()) } catch { $oldPid = 0 }
    if ($oldPid -gt 0) {
        Stop-PidSafe $oldPid
        $stopped = $true
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

try {
    $conns = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
} catch {
    $conns = $null
}
if ($conns) {
    $listenPids = $conns.OwningProcess | Sort-Object -Unique
    foreach ($procId in $listenPids) {
        Stop-PidSafe ([int]$procId)
        $stopped = $true
    }
}

Start-Sleep -Milliseconds 400
$still = $null
try {
    $still = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
} catch {
    $still = $null
}
if ($still) {
    $stillPids = ($still.OwningProcess | Sort-Object -Unique) -join ", "
    Write-Host "WARNING: 127.0.0.1:$Port still in use by PID(s): $stillPids"
    exit 1
}

if ($stopped) {
    Write-Host "options_bridge stopped. 127.0.0.1:$Port is free."
} else {
    Write-Host "options_bridge was not running (no PID file / nothing on 127.0.0.1:$Port)."
}
exit 0
