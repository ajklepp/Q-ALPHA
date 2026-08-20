# =============================================================================
# Q-ALPHA — stop_dashboard.ps1
# Kill whatever is listening on the Streamlit port (default 8501).
#
# Usage:
#   .\stop_dashboard.ps1
#   .\stop_dashboard.ps1 -Port 8501
# =============================================================================
param(
    [int]$Port = 8501
)

$conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $conns) {
    Write-Host "Nothing listening on port $Port — dashboard already stopped."
    exit 0
}

$pids = $conns.OwningProcess | Sort-Object -Unique
foreach ($procId in $pids) {
    try {
        $p = Get-Process -Id $procId -ErrorAction Stop
        Write-Host "Stopping PID $procId ($($p.ProcessName)) on port $Port..."
        Stop-Process -Id $procId -Force
    } catch {
        Write-Host "PID $procId already gone."
    }
}

Start-Sleep -Milliseconds 500
$still = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($still) {
    Write-Host "WARNING: port $Port still in use by PID(s): $($still.OwningProcess -join ', ')"
    exit 1
}
Write-Host "Dashboard stopped. Port $Port is free."
